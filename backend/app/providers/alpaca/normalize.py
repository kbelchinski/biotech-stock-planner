"""Validate Alpaca payloads and convert them into normalized domain models."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from pydantic import ValidationError

from app.domain.models import DailyBar, ListingInfo, SourceRef
from app.providers.alpaca.schemas import AlpacaAsset, AlpacaBar, AlpacaBarsResponse
from app.providers.bpiq.normalize import summarize_validation_error
from app.providers.errors import ErrorKind, ProviderError
from app.screening.market_calendar import NEW_YORK

MARKET_DATA_PROVIDER = "Alpaca Market Data"
TRADING_PROVIDER = "Alpaca Trading API"
BARS_ENDPOINT = "GET /v2/stocks/bars"
ASSET_ENDPOINT = "GET /v2/assets/{symbol}"

_FRACTION = re.compile(r"(\.\d{6})\d+")


def parse_rfc3339(value: str) -> datetime:
    text = _FRACTION.sub(r"\1", value.strip())
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        raise ValueError("timestamp has no timezone")
    return parsed


def parse_bars_page(payload: Any) -> AlpacaBarsResponse:
    try:
        return AlpacaBarsResponse.model_validate(payload)
    except ValidationError as exc:
        raise ProviderError(
            MARKET_DATA_PROVIDER,
            ErrorKind.INVALID_RESPONSE,
            f"Bars response does not match the documented schema: {summarize_validation_error(exc)}",
        ) from None


def normalize_bar(raw: Any) -> DailyBar | str:
    """Return a DailyBar, or a rejection reason."""
    try:
        bar = AlpacaBar.model_validate(raw)
    except ValidationError as exc:
        return f"Schema validation failed: {summarize_validation_error(exc)}"
    try:
        timestamp = parse_rfc3339(bar.t)
    except ValueError:
        return f"Unparseable bar timestamp {bar.t!r}"
    if min(bar.o, bar.h, bar.l, bar.c) <= 0:
        return f"Non-positive price in bar {bar.t}"
    if bar.v < 0:
        return f"Negative volume in bar {bar.t}"
    if bar.vw is not None and bar.vw <= 0:
        return f"Non-positive VWAP in bar {bar.t}"
    return DailyBar(
        # Daily bars are "truncated to the day (in New York)" per the Alpaca FAQ.
        session_date=timestamp.astimezone(NEW_YORK).date(),
        timestamp=timestamp,
        open=float(bar.o),
        high=float(bar.h),
        low=float(bar.l),
        close=float(bar.c),
        volume=float(bar.v),
        trade_count=bar.n,
        vwap=float(bar.vw) if bar.vw is not None else None,
    )


def normalize_asset(payload: Any, requested_symbol: str, retrieved_at: datetime) -> ListingInfo:
    try:
        asset = AlpacaAsset.model_validate(payload)
    except ValidationError as exc:
        raise ProviderError(
            TRADING_PROVIDER,
            ErrorKind.INVALID_RESPONSE,
            f"Asset response for {requested_symbol} does not match the documented schema: "
            f"{summarize_validation_error(exc)}",
        ) from None
    return ListingInfo(
        ticker=requested_symbol,
        exchange=asset.exchange,
        status=asset.status,
        asset_class=asset.asset_class,
        tradable=asset.tradable,
        name=asset.name,
        source=SourceRef(
            provider=TRADING_PROVIDER,
            endpoint=ASSET_ENDPOINT,
            retrieved_at=retrieved_at,
            timestamp_note="Alpaca assets carry no as-of timestamp; the retrieval time is shown instead.",
        ),
    )
