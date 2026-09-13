"""Alpaca Trading API asset lookup, used only to verify exchange listing (read-only; no trading)."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from urllib.parse import quote

from app.domain.models import ListingInfo
from app.providers.alpaca.normalize import normalize_asset
from app.providers.errors import ErrorKind, ProviderError
from app.providers.http import ProviderHttpClient

GLOBAL_FAILURES = frozenset({ErrorKind.AUTH, ErrorKind.PERMISSION})


@dataclass
class AssetsFetchResult:
    retrieved_at: datetime
    # None = symbol not found in Alpaca's asset master.
    listings: dict[str, ListingInfo | None] = field(default_factory=dict)
    errors: dict[str, ProviderError] = field(default_factory=dict)


class AlpacaAssetsClient:
    def __init__(
        self,
        http: ProviderHttpClient,
        *,
        base_url: str,
        concurrency: int = 4,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._http = http
        self._base_url = base_url.rstrip("/")
        self._semaphore = asyncio.Semaphore(concurrency)
        self._clock = clock

    async def fetch_assets(self, symbols: list[str]) -> AssetsFetchResult:
        result = AssetsFetchResult(retrieved_at=self._clock())
        unique = sorted({s.strip().upper() for s in symbols if s.strip()})
        outcomes = await asyncio.gather(*(self._fetch_one(s, result.retrieved_at) for s in unique))
        for symbol, outcome in zip(unique, outcomes, strict=True):
            if isinstance(outcome, ProviderError):
                if outcome.kind in GLOBAL_FAILURES:
                    raise outcome
                result.errors[symbol] = outcome
            else:
                result.listings[symbol] = outcome
        return result

    async def _fetch_one(self, symbol: str, retrieved_at: datetime) -> ListingInfo | None | ProviderError:
        async with self._semaphore:
            try:
                payload = await self._http.get_json(f"{self._base_url}/v2/assets/{quote(symbol, safe='')}")
            except ProviderError as exc:
                return None if exc.kind is ErrorKind.NOT_FOUND else exc
            try:
                return normalize_asset(payload, symbol, retrieved_at)
            except ProviderError as exc:
                return exc
