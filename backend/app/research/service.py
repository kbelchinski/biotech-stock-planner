"""Research workflows: watchlist refresh, company research, trade plans, paper trades, performance.

Provider access goes through the same adapters and ProviderBundle as scans. Business rules live in
the pure modules (catalyst_tracking, price_context, trade_plan, performance). Failures never erase
previously stored results.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from typing import Any

from app.config import BpiqAccessTier, Settings
from app.domain.models import CATALYST_TYPE_LABELS, DataMode, Eligibility, ScanRun
from app.domain.research import (
    JournalEntry,
    MonitoringSettings,
    TrackedCatalyst,
    TrackedStatus,
    Trade,
    TradeKind,
    TradePlanInput,
    TradeStatus,
    WatchItem,
)
from app.fixtures.transport import DemoScenario
from app.logging_setup import get_logger
from app.providers.alpaca.market_data import BarsFetchResult
from app.providers.bpiq_mcp.research import McpResearch
from app.providers.sec_edgar.client import SecEdgarClient
from app.providers.errors import ProviderError
from app.providers.factory import ConfigurationError, ProviderBundle
from app.research import catalyst_tracking as tracking
from app.research.insider_enrichment import build_insider_section
from app.research.critique import analysis_snapshot, build_evidence, diff_snapshots
from app.research.performance import (
    PAPER_COST_PCT,
    PAPER_NOTIONAL_USD,
    PAPER_SLIPPAGE_PCT,
    paper_exit_date,
    resolve_trade,
    summarize,
)
from app.research.price_context import compute_price_context
from app.research.trade_plan import calculate_plan
from app.screening.market_calendar import MarketCalendar, new_york_today
from app.storage.repository import ScanRepository
from app.storage.research_repository import ResearchRepository

log = get_logger("research")

CONTEXT_CALENDAR_DAYS = 400
CACHE_TTL_SECONDS = 600
REFRESH_FAILED_KIND = "refresh_failed"


class ResearchService:
    def __init__(
        self,
        *,
        settings: Settings,
        calendar: MarketCalendar,
        scans: ScanRepository,
        repo: ResearchRepository,
        build_providers: Callable[[DataMode, date, DemoScenario | None], ProviderBundle],
        mode: Callable[[], DataMode],
        mcp: McpResearch,
        sec: SecEdgarClient | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._settings = settings
        self._calendar = calendar
        self._scans = scans
        self.repo = repo
        self._build = build_providers
        self._mode = mode
        self.mcp = mcp
        self.sec = sec
        self._clock = clock
        self.refresh_lock = asyncio.Lock()
        self._cache: dict[tuple, tuple[float, Any]] = {}

    @property
    def mode(self) -> DataMode:
        return self._mode()

    def _scenario(self, scenario: DemoScenario | None) -> DemoScenario | None:
        if self.mode is DataMode.LIVE:
            if scenario is not None:
                raise ValueError("Demo scenarios are only available in demo mode.")
            return None
        return scenario or DemoScenario.NORMAL

    def _dates(self) -> tuple[datetime, date, date]:
        now = self._clock()
        return now, new_york_today(now), self._calendar.latest_completed_session(now)

    def _sessions_before(self, day: date, n: int) -> date | None:
        return self._calendar.sessions_before(day, n)

    # ------------------------------------------------------------ watchlist

    def watchlist(self) -> list[dict[str, Any]]:
        _, today, _ = self._dates()
        out = []
        for item in self.repo.list_watch(self.mode):
            cats = self.repo.list_catalysts(self.mode, item.ticker)
            upcoming = [c for c in cats if c.status is TrackedStatus.ACTIVE and c.catalyst_date and c.catalyst_date >= today]
            revisions = self.repo.list_revisions(self.mode, item.ticker)
            out.append(
                {
                    **item.model_dump(mode="json"),
                    "catalyst_count": len(cats),
                    "next_catalyst": upcoming[0].model_dump(mode="json") if upcoming else None,
                    "undated_count": sum(1 for c in cats if c.catalyst_date is None),
                    "not_returned_count": sum(1 for c in cats if c.status is TrackedStatus.NOT_RETURNED),
                    "date_revision_count": sum(1 for r in revisions if r.kind == "date_changed"),
                    "catalysts": [c.model_dump(mode="json") for c in cats],
                }
            )
        return out

    def add_watch(self, ticker: str, note: str | None, name: str | None = None) -> WatchItem:
        ticker = ticker.strip().upper()
        existing = self.repo.get_watch(self.mode, ticker)
        if existing:
            return existing
        item = WatchItem(mode=self.mode, ticker=ticker, name=name, added_at=self._clock(), note=note)
        self.repo.upsert_watch(item)
        return item

    def remove_watch(self, ticker: str) -> bool:
        return self.repo.remove_watch(self.mode, ticker.strip().upper())

    async def refresh_watchlist(
        self,
        tickers: list[str] | None = None,
        *,
        scenario: DemoScenario | None = None,
        progress: Callable[[str], None] = lambda _: None,
    ) -> dict[str, Any]:
        scenario = self._scenario(scenario)
        async with self.refresh_lock:
            now, today, latest = self._dates()
            mode = self.mode
            watched = {w.ticker: w for w in self.repo.list_watch(mode)}
            targets = [t for t in (tickers or sorted(watched)) if t in watched]
            report: dict[str, Any] = {"started_at": now.isoformat(), "mode": mode.value, "tickers": {}, "issues": [], "notifications_created": 0}
            try:
                bundle = self._build(mode, today, scenario)
            except ConfigurationError as exc:
                report["issues"].append("Live mode is not configured: " + " ".join(exc.problems))
                report["finished_at"] = self._clock().isoformat()
                return report
            try:
                for index, ticker in enumerate(targets, 1):
                    progress(f"Refreshing {ticker} ({index}/{len(targets)})")
                    report["tickers"][ticker] = await self._refresh_one(bundle, watched[ticker], today, now)
                progress("Checking reminders")
                reminders = tracking.due_reminders(
                    mode=mode,
                    catalysts=[c for t in targets for c in self.repo.list_catalysts(mode, t)],
                    settings=self.repo.monitoring_settings(mode),
                    today=today,
                    now=now,
                    sessions_before=self._sessions_before,
                )
                report["notifications_created"] += self.repo.add_notifications(reminders)
                progress("Updating forward paper trades")
                report["paper_trades"] = await self._update_paper_trades(bundle, latest, now)
            finally:
                await bundle.aclose()
            report["notifications_created"] += sum(r.get("notifications", 0) for r in report["tickers"].values())
            report["finished_at"] = self._clock().isoformat()
            self.repo.set_value(mode, "last_watch_refresh", report)
            return report

    async def _refresh_one(self, bundle: ProviderBundle, item: WatchItem, today: date, now: datetime) -> dict[str, Any]:
        mode = self.mode
        ticker = item.ticker
        item = item.model_copy(update={"last_refresh_at": now})
        try:
            fetched = await bundle.bpiq.fetch_catalysts_for_ticker(ticker=ticker, today=today)
        except ProviderError as exc:
            message = exc.message + (f" {exc.hint}" if exc.hint else "")
            self.repo.upsert_watch(item.model_copy(update={"last_refresh_error": message}))
            created = self.repo.add_notifications(
                [
                    tracking.make_notification(
                        mode=mode,
                        now=now,
                        ticker=ticker,
                        event_id=None,
                        kind=REFRESH_FAILED_KIND,
                        title=f"{ticker}: refresh failed",
                        body=f"{message} Previously stored catalysts are kept unchanged.",
                        dedupe_key=f"refresh_failed|{ticker}|{today.isoformat()}",
                    )
                ]
            )
            return {"ok": False, "error": message, "notifications": created}

        previous = self.repo.list_catalysts(mode, ticker)
        complete = fetched.complete and not fetched.rejected
        merged = tracking.merge_catalysts(
            mode=mode,
            ticker=ticker,
            previous=previous,
            fetched=fetched.catalysts,
            flags=fetched.flags,
            now=now,
            today=today,
            query_complete=complete,
            covered_until=bundle.bpiq.covered_until(today),
        )
        issues = [] if complete else ["Provider query incomplete or had rejected records; 'no longer returned' checks were skipped."]
        try:
            historical = await bundle.bpiq.fetch_historical_for_ticker(ticker=ticker)
            outcomes = tracking.attach_outcomes(mode=mode, catalysts=merged.catalysts, outcomes=historical.outcomes, now=now)
            merged.catalysts = outcomes.catalysts
            merged.revisions += outcomes.revisions
            merged.notifications += outcomes.notifications
            if historical.truncated:
                issues.append("Historical catalysts truncated after the page limit.")
        except ProviderError as exc:
            issues.append(f"Historical catalysts unavailable: {exc.message}")

        self.repo.save_catalysts(merged.catalysts)
        self.repo.add_revisions(mode, merged.revisions)
        created = self.repo.add_notifications(merged.notifications)
        profile = fetched.companies.get(ticker)
        self.repo.upsert_watch(
            item.model_copy(
                update={"last_refresh_ok_at": now, "last_refresh_error": None, "name": (profile.name if profile else None) or item.name}
            )
        )
        return {
            "ok": True,
            "catalysts": len(merged.catalysts),
            "revisions": len([r for r in merged.revisions if r.kind != "first_seen"]),
            "notifications": created,
            "issues": issues,
        }

    # ------------------------------------------------------------ paper trades

    def record_paper_trades(self, run: ScanRun) -> list[Trade]:
        """Create forward paper trades for qualifying companies. Entry is always after the scan finished."""
        if run.mode is not self.mode or not run.latest_completed_session:
            return []
        open_keys = {
            (t.ticker, t.catalyst_event_id)
            for t in self.repo.list_trades(run.mode, TradeKind.PAPER)
            if t.status in (TradeStatus.PENDING_ENTRY, TradeStatus.OPEN)
        }
        entry = self._calendar.next_session(run.latest_completed_session)
        while entry is not None and self._calendar.session_open(entry) <= run.finished_at:
            entry = self._calendar.next_session(entry)
        created: list[Trade] = []
        for result in run.results:
            if result.eligibility is not Eligibility.QUALIFIES or entry is None:
                continue
            primary = next((e for e in result.catalysts if e.catalyst.event_id == result.primary_catalyst_event_id), None)
            if primary is None or primary.catalyst.catalyst_date is None:
                continue
            key = (result.ticker, primary.catalyst.event_id)
            if key in open_keys:
                continue
            exit_date = paper_exit_date(primary.catalyst.catalyst_date, self._sessions_before)
            if exit_date is None or exit_date < entry:
                continue
            now = self._clock()
            trade = Trade(
                id=uuid.uuid4().hex,
                mode=run.mode,
                kind=TradeKind.PAPER,
                status=TradeStatus.PENDING_ENTRY,
                ticker=result.ticker,
                created_at=now,
                updated_at=now,
                catalyst_event_id=primary.catalyst.event_id,
                catalyst_category=CATALYST_TYPE_LABELS[primary.catalyst.catalyst_type] if primary.catalyst.catalyst_type else "Unclassified",
                source_scan_id=run.id,
                entry_date=entry,
                planned_exit_date=exit_date,
                notional_usd=PAPER_NOTIONAL_USD,
                cost_pct_per_side=PAPER_COST_PCT,
                slippage_pct_per_side=PAPER_SLIPPAGE_PCT,
                thesis=f"Automated forward tracking: qualified in scan {run.id[:8]} (price session {run.latest_completed_session}).",
                history=[f"{now.date()}: created; entry at open of {entry}, exit at close of {exit_date} (last session before {primary.catalyst.catalyst_date})."],
            )
            self.repo.save_trade(trade)
            open_keys.add(key)
            created.append(trade)
        return created

    async def _update_paper_trades(self, bundle: ProviderBundle, latest: date, now: datetime) -> dict[str, Any]:
        mode = self.mode
        active = [t for t in self.repo.list_trades(mode, TradeKind.PAPER) if t.status in (TradeStatus.PENDING_ENTRY, TradeStatus.OPEN)]
        due = [t for t in active if t.entry_date and t.entry_date <= latest]
        if not active:
            return {"updated": 0}
        bars: BarsFetchResult | None = None
        if due:
            start = min(t.entry_date for t in due)  # type: ignore[type-var]
            try:
                bars = await bundle.market_data.fetch_daily_bars(sorted({t.ticker for t in due}), start, latest, adjustment="split")
            except ProviderError as exc:
                return {"updated": 0, "error": f"Bars unavailable; paper trades left unchanged: {exc.message}"}
        tracked = {c.event_id: c for c in self.repo.list_catalysts(mode)}
        updated = 0
        for trade in active:
            history = bars.histories.get(trade.ticker) if bars else None
            by_date = {b.session_date: b for b in history.bars} if history else {}
            cat = tracked.get(trade.catalyst_event_id or "")
            new = resolve_trade(
                trade,
                bars=by_date,
                latest_session=latest,
                now=now,
                catalyst_date=cat.catalyst_date if cat else None,
                sessions_before=self._sessions_before,
            )
            if new.model_dump() != trade.model_dump():
                self.repo.save_trade(new)
                updated += 1
                if new.status is not trade.status:
                    self.repo.add_notifications(
                        [
                            tracking.make_notification(
                                mode=mode,
                                now=now,
                                ticker=new.ticker,
                                event_id=new.catalyst_event_id,
                                kind="paper_trade",
                                title=f"{new.ticker}: paper trade {new.status.value.replace('_', ' ')}",
                                body=new.history[-1] if new.history else "",
                                dedupe_key=f"paper|{new.id}|{new.status.value}",
                            )
                        ]
                    )
        return {"updated": updated}

    # ------------------------------------------------------------ cached provider reads

    async def _cached(self, key: tuple, loader: Callable[[], Any]) -> Any:
        hit = self._cache.get(key)
        if hit and hit[0] > time.monotonic():
            return hit[1]
        value = await loader()
        self._cache[key] = (time.monotonic() + CACHE_TTL_SECONDS, value)
        if len(self._cache) > 200:
            for k in sorted(self._cache, key=lambda k: self._cache[k][0])[:50]:
                self._cache.pop(k, None)
        return value

    # ------------------------------------------------------------ company research

    async def company_research(self, ticker: str, *, scenario: DemoScenario | None = None) -> dict[str, Any]:
        ticker = ticker.strip().upper()
        scenario = self._scenario(scenario)
        now, today, latest = self._dates()
        mode = self.mode
        issues: list[str] = []
        watch = self.repo.get_watch(mode, ticker)
        settings = self.repo.monitoring_settings(mode)

        screening: dict[str, Any] = {"result": None, "scan": None, "note": "Not found in any saved scan for this data mode."}
        history = self._scans.company_history(mode, ticker)
        if history:
            run = self._scans.get(history[0]["scan_id"])
            result = next((r for r in run.results if r.ticker == ticker), None) if run else None
            if run and result:
                screening = {
                    "result": result.model_dump(mode="json"),
                    "scan": {
                        "id": run.id,
                        "mode": run.mode.value,
                        "outcome": run.outcome.value,
                        "scan_date": run.scan_date.isoformat(),
                        "finished_at": run.finished_at.isoformat(),
                        "latest_completed_session": run.latest_completed_session.isoformat() if run.latest_completed_session else None,
                        "rule_version": run.rule_version,
                        "criteria": run.criteria.model_dump(mode="json"),
                    },
                    "note": f"Decisions as of the most recent saved scan that evaluated {ticker}. They are not re-evaluated live.",
                }

        catalysts: dict[str, Any] = {"origin": None, "items": [], "revisions": [], "historical": [], "issues": []}
        price_context = None
        price_context_error = None
        profile = None
        try:
            bundle = self._build(mode, today, scenario)
        except ConfigurationError as exc:
            bundle = None
            issues.append("Live providers are not configured: " + " ".join(exc.problems))

        if bundle is not None:
            try:
                if watch:
                    catalysts["origin"] = "tracked"
                    catalysts["items"] = [c.model_dump(mode="json") for c in self.repo.list_catalysts(mode, ticker)]
                    catalysts["revisions"] = [r.model_dump(mode="json") for r in self.repo.list_revisions(mode, ticker)]
                    catalysts["retrieved_at"] = watch.last_refresh_ok_at.isoformat() if watch.last_refresh_ok_at else None
                    if watch.last_refresh_error:
                        catalysts["issues"].append(f"Last refresh failed: {watch.last_refresh_error}")
                    if not watch.last_refresh_ok_at:
                        catalysts["issues"].append("Watched but not refreshed yet. Refresh the watchlist to load its full catalyst timeline.")
                try:
                    fetched = await self._cached(
                        (mode, scenario, "catalysts", ticker, today),
                        lambda: bundle.bpiq.fetch_catalysts_for_ticker(ticker=ticker, today=today),
                    )
                    profile = fetched.companies.get(ticker)
                    if not watch:
                        catalysts["origin"] = "live_query"
                        catalysts["retrieved_at"] = fetched.retrieved_at.isoformat()
                        items = [tracking.to_tracked(c, mode=mode, now=now, flags=fetched.flags.get(c.event_id)) for c in fetched.catalysts]
                        catalysts["items"] = [c.model_dump(mode="json") for c in items]
                        catalysts["issues"].append("Not watched: this timeline is a one-off query and revisions are not tracked.")
                    covered = bundle.bpiq.covered_until(today)
                    catalysts["covered_until"] = covered.isoformat() if covered else None
                    if covered:
                        catalysts["issues"].append(f"BPIQ Apex trial: catalysts after {covered.isoformat()} are not visible.")
                except ProviderError as exc:
                    catalysts["issues"].append(f"BPIQ catalysts unavailable: {exc.message}")
                try:
                    hist = await self._cached(
                        (mode, scenario, "historical", ticker), lambda: bundle.bpiq.fetch_historical_for_ticker(ticker=ticker)
                    )
                    catalysts["historical"] = [o.model_dump(mode="json") for o in sorted(hist.outcomes, key=lambda o: o.catalyst_date or date.min, reverse=True)]
                except ProviderError as exc:
                    catalysts["issues"].append(f"BPIQ historical catalysts unavailable: {exc.message}")

                first_seen = self._scans.first_seen(mode, ticker)
                start = latest - timedelta(days=CONTEXT_CALENDAR_DAYS)
                if first_seen and first_seen.price_session and first_seen.price_session < start:
                    start = first_seen.price_session
                symbols = sorted({ticker, settings.benchmark_symbol})
                try:
                    bars = await self._cached(
                        (mode, scenario, "bars", tuple(symbols), start, latest),
                        lambda: bundle.market_data.fetch_daily_bars(symbols, start, latest, adjustment="split"),
                    )
                    own = bars.histories.get(ticker)
                    bench = bars.histories.get(settings.benchmark_symbol)
                    own_bars = own.bars if own else []
                    first_close = None
                    if first_seen and first_seen.price_session:
                        first_close = next((b.close for b in own_bars if b.session_date == first_seen.price_session), None)
                    price_context = compute_price_context(
                        ticker=ticker,
                        bars=own_bars,
                        benchmark_symbol=settings.benchmark_symbol,
                        benchmark_bars=bench.bars if bench and bench.bars else None,
                        sessions=self._calendar.sessions_between(start, latest),
                        latest_session=latest,
                        first_seen=first_seen,
                        retrieved_at=bars.retrieved_at,
                        first_seen_close=first_close,
                    ).model_dump(mode="json")
                    price_context["chart_bars"] = [
                        {"session_date": b.session_date.isoformat(), "open": b.open, "high": b.high, "low": b.low, "close": b.close, "volume": b.volume}
                        for b in own_bars[-260:]
                    ]
                except ProviderError as exc:
                    price_context_error = exc.message + (f" {exc.hint}" if exc.hint else "")
            finally:
                await bundle.aclose()

        items = catalysts["items"]
        primary_id = (screening["result"] or {}).get("primary_catalyst_event_id")
        if not any(c["event_id"] == primary_id for c in items):
            upcoming = [c for c in items if c["status"] == "active" and c["catalyst_date"] and c["catalyst_date"] >= today.isoformat()]
            primary_id = upcoming[0]["event_id"] if upcoming else None
        primary = next((c for c in items if c["event_id"] == primary_id), None)
        catalysts["primary_event_id"] = primary_id
        catalysts["earlier_than_primary"] = [
            c["event_id"]
            for c in items
            if primary and primary["catalyst_date"] and c["event_id"] != primary_id and c["status"] == "active"
            and c["catalyst_date"] and today.isoformat() <= c["catalyst_date"] < primary["catalyst_date"]
        ]

        if mode is DataMode.LIVE:
            mcp = await self.mcp.research(ticker, today=today)
        else:
            reason = "BPIQ MCP is not used in demo mode: its tool schemas are unverified, so no fixtures were created."
            mcp = {cap: {"state": "unavailable", "reason": reason, "records": []} for cap in ("financials", "insiders", "funds")}

        runway_criterion = next((c for c in (screening["result"] or {}).get("criteria", []) if c["key"] == "runway"), None)
        research: dict[str, Any] = {
            "ticker": ticker,
            "mode": mode.value,
            "generated_at": now.isoformat(),
            "today": today.isoformat(),
            "latest_completed_session": latest.isoformat(),
            "overview": {
                "name": (profile.name if profile else None) or (screening["result"] or {}).get("name") or (watch.name if watch else None),
                "exchange": (screening["result"] or {}).get("exchange"),
                "market_cap_usd": profile.market_cap_usd if profile else (screening["result"] or {}).get("market_cap_usd"),
                "market_cap_source": profile.source.model_dump(mode="json") if profile else None,
                "provider_last_price": profile.provider_last_price if profile else None,
                "watched": watch is not None,
                "watch": watch.model_dump(mode="json") if watch else None,
            },
            "screening": screening,
            "scan_history": history,
            "catalysts": catalysts,
            "price_context": price_context,
            "price_context_error": price_context_error,
            "financials": {
                "mcp": mcp["financials"],
                "screening_runway": {
                    "status": runway_criterion["status"],
                    "observed": runway_criterion["observed"],
                    "explanation": runway_criterion["explanation"],
                    "is_mock": bool((screening["result"] or {}).get("runway_is_mock")),
                }
                if runway_criterion
                else None,
            },
            "insiders": await build_insider_section(mcp["insiders"], self.sec, ticker, today=today, live=mode is DataMode.LIVE),
            "funds": {"mcp": mcp["funds"], "provider_flags": [{"event_id": c["event_id"], **(c.get("provider_flags") or {})} for c in items]},
            "issues": issues,
        }

        previous = self.repo.latest_analyses(mode, ticker, 1)
        current_snapshot = analysis_snapshot(research)
        research["changes"] = (
            {"previous_saved_at": previous[0]["created_at"], "items": diff_snapshots(previous[0]["payload"]["snapshot"], current_snapshot)}
            if previous
            else None
        )
        research["questions"] = _questions(research, today)
        research["evidence"] = build_evidence(research)
        return research

    def save_analysis(self, research: dict[str, Any], note: str | None) -> dict[str, Any]:
        created = self._clock()
        payload = {"snapshot": analysis_snapshot(research), "note": note, "evidence": research.get("evidence"), "generated_at": research.get("generated_at")}
        analysis_id = self.repo.save_analysis(self.mode, research["ticker"], created, payload)
        return {"id": analysis_id, "created_at": created.isoformat()}

    # ------------------------------------------------------------ plans and trades

    async def plan(self, plan: TradePlanInput, *, scenario: DemoScenario | None = None) -> dict[str, Any]:
        _, today, _ = self._dates()
        output = calculate_plan(plan, today=today)
        catalysts: list[dict[str, Any]] = []
        note = None
        if plan.planned_exit_date:
            tracked = self.repo.list_catalysts(self.mode, plan.ticker) if self.repo.get_watch(self.mode, plan.ticker) else None
            if tracked is None:
                try:
                    research = await self.company_research(plan.ticker, scenario=scenario)
                    tracked = [TrackedCatalyst.model_validate(c) for c in research["catalysts"]["items"]]
                    note = "Company is not watched; catalysts come from a one-off BPIQ query."
                except (ValueError, ProviderError) as exc:
                    tracked = []
                    note = f"Catalysts unavailable: {exc}"
            for c in tracked:
                if c.status is TrackedStatus.NOT_RETURNED:
                    continue
                if c.catalyst_date is None:
                    catalysts.append({**c.model_dump(mode="json"), "relation": "undated — cannot be ruled out before exit"})
                elif today <= c.catalyst_date <= plan.planned_exit_date:
                    catalysts.append({**c.model_dump(mode="json"), "relation": "on or before planned exit"})
        return {"output": output.model_dump(mode="json"), "catalysts_before_exit": catalysts, "catalyst_note": note}

    def save_trade(self, trade: Trade) -> Trade:
        if trade.mode is not self.mode:
            raise ValueError("Trade mode does not match the current data mode.")
        self.repo.save_trade(trade)
        return trade

    def add_journal(self, entry: JournalEntry) -> JournalEntry:
        self.repo.add_journal(entry)
        return entry

    async def performance(self, *, scenario: DemoScenario | None = None) -> dict[str, Any]:
        scenario = self._scenario(scenario)
        _, today, latest = self._dates()
        mode = self.mode
        trades = self.repo.list_trades(mode)
        benchmark = self.repo.monitoring_settings(mode).benchmark_symbol
        issues: list[str] = []
        histories: dict[str, dict[date, Any]] = {}
        relevant = [t for t in trades if t.entry_date and t.kind is not TradeKind.PLANNED]
        if relevant:
            start = min(t.entry_date for t in relevant)  # type: ignore[type-var]
            symbols = sorted({benchmark} | {t.ticker for t in relevant if t.status is TradeStatus.OPEN})
            try:
                bundle = self._build(mode, today, scenario)
                try:
                    bars = await self._cached(
                        (mode, scenario, "perf-bars", tuple(symbols), start, latest),
                        lambda: bundle.market_data.fetch_daily_bars(symbols, start, latest, adjustment="split"),
                    )
                finally:
                    await bundle.aclose()
                histories = {s: {b.session_date: b for b in h.bars} for s, h in bars.histories.items()}
            except (ConfigurationError, ProviderError) as exc:
                issues.append(f"Benchmark and marks unavailable: {getattr(exc, 'message', exc)}")

        def benchmark_return(entry: date, exit_: date) -> float | None:
            series = histories.get(benchmark, {})
            a, b = series.get(entry), series.get(exit_)
            return b.close / a.open - 1 if a and b else None

        def latest_close(ticker: str) -> tuple[date, float] | None:
            series = histories.get(ticker, {})
            if not series:
                return None
            d = max(series)
            return d, series[d].close

        return {
            "mode": mode.value,
            "benchmark": benchmark,
            "summaries": {
                kind.value: summarize(trades, kind, benchmark_return=benchmark_return, latest_close=latest_close).model_dump(mode="json")
                for kind in (TradeKind.ACTUAL, TradeKind.HYPOTHETICAL, TradeKind.PAPER)
            },
            "issues": issues,
        }

    def monitoring_settings(self) -> MonitoringSettings:
        return self.repo.monitoring_settings(self.mode)


def _pct(metric: dict[str, Any] | None) -> str:
    if not metric or metric["status"] != "ok":
        return "unknown" if not metric else f"unknown ({metric.get('detail') or metric['status']})"
    return f"{metric['value']:.2f}×" if metric["unit"] == "ratio" else f"{metric['value'] * 100:+.1f}%"


def _questions(research: dict[str, Any], today: date) -> list[dict[str, Any]]:
    cats = research["catalysts"]
    by_id = {c["event_id"]: c for c in cats["items"]}
    primary = by_id.get(cats.get("primary_event_id") or "")
    ctx = research.get("price_context") or {}
    metrics = {m["key"]: m for m in ctx.get("metrics", [])}
    result = research["screening"]["result"]
    evidence_against = [e for e in research.get("evidence", []) if e["category"] == "against"]

    def q(qid: str, question: str, answer: str, kind: str, known: bool) -> dict[str, Any]:
        return {"id": qid, "question": question, "answer": answer, "kind": kind, "known": known}

    out = []
    if primary:
        label = primary.get("stage_event_label") or " ".join(filter(None, [primary.get("stage_label"), primary.get("event_label")]))
        out.append(q("event", "What event could move the stock?", f"{label} for {primary.get('drug_name') or 'unnamed program'} dated {primary.get('catalyst_date') or 'undated'}.", "fact", True))
        revisions = [r for r in cats.get("revisions", []) if r["event_id"] == primary["event_id"] and r["kind"] == "date_changed"]
        timing = (
            "BPIQ provides a single date; whether it is exact or a guided estimate is not documented. "
            + (f"The date has been revised {len(revisions)} time(s) while tracked." if revisions else "No date revisions observed while tracked." if cats.get("origin") == "tracked" else "Revisions are not tracked (not watched).")
        )
        out.append(q("timing", "How certain is the event timing?", timing, "fact", True))
    else:
        out.append(q("event", "What event could move the stock?", "Unknown: no upcoming dated catalyst from connected sources.", "fact", False))
        out.append(q("timing", "How certain is the event timing?", "Unknown.", "fact", False))
    if result:
        failing = [c["label"] for c in result["criteria"] if c["status"] == "fail"]
        unknown = [c["label"] for c in result["criteria"] if c["status"] == "unknown"]
        answer = f"{result['eligibility'].replace('_', ' ').capitalize()} in scan {research['screening']['scan']['id'][:8]} ({research['screening']['scan']['scan_date']})."
        if failing:
            answer += f" Fails: {', '.join(failing)}."
        if unknown:
            answer += f" Unknown: {', '.join(unknown)}."
        out.append(q("rules", "Does the company meet my screening rules?", answer, "calculation", True))
    else:
        out.append(q("rules", "Does the company meet my screening rules?", "Unknown: not evaluated in any saved scan.", "calculation", False))
    out.append(
        q(
            "moved",
            "Has the price already moved substantially?",
            f"20 sessions {_pct(metrics.get('return_20'))}; 60 sessions {_pct(metrics.get('return_60'))}; vs benchmark 60 sessions {_pct(metrics.get('relative_60'))}; "
            f"from 252-session high {_pct(metrics.get('from_high_252'))}. No threshold is applied.",
            "calculation",
            bool(metrics.get("return_60") and metrics["return_60"]["status"] == "ok"),
        )
    )
    out.append(
        q(
            "activity",
            "Is trading activity increasing?",
            f"Recent 5-session volume vs 60-session baseline: {_pct(metrics.get('volume_ratio'))}.",
            "calculation",
            bool(metrics.get("volume_ratio") and metrics["volume_ratio"]["status"] == "ok"),
        )
    )
    fin = research["financials"]["mcp"]
    out.append(
        q(
            "financial",
            "What financial risks are documented?",
            f"{len(fin['records'])} financial value(s) from BPIQ MCP (unverified mapping)." if fin["state"] == "ok" else f"Unknown / unavailable from connected sources: {fin['reason']}",
            "fact",
            fin["state"] == "ok",
        )
    )
    out.append(
        q(
            "contradicting",
            "What evidence contradicts the opportunity?",
            f"{len(evidence_against)} item(s) listed under Evidence against." if evidence_against else "None found in connected data. This does not mean none exists.",
            "fact",
            True,
        )
    )
    missing = [e["text"] for e in research.get("evidence", []) if e["category"] == "missing_or_stale"]
    out.append(q("missing", "What information is missing?", f"{len(missing)} item(s) listed under Missing or stale." if missing else "No gaps detected in connected data.", "fact", True))
    return out
