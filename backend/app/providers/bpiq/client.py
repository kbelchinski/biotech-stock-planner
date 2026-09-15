"""BPIQ Apex REST adapter: upcoming catalysts with documented filters and full pagination."""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any
from urllib.parse import urlsplit

from app.config import BPIQ_TRIAL_HORIZON_DAYS, BpiqAccessTier
from app.domain.models import Catalyst, CompanyProfile
from app.domain.research import OutcomeRecord
from app.providers.bpiq.normalize import (
    PROVIDER,
    RejectedRecord,
    normalize_catalyst,
    normalize_historical,
    parse_catalyst_envelope,
    provider_flags,
)
from app.providers.errors import ErrorKind, ProviderError
from app.logging_setup import get_logger
from app.providers.http import ProviderHttpClient

log = get_logger("bpiq")

MAX_PAGES = 200
# Historical context for one company; older records beyond this are reported as truncated.
MAX_HISTORICAL_PAGES = 10

BPIQ_HINTS = {
    ErrorKind.AUTH: "Check BPIQ_API_KEY in .env (sent as 'Authorization: Token <key>').",
    ErrorKind.PERMISSION: (
        "The BPIQ plan for this key does not include the requested endpoint. "
        "Apex includes only /catalysts/ and /historical-catalysts/."
    ),
    ErrorKind.RATE_LIMITED: "BPIQ allows 15 requests/min on paid Apex and 10 on trial. Wait a minute, then rerun.",
    ErrorKind.BAD_REQUEST: "BPIQ rejected the query parameters or date range.",
    ErrorKind.UNAVAILABLE: "BPIQ is unavailable or returned a server error. Try again later.",
    ErrorKind.TIMEOUT: "BPIQ did not respond in time. Try again later.",
}


@dataclass
class CatalystFetchResult:
    retrieved_at: datetime
    query: dict[str, str]
    catalysts: list[Catalyst] = field(default_factory=list)
    companies: dict[str, CompanyProfile] = field(default_factory=dict)
    rejected: list[RejectedRecord] = field(default_factory=list)
    undated_excluded: int = 0
    duplicates: int = 0
    pages: int = 0
    reported_count: int | None = None
    received_records: int = 0
    label_inventory: Counter[tuple[str | None, str | None]] = field(default_factory=Counter)
    flags: dict[str, dict[str, bool | None]] = field(default_factory=dict)

    @property
    def complete(self) -> bool:
        return self.reported_count is None or self.received_records >= self.reported_count


@dataclass
class HistoricalFetchResult:
    retrieved_at: datetime
    outcomes: list[OutcomeRecord] = field(default_factory=list)
    rejected: list[RejectedRecord] = field(default_factory=list)
    pages: int = 0
    reported_count: int | None = None
    truncated: bool = False


class BpiqClient:
    def __init__(
        self,
        http: ProviderHttpClient,
        *,
        base_url: str,
        access_tier: BpiqAccessTier,
        page_size: int = 50,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._http = http
        self._base_url = base_url.rstrip("/")
        self._tier = access_tier
        self._page_size = page_size
        self._clock = clock

    def check_trial_horizon(self, today: date, date_max: date) -> None:
        if self._tier is not BpiqAccessTier.APEX_TRIAL:
            return
        days = (date_max - today).days
        if days > BPIQ_TRIAL_HORIZON_DAYS:
            raise ProviderError(
                PROVIDER,
                ErrorKind.SUBSCRIPTION_LIMIT,
                f"BPIQ Apex trial access covers catalysts in the next {BPIQ_TRIAL_HORIZON_DAYS} days only, "
                f"but this scan needs catalysts through {date_max.isoformat()} ({days} days out). "
                "Results would be incomplete, so the scan was not run.",
                hint="Upgrade to paid Apex (BPIQ_ACCESS_TIER=apex_paid) or choose a window ending within 30 days.",
            )

    async def fetch_upcoming_catalysts(
        self,
        *,
        today: date,
        date_min: date,
        date_max: date,
        market_cap_min: float | None = None,
        market_cap_max: float | None = None,
    ) -> CatalystFetchResult:
        self.check_trial_horizon(today, date_max)

        params: dict[str, str] = {
            "limit": str(self._page_size),
            "offset": "0",
            "catalyst_date_min": date_min.isoformat(),
            "catalyst_date_max": date_max.isoformat(),
        }
        if market_cap_min is not None:
            params["market_cap_min"] = str(int(market_cap_min))
        if market_cap_max is not None:
            params["market_cap_max"] = str(int(market_cap_max))

        result = CatalystFetchResult(retrieved_at=self._clock(), query=dict(params))
        seen_ids: set[int] = set()
        seen_pages: set[str] = set()
        url: str | None = f"{self._base_url}/catalysts/"
        request_params: dict[str, str] | None = params
        log.info("BPIQ query %s", " ".join(f"{k}={v}" for k, v in params.items()))

        while url is not None:
            if result.pages >= MAX_PAGES:
                raise ProviderError(
                    PROVIDER, ErrorKind.INVALID_RESPONSE, f"Pagination exceeded {MAX_PAGES} pages; stopping."
                )
            page_key = url + "?" + "&".join(f"{k}={v}" for k, v in sorted((request_params or {}).items()))
            if page_key in seen_pages:
                raise ProviderError(PROVIDER, ErrorKind.INVALID_RESPONSE, "Pagination loop: `next` repeated a page.")
            seen_pages.add(page_key)

            envelope = parse_catalyst_envelope(await self._http.get_json(url, request_params))
            result.pages += 1
            if result.reported_count is None:
                result.reported_count = envelope.count
                log.info("BPIQ reported count=%s", envelope.count)
            log.info(
                "BPIQ page %s: %s records (running total %s/%s) next=%s",
                result.pages,
                len(envelope.results),
                result.received_records + len(envelope.results),
                result.reported_count,
                "yes" if envelope.next else "no",
            )

            for raw in envelope.results:
                result.received_records += 1
                _log_catalyst_result(raw)
                normalized = normalize_catalyst(raw, result.retrieved_at)
                if isinstance(normalized, RejectedRecord):
                    log.warning("BPIQ skipped record %s: %s", normalized.record_id, normalized.reason)
                    result.rejected.append(normalized)
                    continue
                catalyst = normalized.catalyst
                if catalyst.provider_record_id in seen_ids:
                    result.duplicates += 1
                    continue
                seen_ids.add(catalyst.provider_record_id)
                result.label_inventory[(catalyst.stage_label, catalyst.event_label)] += 1
                if catalyst.catalyst_date is None:
                    # Undated catalysts are excluded from scans by decision (2026-09-13).
                    result.undated_excluded += 1
                    continue
                result.catalysts.append(catalyst)
                result.companies.setdefault(catalyst.ticker, normalized.company)

            url = self._validated_next(envelope.next)
            request_params = None

        return result

    async def fetch_catalysts_for_ticker(self, *, ticker: str, today: date) -> CatalystFetchResult:
        """All upcoming catalysts for one ticker, including undated ones (watchlist tracking).

        `ticker` filtering verified against an authenticated response on 2026-09-14. On Apex trial the
        query is limited to the documented 30-day horizon; `covered_until` tells callers how far it reaches.
        """
        params: dict[str, str] = {"limit": str(self._page_size), "offset": "0", "ticker": ticker}
        if self._tier is BpiqAccessTier.APEX_TRIAL:
            params["catalyst_date_max"] = self.covered_until(today).isoformat()  # type: ignore[union-attr]
        return await self._paginate_catalysts(params, include_undated=True, ticker=ticker)

    def covered_until(self, today: date) -> date | None:
        if self._tier is BpiqAccessTier.APEX_TRIAL:
            return date.fromordinal(today.toordinal() + BPIQ_TRIAL_HORIZON_DAYS)
        return None

    async def fetch_historical_for_ticker(self, *, ticker: str) -> HistoricalFetchResult:
        params: dict[str, str] = {"limit": str(self._page_size), "offset": "0", "ticker": ticker}
        result = HistoricalFetchResult(retrieved_at=self._clock())
        url: str | None = f"{self._base_url}/historical-catalysts/"
        request_params: dict[str, str] | None = params
        seen: set[str] = set()
        while url is not None:
            if result.pages >= MAX_HISTORICAL_PAGES:
                result.truncated = True
                break
            key = url + repr(sorted((request_params or {}).items()))
            if key in seen:
                raise ProviderError(PROVIDER, ErrorKind.INVALID_RESPONSE, "Pagination loop: `next` repeated a page.")
            seen.add(key)
            envelope = parse_catalyst_envelope(await self._http.get_json(url, request_params))
            result.pages += 1
            result.reported_count = envelope.count if result.reported_count is None else result.reported_count
            for raw in envelope.results:
                normalized = normalize_historical(raw, result.retrieved_at)
                if isinstance(normalized, RejectedRecord):
                    result.rejected.append(normalized)
                    continue
                raw_ticker = (raw.get("ticker") or (raw.get("company") or {}).get("ticker") or "").strip().upper()
                if raw_ticker != ticker:
                    result.rejected.append(RejectedRecord(normalized.provider_record_id, f"ticker {raw_ticker!r} does not match {ticker}"))
                    continue
                result.outcomes.append(normalized)
            url = self._validated_next(envelope.next)
            request_params = None
        return result

    async def _paginate_catalysts(
        self, params: dict[str, str], *, include_undated: bool, ticker: str | None = None
    ) -> CatalystFetchResult:
        result = CatalystFetchResult(retrieved_at=self._clock(), query=dict(params))
        seen_ids: set[int] = set()
        seen_pages: set[str] = set()
        url: str | None = f"{self._base_url}/catalysts/"
        request_params: dict[str, str] | None = params
        while url is not None:
            if result.pages >= MAX_PAGES:
                raise ProviderError(PROVIDER, ErrorKind.INVALID_RESPONSE, f"Pagination exceeded {MAX_PAGES} pages; stopping.")
            page_key = url + "?" + "&".join(f"{k}={v}" for k, v in sorted((request_params or {}).items()))
            if page_key in seen_pages:
                raise ProviderError(PROVIDER, ErrorKind.INVALID_RESPONSE, "Pagination loop: `next` repeated a page.")
            seen_pages.add(page_key)
            envelope = parse_catalyst_envelope(await self._http.get_json(url, request_params))
            result.pages += 1
            if result.reported_count is None:
                result.reported_count = envelope.count
            for raw in envelope.results:
                result.received_records += 1
                normalized = normalize_catalyst(raw, result.retrieved_at)
                if isinstance(normalized, RejectedRecord):
                    result.rejected.append(normalized)
                    continue
                catalyst = normalized.catalyst
                if ticker is not None and catalyst.ticker != ticker:
                    result.rejected.append(RejectedRecord(catalyst.provider_record_id, f"ticker {catalyst.ticker} does not match {ticker}"))
                    continue
                if catalyst.provider_record_id in seen_ids:
                    result.duplicates += 1
                    continue
                seen_ids.add(catalyst.provider_record_id)
                result.label_inventory[(catalyst.stage_label, catalyst.event_label)] += 1
                if catalyst.catalyst_date is None and not include_undated:
                    result.undated_excluded += 1
                    continue
                result.catalysts.append(catalyst)
                result.flags[catalyst.event_id] = provider_flags(raw)
                result.companies.setdefault(catalyst.ticker, normalized.company)
            url = self._validated_next(envelope.next)
            request_params = None
        return result

    def _validated_next(self, next_url: str | None) -> str | None:
        """Follow `next` only within the configured API origin, so the key is never sent elsewhere."""
        if not next_url:
            return None
        base = urlsplit(self._base_url)
        candidate = urlsplit(next_url)
        if (candidate.scheme, candidate.netloc) != (base.scheme, base.netloc) or not candidate.path.startswith(
            base.path
        ):
            raise ProviderError(
                PROVIDER,
                ErrorKind.INVALID_RESPONSE,
                "Refusing to follow a pagination link outside the configured BPIQ API origin.",
            )
        return next_url


def _log_catalyst_result(raw: Any) -> None:
    """Print one Apex /catalysts/ result. The payload has no credentials."""
    if not isinstance(raw, dict):
        log.info("BPIQ /catalysts/ result %s", raw)
        return
    company = raw.get("company") if isinstance(raw.get("company"), dict) else {}
    stage = raw.get("stage_event") if isinstance(raw.get("stage_event"), dict) else {}
    log.info(
        "BPIQ /catalysts/ id=%s ticker=%s company_ticker=%s company=%s date=%s stage=%s event=%s drug=%s cap=%s",
        raw.get("id"),
        raw.get("ticker"),
        company.get("ticker"),
        company.get("name"),
        raw.get("catalyst_date"),
        stage.get("stage_label"),
        stage.get("event_label"),
        raw.get("drug_name"),
        company.get("market_cap"),
    )
    log.info("BPIQ /catalysts/ json %s", json.dumps(raw, default=str, ensure_ascii=True))
