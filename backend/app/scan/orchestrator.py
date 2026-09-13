"""Scan orchestration: fetch → normalize → evaluate → persist.

Provider failures are isolated. BPIQ defines the candidate universe, so a BPIQ failure ends the
scan. Alpaca failures leave the affected criteria Unknown and mark the scan incomplete.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta

from app.config import AppMode, Settings
from app.domain.models import (
    LIQUIDITY_WEEKS,
    ClassificationStatus,
    CompanyResult,
    CriterionKey,
    DataMode,
    Eligibility,
    ScanCriteria,
    ScanIssue,
    ScanOutcome,
    ScanRun,
    ScanSummary,
)
from app.fixtures.transport import DemoScenario
from app.providers.alpaca.normalize import MARKET_DATA_PROVIDER, TRADING_PROVIDER
from app.providers.bpiq.normalize import PROVIDER as BPIQ_PROVIDER
from app.providers.errors import ErrorKind, ProviderError
from app.providers.factory import (
    ConfigurationError,
    ProviderBundle,
    build_demo_providers,
    build_live_providers,
)
from app.screening.criteria import CompanyInputs, ScanContext, evaluate_company
from app.screening.market_calendar import MarketCalendar, new_york_today
from app.storage.repository import ScanRepository

ProgressCallback = Callable[[str, int, int], None]
TOTAL_STEPS = 6

SUBSCRIPTION_KINDS = frozenset({ErrorKind.PERMISSION, ErrorKind.SUBSCRIPTION_LIMIT})
CONFIG_KINDS = frozenset({ErrorKind.AUTH, ErrorKind.NOT_CONFIGURED})

ELIGIBILITY_ORDER = {Eligibility.QUALIFIES: 0, Eligibility.INSUFFICIENT_DATA: 1, Eligibility.DOES_NOT_QUALIFY: 2}

DEMO_NOTICE = (
    "Demo data: synthetic provider responses in the documented BPIQ and Alpaca schemas, plus clearly "
    "labelled mock financials. Companies and tickers are fictional."
)
LIVE_RUNWAY_NOTICE = (
    "Cash runway is unavailable from the configured live providers, so every live result is at best "
    "'Insufficient data' while the runway criterion is enabled. Disable it to screen on the remaining criteria."
)


def _noop_progress(message: str, step: int, total: int) -> None:
    return None


class ScanOrchestrator:
    def __init__(
        self,
        *,
        settings: Settings,
        calendar: MarketCalendar,
        repository: ScanRepository,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        provider_factory: Callable[..., ProviderBundle] | None = None,
    ) -> None:
        self._settings = settings
        self._calendar = calendar
        self._repository = repository
        self._clock = clock
        self._provider_factory = provider_factory

    @property
    def mode(self) -> DataMode:
        return DataMode.LIVE if self._settings.app_mode is AppMode.LIVE else DataMode.DEMO

    async def run(
        self,
        criteria: ScanCriteria,
        *,
        demo_scenario: DemoScenario | None = None,
        progress: ProgressCallback = _noop_progress,
    ) -> ScanRun:
        started_at = self._clock()
        mode = self.mode
        if mode is DataMode.LIVE and demo_scenario is not None:
            raise ValueError("Demo scenarios are only available in demo mode.")
        scenario = (demo_scenario or DemoScenario.NORMAL) if mode is DataMode.DEMO else None
        today = new_york_today(started_at)
        latest_session = self._calendar.latest_completed_session(started_at)
        weeks = self._calendar.completed_weeks(latest_session, LIQUIDITY_WEEKS)

        draft = _Draft(
            scan_id=uuid.uuid4().hex,
            mode=mode,
            started_at=started_at,
            today=today,
            latest_session=latest_session,
            criteria=criteria,
            scenario=scenario,
        )
        if mode is DataMode.DEMO:
            draft.notices.append(DEMO_NOTICE)
        elif criteria.runway_enabled:
            draft.notices.append(LIVE_RUNWAY_NOTICE)

        progress("Preparing providers", 1, TOTAL_STEPS)
        try:
            bundle = self._build_providers(mode, today, scenario)
        except ConfigurationError as exc:
            draft.issues.append(
                ScanIssue(
                    severity="error",
                    provider=None,
                    kind=ErrorKind.NOT_CONFIGURED.value,
                    message="Live mode is not configured: " + " ".join(exc.problems),
                    hint="Add the credentials to .env (see .env.example) and restart the backend, "
                    "or set APP_MODE=demo.",
                )
            )
            return self._finish(draft, ScanOutcome.CONFIGURATION_ERROR, [])

        try:
            return await self._run(bundle, draft, weeks, progress)
        finally:
            await bundle.aclose()

    def _build_providers(self, mode: DataMode, today: date, scenario: DemoScenario | None) -> ProviderBundle:
        if self._provider_factory is not None:
            return self._provider_factory(mode=mode, today=today, scenario=scenario)
        if mode is DataMode.LIVE:
            return build_live_providers(self._settings)
        return build_demo_providers(
            self._settings, today=today, calendar=self._calendar, scenario=scenario or DemoScenario.NORMAL
        )

    async def _run(self, bundle: ProviderBundle, draft: _Draft, weeks, progress: ProgressCallback) -> ScanRun:
        c = draft.criteria
        today = draft.today
        date_min = today if c.include_near_term_catalysts else today + timedelta(days=c.catalyst_min_days)
        date_max = today + timedelta(days=c.catalyst_max_days)
        prefilter = c.provider_market_cap_prefilter and c.market_cap_enabled

        progress("Fetching catalysts from BPIQ", 2, TOTAL_STEPS)
        try:
            fetched = await bundle.bpiq.fetch_upcoming_catalysts(
                today=today,
                date_min=date_min,
                date_max=date_max,
                market_cap_min=c.market_cap_min_usd if prefilter else None,
                market_cap_max=c.market_cap_max_usd if prefilter else None,
            )
        except ProviderError as exc:
            draft.issues.append(_issue(exc))
            return self._finish(draft, _fatal_outcome(exc), [])

        draft.summary.catalyst_records = fetched.received_records
        draft.summary.undated_excluded = fetched.undated_excluded
        draft.summary.rejected_records = len(fetched.rejected)
        draft.summary.duplicate_records = fetched.duplicates
        draft.notices.append(
            f"BPIQ query: catalyst_date {date_min.isoformat()} to {date_max.isoformat()}"
            + (
                f", market cap {int(c.market_cap_min_usd)}–{int(c.market_cap_max_usd)} (companies outside "
                "the range were excluded by BPIQ and are not counted)"
                if prefilter
                else ""
            )
            + f"; {fetched.pages} page(s)."
        )
        if fetched.undated_excluded:
            draft.notices.append(
                f"{fetched.undated_excluded} catalyst(s) without a catalyst_date were excluded from the scan."
            )
        if fetched.duplicates:
            draft.notices.append(f"{fetched.duplicates} duplicate catalyst record(s) (same BPIQ id) were ignored.")
        if fetched.rejected:
            reasons = "; ".join(f"id {r.record_id}: {r.reason}" for r in fetched.rejected[:3])
            draft.issues.append(
                ScanIssue(
                    severity="error",
                    provider=BPIQ_PROVIDER,
                    kind=ErrorKind.INVALID_RESPONSE.value,
                    message=f"{len(fetched.rejected)} catalyst record(s) failed validation and were skipped ({reasons}).",
                    hint="Companies in skipped records may be missing from this scan.",
                )
            )
        if not fetched.complete:
            draft.issues.append(
                ScanIssue(
                    severity="error",
                    provider=BPIQ_PROVIDER,
                    kind="incomplete_pagination",
                    message=f"BPIQ reported {fetched.reported_count} records but {fetched.received_records} "
                    "were received.",
                    hint="Records may have changed during pagination. Run the scan again.",
                )
            )

        by_ticker = defaultdict(list)
        for catalyst in fetched.catalysts:
            by_ticker[catalyst.ticker].append(catalyst)
        tickers = sorted(by_ticker)
        unrecognized = sum(
            1 for cat in fetched.catalysts if cat.classification_status is ClassificationStatus.UNRECOGNIZED
        )
        if unrecognized:
            draft.notices.append(
                f"{unrecognized} catalyst(s) have stage/event labels with no classification rule and are "
                "treated as Unknown type."
            )
        if not tickers:
            return self._finish(draft, None, [])

        inputs = {t: CompanyInputs(ticker=t, profile=fetched.companies.get(t), catalysts=by_ticker[t]) for t in tickers}

        progress(f"Fetching SIP daily bars for {len(tickers)} symbols from Alpaca", 3, TOTAL_STEPS)
        try:
            bars = await bundle.market_data.fetch_daily_bars(tickers, weeks[0].week_start, draft.latest_session)
            for ticker in tickers:
                inputs[ticker].price_history = bars.histories.get(ticker)
                if rejected := bars.rejected_bars.get(ticker):
                    inputs[ticker].issues.append(f"{len(rejected)} daily bar(s) rejected: {rejected[0]}")
        except ProviderError as exc:
            draft.issues.append(_issue(exc, tickers))
            for ticker in tickers:
                inputs[ticker].market_data_error = exc.message

        progress("Verifying exchange listings", 4, TOTAL_STEPS)
        if c.listing_enabled:
            try:
                assets = await bundle.assets.fetch_assets(tickers)
                for ticker in tickers:
                    inputs[ticker].listing = assets.listings.get(ticker)
                    if error := assets.errors.get(ticker):
                        inputs[ticker].listing_error = error.message
                if assets.errors:
                    first = next(iter(assets.errors.values()))
                    draft.issues.append(
                        ScanIssue(
                            severity="error",
                            provider=TRADING_PROVIDER,
                            kind=first.kind.value,
                            message=f"Listing lookup failed for {len(assets.errors)} symbol(s): {first.message}",
                            hint=first.hint,
                            tickers=sorted(assets.errors),
                        )
                    )
            except ProviderError as exc:
                draft.issues.append(_issue(exc, tickers))
                for ticker in tickers:
                    inputs[ticker].listing_error = exc.message

        progress("Loading financial data", 5, TOTAL_STEPS)
        financials = await bundle.financials.fetch(tickers, today)
        if draft.mode is DataMode.LIVE and (
            bundle.financials.is_mock or any(s.source.is_mock for s in financials.snapshots.values())
        ):
            raise RuntimeError("Mock financial data must never be used in live mode.")
        for ticker in tickers:
            inputs[ticker].financials = financials.snapshots.get(ticker)
            inputs[ticker].financials_unavailable_reason = financials.unavailable_reason

        progress("Evaluating criteria", 6, TOTAL_STEPS)
        ctx = ScanContext(today=today, criteria=c, latest_session=draft.latest_session, weeks=weeks)
        results = [evaluate_company(inputs[t], ctx) for t in tickers]
        results.sort(key=_result_sort_key)
        return self._finish(draft, None, results)

    def _finish(self, draft: _Draft, outcome: ScanOutcome | None, results: list[CompanyResult]) -> ScanRun:
        summary = draft.summary
        summary.evaluated = len(results)
        summary.qualifying = sum(r.eligibility is Eligibility.QUALIFIES for r in results)
        summary.failed = sum(r.eligibility is Eligibility.DOES_NOT_QUALIFY for r in results)
        summary.insufficient_data = sum(r.eligibility is Eligibility.INSUFFICIENT_DATA for r in results)

        if outcome is None:
            errors = [i for i in draft.issues if i.severity == "error"]
            if any(i.kind in SUBSCRIPTION_KINDS for i in errors):
                outcome = ScanOutcome.SUBSCRIPTION_LIMITATION
            elif errors:
                outcome = ScanOutcome.INCOMPLETE
            elif summary.qualifying:
                outcome = ScanOutcome.SUCCESS
            else:
                outcome = ScanOutcome.NO_MATCHES

        c = draft.criteria
        enabled = {
            CriterionKey.MARKET_CAP: c.market_cap_enabled,
            CriterionKey.PRICE: c.price_enabled,
            CriterionKey.RUNWAY: c.runway_enabled,
            CriterionKey.LIQUIDITY: c.liquidity_enabled,
            CriterionKey.LISTING: c.listing_enabled,
        }
        run = ScanRun(
            id=draft.scan_id,
            mode=draft.mode,
            outcome=outcome,
            started_at=draft.started_at,
            finished_at=self._clock(),
            scan_date=draft.today,
            latest_completed_session=draft.latest_session,
            criteria=c,
            summary=summary,
            results=results,
            issues=draft.issues,
            not_applied_criteria=[k for k, on in enabled.items() if not on],
            notices=draft.notices,
            demo_scenario=draft.scenario.value if draft.scenario else None,
        )
        self._repository.save(run)
        return run


class _Draft:
    def __init__(
        self,
        *,
        scan_id: str,
        mode: DataMode,
        started_at: datetime,
        today: date,
        latest_session: date,
        criteria: ScanCriteria,
        scenario: DemoScenario | None,
    ) -> None:
        self.scan_id = scan_id
        self.mode = mode
        self.started_at = started_at
        self.today = today
        self.latest_session = latest_session
        self.criteria = criteria
        self.scenario = scenario
        self.issues: list[ScanIssue] = []
        self.notices: list[str] = []
        self.summary = ScanSummary()


def _issue(exc: ProviderError, tickers: list[str] | None = None) -> ScanIssue:
    return ScanIssue(
        severity="error",
        provider=exc.provider,
        kind=exc.kind.value,
        message=exc.message + (f" (after {exc.attempts} attempts)" if exc.attempts > 1 else ""),
        hint=exc.hint,
        tickers=tickers or [],
    )


def _fatal_outcome(exc: ProviderError) -> ScanOutcome:
    if exc.kind in SUBSCRIPTION_KINDS:
        return ScanOutcome.SUBSCRIPTION_LIMITATION
    if exc.kind in CONFIG_KINDS:
        return ScanOutcome.CONFIGURATION_ERROR
    return ScanOutcome.PROVIDER_UNAVAILABLE


def _result_sort_key(result: CompanyResult) -> tuple[int, int, str]:
    primary = next(
        (e for e in result.catalysts if e.catalyst.event_id == result.primary_catalyst_event_id), None
    )
    days = primary.days_until if primary and primary.days_until is not None else 10_000
    return (ELIGIBILITY_ORDER[result.eligibility], days, result.ticker)


__all__ = ["ScanOrchestrator", "MARKET_DATA_PROVIDER"]
