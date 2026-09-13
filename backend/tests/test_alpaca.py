from datetime import UTC, date, datetime

import httpx
import pytest

from app.fixtures.transport import load_fixture
from app.providers.alpaca.assets import AlpacaAssetsClient
from app.providers.alpaca.market_data import ALPACA_HINTS, AlpacaMarketDataClient
from app.providers.alpaca.normalize import normalize_bar, parse_rfc3339
from app.providers.alpaca.schemas import AlpacaAsset
from app.providers.errors import ErrorKind, ProviderError
from tests.helpers import make_http

NOW = datetime(2026, 9, 15, 2, tzinfo=UTC)


def raw_bar(day: str, **overrides):
    value = {"t": f"{day}T04:00:00Z", "o": 10.0, "h": 10.5, "l": 9.5, "c": 10.2, "v": 120000, "n": 900, "vw": 10.1}
    value.update(overrides)
    return value


def market_client(handler):
    http = make_http("Alpaca Market Data", handler, hints=ALPACA_HINTS)
    return AlpacaMarketDataClient(http, base_url="https://data.alpaca.markets", clock=lambda: NOW)


async def test_bars_pagination_across_symbols_with_explicit_sip_and_raw():
    pages = {
        None: {"bars": {"AAA": [raw_bar("2026-09-10"), raw_bar("2026-09-11")]}, "next_page_token": "p2", "currency": "USD"},
        "p2": {"bars": {"AAA": [raw_bar("2026-09-14")], "BBB": [raw_bar("2026-09-14", vw=None)]}, "next_page_token": None},
    }
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json=pages[request.url.params.get("page_token")])

    result = await market_client(handler).fetch_daily_bars(["bbb", "AAA", "CCC"], date(2026, 9, 10), date(2026, 9, 14))
    assert len(requests) == 2 and result.pages == 2
    for request in requests:
        params = request.url.params
        assert params["feed"] == "sip"
        assert params["adjustment"] == "raw"
        assert params["timeframe"] == "1Day"
        assert params["symbols"] == "AAA,BBB,CCC"
        assert params["start"] == "2026-09-10" and params["end"] == "2026-09-14"
    assert [b.session_date for b in result.histories["AAA"].bars] == [date(2026, 9, 10), date(2026, 9, 11), date(2026, 9, 14)]
    assert result.histories["BBB"].bars[0].vwap is None
    assert result.histories["CCC"].bars == []
    assert "feed=sip" in result.histories["AAA"].source.endpoint


async def test_sip_permission_error_never_falls_back_to_iex():
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(403, json={"message": "subscription does not permit querying recent SIP data"})

    with pytest.raises(ProviderError) as info:
        await market_client(handler).fetch_daily_bars(["AAA"], date(2026, 9, 10), date(2026, 9, 14))
    assert info.value.kind is ErrorKind.PERMISSION
    assert "IEX" in info.value.hint
    assert [r.url.params["feed"] for r in requests] == ["sip"]


def test_only_sip_feed_is_accepted():
    with pytest.raises(ValueError):
        AlpacaMarketDataClient(make_http("x", lambda r: httpx.Response(200)), base_url="https://x", feed="iex")


async def test_invalid_duplicate_and_out_of_range_bars_are_rejected():
    payload = {
        "bars": {
            "AAA": [
                raw_bar("2026-09-11"),
                raw_bar("2026-09-11"),
                raw_bar("2026-09-12", v=-5),
                {"t": "2026-09-14T04:00:00Z", "o": 1.0},
                raw_bar("2026-08-01"),
            ]
        },
        "next_page_token": None,
    }
    result = await market_client(lambda r: httpx.Response(200, json=payload)).fetch_daily_bars(["AAA"], date(2026, 9, 10), date(2026, 9, 14))
    assert len(result.histories["AAA"].bars) == 1
    assert len(result.rejected_bars["AAA"]) == 4


def test_rfc3339_with_nanoseconds_and_new_york_session_date():
    assert parse_rfc3339("2026-09-14T04:00:00.123456789Z") == datetime(2026, 9, 14, 4, 0, 0, 123456, tzinfo=UTC)
    bar = normalize_bar(raw_bar("2026-09-14"))
    assert bar.session_date == date(2026, 9, 14)
    assert isinstance(normalize_bar(raw_bar("2026-09-14", t="not-a-time")), str)


def asset_client(handler):
    http = make_http("Alpaca Trading API", handler)
    return AlpacaAssetsClient(http, base_url="https://paper-api.alpaca.markets", clock=lambda: NOW)


async def test_assets_lookup_found_missing_and_failed():
    assets = {a["symbol"]: a for a in load_fixture("alpaca/assets.json")["assets"]}

    def handler(request):
        symbol = request.url.path.rsplit("/", 1)[-1]
        if symbol == "FAIL":
            return httpx.Response(500, text="err")
        if symbol in assets:
            return httpx.Response(200, json=assets[symbol])
        return httpx.Response(404, json={"message": "asset not found"})

    client = asset_client(handler)
    client._http._retry = client._http._retry.__class__(max_retries=0)
    result = await client.fetch_assets(["AURX", "OTCB", "NOPE", "FAIL"])
    assert result.listings["AURX"].exchange == "NASDAQ"
    assert result.listings["OTCB"].exchange == "OTC"
    assert result.listings["NOPE"] is None
    assert result.errors["FAIL"].kind is ErrorKind.UNAVAILABLE


async def test_assets_auth_failure_is_global():
    with pytest.raises(ProviderError) as info:
        await asset_client(lambda r: httpx.Response(401, json={"message": "unauthorized."})).fetch_assets(["AAA", "BBB"])
    assert info.value.kind is ErrorKind.AUTH


def test_asset_fixtures_match_documented_schema():
    for asset in load_fixture("alpaca/assets.json")["assets"]:
        AlpacaAsset.model_validate(asset)
