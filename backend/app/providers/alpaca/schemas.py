"""External response schemas for Alpaca, as documented (reviewed 2026-09-13):

- Historical bars: GET https://data.alpaca.markets/v2/stocks/bars
  https://docs.alpaca.markets/us/reference/stockbars
- Asset by symbol: GET {trading_base}/v2/assets/{symbol_or_asset_id}
  https://docs.alpaca.markets/us/reference/get-v2-assets-symbol_or_asset_id
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictFloat, StrictInt, StrictStr

Number = StrictInt | StrictFloat


class _AlpacaModel(BaseModel):
    model_config = ConfigDict(extra="allow", frozen=True, populate_by_name=True)


class AlpacaBar(_AlpacaModel):
    t: StrictStr  # RFC-3339 timestamp
    o: Number
    h: Number
    l: Number  # noqa: E741 - provider field name
    c: Number
    v: Number  # documented int64
    n: StrictInt | None = None  # trade count
    vw: Number | None = None  # volume-weighted average price


class AlpacaBarsResponse(_AlpacaModel):
    # {symbol: [bar, ...]}; bars validated individually in normalization.
    bars: dict[str, list[dict[str, Any]]] | None = None
    next_page_token: StrictStr | None = None
    currency: StrictStr | None = None


class AlpacaAsset(_AlpacaModel):
    id: StrictStr
    asset_class: StrictStr = Field(alias="class")
    exchange: StrictStr
    symbol: StrictStr
    name: StrictStr | None = None
    status: StrictStr
    tradable: StrictBool
    attributes: list[StrictStr] | None = None
