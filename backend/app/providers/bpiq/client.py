"""BPIQ Apex REST adapter: upcoming catalysts with documented filters and full pagination."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from urllib.parse import urlsplit

from app.config import BPIQ_TRIAL_HORIZON_DAYS, BpiqAccessTier
from app.domain.models import Catalyst, CompanyProfile
from app.providers.bpiq.normalize import (
    PROVIDER,
    RejectedRecord,
    normalize_catalyst,
    parse_catalyst_envelope,
)
from app.providers.errors import ErrorKind, ProviderError
from app.providers.http import ProviderHttpClient

MAX_PAGES = 200

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

    @property
    def complete(self) -> bool:
        return self.reported_count is None or self.received_records >= self.reported_count


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

            for raw in envelope.results:
                result.received_records += 1
                normalized = normalize_catalyst(raw, result.retrieved_at)
                if isinstance(normalized, RejectedRecord):
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
