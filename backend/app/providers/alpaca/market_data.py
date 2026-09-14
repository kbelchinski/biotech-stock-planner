"""Alpaca historical daily bars adapter (consolidated SIP feed only, raw adjustment)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime

from app.domain.models import DailyBar, PriceHistory, SourceRef
from app.providers.alpaca.normalize import (
    BARS_ENDPOINT,
    MARKET_DATA_PROVIDER,
    normalize_bar,
    parse_bars_page,
)
from app.providers.errors import ErrorKind, ProviderError
from app.logging_setup import get_logger
from app.providers.http import ProviderHttpClient

log = get_logger("alpaca.bars")

SYMBOLS_PER_REQUEST = 100
BARS_PAGE_LIMIT = 10_000  # documented maximum; applies across all symbols in the request
MAX_PAGES_PER_CHUNK = 500
# Raw prices and raw volumes are mutually consistent for dollar turnover (volume × VWAP) and
# give the actual traded close for the price filter.
ADJUSTMENT = "raw"

ALPACA_HINTS = {
    ErrorKind.AUTH: "Check ALPACA_API_KEY_ID and ALPACA_API_SECRET_KEY in .env.",
    ErrorKind.PERMISSION: (
        "This Alpaca account is not permitted to request feed=sip for this range. "
        "The screener does not fall back to IEX-only volume."
    ),
    ErrorKind.RATE_LIMITED: "Alpaca rate limit reached (Basic plan: 200 requests/min). Retry shortly.",
    ErrorKind.BAD_REQUEST: "Alpaca rejected the bar request parameters.",
    ErrorKind.UNAVAILABLE: "Alpaca market data is unavailable. Try again later.",
    ErrorKind.TIMEOUT: "Alpaca market data did not respond in time.",
}


@dataclass
class BarsFetchResult:
    retrieved_at: datetime
    feed: str
    histories: dict[str, PriceHistory] = field(default_factory=dict)
    rejected_bars: dict[str, list[str]] = field(default_factory=dict)
    pages: int = 0


class AlpacaMarketDataClient:
    def __init__(
        self,
        http: ProviderHttpClient,
        *,
        base_url: str,
        feed: str = "sip",
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        if feed != "sip":
            raise ValueError("Only the consolidated SIP feed is supported; IEX-only volume is never used.")
        self._http = http
        self._base_url = base_url.rstrip("/")
        self.feed = feed
        self._clock = clock

    async def fetch_daily_bars(self, symbols: list[str], start: date, end: date) -> BarsFetchResult:
        unique = sorted({s.strip().upper() for s in symbols if s.strip()})
        result = BarsFetchResult(retrieved_at=self._clock(), feed=self.feed)
        collected: dict[str, dict[date, DailyBar]] = {s: {} for s in unique}
        log.info("Alpaca bars: %s symbols %s to %s feed=%s", len(unique), start.isoformat(), end.isoformat(), self.feed)

        for offset in range(0, len(unique), SYMBOLS_PER_REQUEST):
            chunk = unique[offset : offset + SYMBOLS_PER_REQUEST]
            log.info("Alpaca bars chunk %s-%s (%s symbols)", offset + 1, offset + len(chunk), len(chunk))
            await self._fetch_chunk(chunk, start, end, collected, result)

        for symbol in unique:
            bars = sorted(collected[symbol].values(), key=lambda b: b.session_date)
            result.histories[symbol] = PriceHistory(
                ticker=symbol,
                feed=self.feed,
                adjustment=ADJUSTMENT,
                requested_start=start,
                requested_end=end,
                bars=bars,
                source=SourceRef(
                    provider=f"{MARKET_DATA_PROVIDER} ({self.feed.upper()})",
                    endpoint=f"{BARS_ENDPOINT} · timeframe=1Day · adjustment={ADJUSTMENT} · feed={self.feed}",
                    retrieved_at=result.retrieved_at,
                    source_timestamp=bars[-1].timestamp.isoformat() if bars else None,
                ),
            )
        return result

    async def _fetch_chunk(
        self,
        chunk: list[str],
        start: date,
        end: date,
        collected: dict[str, dict[date, DailyBar]],
        result: BarsFetchResult,
    ) -> None:
        wanted = set(chunk)
        token: str | None = None
        seen_tokens: set[str] = set()
        for _ in range(MAX_PAGES_PER_CHUNK):
            params = {
                "symbols": ",".join(chunk),
                "timeframe": "1Day",
                "start": start.isoformat(),
                "end": end.isoformat(),
                "limit": str(BARS_PAGE_LIMIT),
                "adjustment": ADJUSTMENT,
                "feed": self.feed,
                "sort": "asc",
            }
            if token:
                params["page_token"] = token
            page = parse_bars_page(await self._http.get_json(f"{self._base_url}/v2/stocks/bars", params))
            result.pages += 1
            symbols_on_page = len(page.bars or {})
            bars_on_page = sum(len(v) for v in (page.bars or {}).values())
            log.info(
                "Alpaca bars page %s: %s symbols, %s bars, next_page=%s",
                result.pages,
                symbols_on_page,
                bars_on_page,
                "yes" if page.next_page_token else "no",
            )

            for symbol, raw_bars in (page.bars or {}).items():
                if symbol not in wanted:
                    continue
                for raw in raw_bars:
                    bar = normalize_bar(raw)
                    if isinstance(bar, str):
                        result.rejected_bars.setdefault(symbol, []).append(bar)
                    elif not (start <= bar.session_date <= end):
                        result.rejected_bars.setdefault(symbol, []).append(
                            f"Bar for {bar.session_date} is outside the requested range"
                        )
                    elif bar.session_date in collected[symbol]:
                        result.rejected_bars.setdefault(symbol, []).append(
                            f"Duplicate bar for {bar.session_date}"
                        )
                    else:
                        collected[symbol][bar.session_date] = bar

            token = page.next_page_token
            if not token:
                return
            if token in seen_tokens:
                raise ProviderError(
                    MARKET_DATA_PROVIDER, ErrorKind.INVALID_RESPONSE, "Pagination loop: next_page_token repeated."
                )
            seen_tokens.add(token)
        raise ProviderError(
            MARKET_DATA_PROVIDER, ErrorKind.INVALID_RESPONSE, f"Bars pagination exceeded {MAX_PAGES_PER_CHUNK} pages."
        )
