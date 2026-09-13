from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime
from typing import Any

import httpx

from app.config import Settings
from app.domain.models import DailyBar
from app.providers.http import ProviderHttpClient, RateLimiter, RetryPolicy

# Monday 2026-09-14, 22:00 New York: the Sep 14 session is settled; the Labor Day week
# (Sep 7–11) is holiday-shortened.
SCAN_NOW = datetime(2026, 9, 15, 2, 0, tzinfo=UTC)
SCAN_TODAY = date(2026, 9, 14)


class SleepRecorder:
    def __init__(self) -> None:
        self.calls: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)


def demo_settings(tmp_path, **overrides: Any) -> Settings:
    return Settings(_env_file=None, database_path=tmp_path / "scans.sqlite3", **overrides)


def make_http(
    provider: str,
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    headers: dict[str, str] | None = None,
    max_retries: int = 3,
    sleep: SleepRecorder | None = None,
    limiter: RateLimiter | None = None,
    hints=None,
) -> ProviderHttpClient:
    return ProviderHttpClient(
        provider=provider,
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler), headers=headers or {}),
        retry=RetryPolicy(max_retries=max_retries, jitter=False),
        rate_limiter=limiter,
        hints=hints,
        sleep=sleep or SleepRecorder(),
    )


def bpiq_record(
    record_id: int,
    ticker: str = "AAAA",
    *,
    catalyst_date: str | None = "2026-11-20",
    stage: str = "Phase 3",
    event: str = "Topline data",
    market_cap: Any = 500_000_000,
    **overrides: Any,
) -> dict[str, Any]:
    record = {
        "id": record_id,
        "company": {"ticker": ticker, "name": f"{ticker} Inc.", "last_price": "10.00", "market_cap": market_cap},
        "ticker": ticker,
        "drug_name": f"{ticker}-1",
        "indications": [{"id": 1, "title": "Indication", "nickname": ""}],
        "indications_text": "Indication",
        "stage_event": {"id": 1, "label": f"{stage} {event}", "stage_label": stage, "event_label": event},
        "note": "note",
        "catalyst_date": catalyst_date,
        "has_catalyst": True,
        "catalyst_source": "https://www.example.com/source",
        "is_big_mover": False,
        "is_suspected_mover": False,
        "is_hedge_fund_pick": False,
        "is_hedge_fund_avoid": False,
        "is_high_mgmt_interest": False,
    }
    record.update(overrides)
    return record


def bar(session: date, *, close: float = 10.0, volume: float = 100_000, vwap: float | None = 10.0) -> DailyBar:
    return DailyBar(
        session_date=session,
        timestamp=datetime(session.year, session.month, session.day, 4, 0, tzinfo=UTC),
        open=close,
        high=close,
        low=close,
        close=close,
        volume=volume,
        trade_count=100,
        vwap=vwap,
    )
