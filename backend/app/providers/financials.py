"""Cash-runway inputs.

No approved provider supplies cash or burn under the current plans:
- BPIQ Apex REST: cash/burn fields exist only on /all-companies/ (API Premium only).
- Alpaca: no fundamentals endpoint.

Live mode therefore reports runway as unavailable. Demo mode uses a separate, explicitly
labelled mock dataset that is never attached to BPIQ responses and never used in live mode.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any, Protocol

from app.domain.models import FinancialSnapshot, SourceRef

LIVE_RUNWAY_UNAVAILABLE = (
    "Cash runway is unavailable from the configured providers. BPIQ Apex catalysts contain no cash "
    "or burn fields (those are on /all-companies/, API Premium only) and Alpaca provides no fundamentals."
)
MOCK_PROVIDER_LABEL = "DEMO MOCK FINANCIALS (synthetic — not from any provider)"


@dataclass
class FinancialsResult:
    snapshots: dict[str, FinancialSnapshot] = field(default_factory=dict)
    unavailable_reason: str | None = None


class FinancialsProvider(Protocol):
    is_mock: bool

    async def fetch(self, tickers: list[str], today: date) -> FinancialsResult: ...


class UnavailableFinancialsProvider:
    is_mock = False

    async def fetch(self, tickers: list[str], today: date) -> FinancialsResult:
        return FinancialsResult(unavailable_reason=LIVE_RUNWAY_UNAVAILABLE)


class MockFinancialsProvider:
    """Serves synthetic cash/burn values from app/fixtures/mock_financials.json (demo only)."""

    is_mock = True

    def __init__(self, records: dict[str, dict[str, Any]]) -> None:
        self._records = records

    async def fetch(self, tickers: list[str], today: date) -> FinancialsResult:
        retrieved_at = datetime.now(UTC)
        result = FinancialsResult()
        for ticker in tickers:
            record = self._records.get(ticker)
            if record is None:
                continue
            as_of = today - timedelta(days=int(record["as_of_days_ago"])) if "as_of_days_ago" in record else None
            result.snapshots[ticker] = FinancialSnapshot(
                ticker=ticker,
                cash_usd=record.get("cash_usd"),
                monthly_net_burn_usd=record.get("monthly_net_burn_usd"),
                as_of=as_of,
                source=SourceRef(
                    provider=MOCK_PROVIDER_LABEL,
                    endpoint="app/fixtures/mock_financials.json",
                    retrieved_at=retrieved_at,
                    source_timestamp=as_of.isoformat() if as_of else None,
                    is_mock=True,
                ),
            )
        return result
