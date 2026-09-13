"""Mock HTTP transport serving synthetic provider responses in the documented schemas.

Demo mode swaps only the httpx transport; request construction, authentication headers,
retries, pagination, validation, and normalization are the same code paths as live mode.

Behaviours not stated in provider docs are marked INFERRED below.
"""

from __future__ import annotations

import base64
import copy
import json
import random
import re
import zlib
from datetime import UTC, date, datetime, time, timedelta
from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlencode

import httpx

from app.screening.market_calendar import NEW_YORK, MarketCalendar

FIXTURES_DIR = Path(__file__).resolve().parent

DEMO_BPIQ_KEY = "demo-bpiq-key"
DEMO_ALPACA_KEY_ID = "demo-alpaca-key-id"
DEMO_ALPACA_SECRET = "demo-alpaca-secret"

# Small pages so demo scans exercise multi-page BPIQ and Alpaca pagination.
DEMO_BPIQ_PAGE_SIZE = 6
DEMO_BARS_PAGE_CAP = 100

_DATE_TOKEN = re.compile(r"^\{\{today([+-]\d+)\}\}$")


class DemoScenario(StrEnum):
    NORMAL = "normal"
    BPIQ_RATE_LIMITED = "bpiq_rate_limited"
    BPIQ_UNAVAILABLE = "bpiq_unavailable"
    BPIQ_AUTH_ERROR = "bpiq_auth_error"
    BPIQ_TRIAL = "bpiq_trial"
    BPIQ_MALFORMED_RECORD = "bpiq_malformed_record"
    ALPACA_SIP_FORBIDDEN = "alpaca_sip_forbidden"
    ALPACA_DATA_UNAVAILABLE = "alpaca_data_unavailable"
    ALPACA_PARTIAL_OUTAGE = "alpaca_partial_outage"


DEMO_SCENARIOS: dict[DemoScenario, tuple[str, str]] = {
    DemoScenario.NORMAL: ("Normal", "All providers respond normally."),
    DemoScenario.BPIQ_RATE_LIMITED: ("BPIQ rate limit", "One BPIQ page returns HTTP 429, then succeeds on retry."),
    DemoScenario.BPIQ_UNAVAILABLE: ("BPIQ outage", "BPIQ returns HTTP 503 on every request."),
    DemoScenario.BPIQ_AUTH_ERROR: ("BPIQ invalid key", "BPIQ rejects the API key (HTTP 401)."),
    DemoScenario.BPIQ_TRIAL: ("BPIQ Apex trial", "Account is on an Apex trial (next 30 days only)."),
    DemoScenario.BPIQ_MALFORMED_RECORD: (
        "BPIQ malformed record",
        "One catalyst record violates the documented schema and is skipped.",
    ),
    DemoScenario.ALPACA_SIP_FORBIDDEN: ("Alpaca SIP not permitted", "Alpaca returns HTTP 403 for feed=sip."),
    DemoScenario.ALPACA_DATA_UNAVAILABLE: ("Alpaca data outage", "Alpaca bars return HTTP 503 on every request."),
    DemoScenario.ALPACA_PARTIAL_OUTAGE: (
        "Alpaca partial failure",
        "Asset lookups fail for two symbols; everything else succeeds.",
    ),
}

PARTIAL_OUTAGE_SYMBOLS = frozenset({"SLVR", "QVLT"})


@lru_cache
def load_fixture(relative_path: str) -> Any:
    return json.loads((FIXTURES_DIR / relative_path).read_text(encoding="utf-8"))


def load_mock_financials() -> dict[str, dict[str, Any]]:
    return copy.deepcopy(load_fixture("mock_financials.json")["records"])


def render_catalyst_records(today: date, relative_path: str = "bpiq/catalysts.json") -> list[dict[str, Any]]:
    records = copy.deepcopy(load_fixture(relative_path)["records"])
    for record in records:
        value = record.get("catalyst_date")
        if isinstance(value, str) and (match := _DATE_TOKEN.match(value)):
            record["catalyst_date"] = (today + timedelta(days=int(match.group(1)))).isoformat()
    return records


def _json(status: int, body: Any, headers: dict[str, str] | None = None) -> httpx.Response:
    return httpx.Response(status, json=body, headers=headers)


class DemoProviderServer:
    def __init__(self, *, today: date, scenario: DemoScenario, calendar: MarketCalendar) -> None:
        self.today = today
        self.scenario = scenario
        self.calendar = calendar
        self.catalyst_records = render_catalyst_records(today)
        if scenario is DemoScenario.BPIQ_MALFORMED_RECORD:
            self.catalyst_records += render_catalyst_records(today, "bpiq/catalysts_malformed.json")
        self.bar_profiles: dict[str, dict[str, Any]] = load_fixture("alpaca/bar_profiles.json")["profiles"]
        self.assets = {a["symbol"]: a for a in load_fixture("alpaca/assets.json")["assets"]}
        self.bpiq_requests = 0
        self.bars_requests = 0
        self.bars_feeds: list[str | None] = []

    # ---------------------------------------------------------------- BPIQ

    def bpiq_handler(self, request: httpx.Request) -> httpx.Response:
        if request.headers.get("Authorization") != f"Token {DEMO_BPIQ_KEY}":
            return _json(401, {"detail": "Invalid token."})
        self.bpiq_requests += 1
        if self.scenario is DemoScenario.BPIQ_AUTH_ERROR:
            return _json(401, {"detail": "Invalid token."})
        if self.scenario is DemoScenario.BPIQ_UNAVAILABLE:
            return httpx.Response(503, text="Service Unavailable")
        if self.scenario is DemoScenario.BPIQ_RATE_LIMITED and self.bpiq_requests == 2:
            # INFERRED: BPIQ documents no 429 response; this mirrors a standard throttle reply.
            return _json(429, {"detail": "Request was throttled."}, headers={"Retry-After": "1"})
        if request.url.path != "/api/v1/info/catalysts/":
            return _json(404, {"detail": "Not found."})

        params = request.url.params
        try:
            limit = int(params.get("limit", "30"))
            offset = int(params.get("offset", "0"))
            date_min = _opt_date(params.get("catalyst_date_min"))
            date_max = _opt_date(params.get("catalyst_date_max"))
            cap_min = _opt_float(params.get("market_cap_min"))
            cap_max = _opt_float(params.get("market_cap_max"))
        except ValueError:
            return _json(400, {"detail": "Invalid query params or date range."})

        matching = [
            r for r in self.catalyst_records if _catalyst_matches(r, date_min, date_max, cap_min, cap_max)
        ]
        page = matching[offset : offset + limit]

        def link(new_offset: int) -> str:
            query = dict(params)
            query.update({"limit": str(limit), "offset": str(new_offset)})
            return f"https://api.bpiq.com/api/v1/info/catalysts/?{urlencode(query)}"

        return _json(
            200,
            {
                "count": len(matching),
                "next": link(offset + limit) if offset + limit < len(matching) else None,
                "previous": link(max(0, offset - limit)) if offset > 0 else None,
                "results": page,
            },
        )

    # ---------------------------------------------------------------- Alpaca market data

    def _alpaca_authorized(self, request: httpx.Request) -> bool:
        return (
            request.headers.get("APCA-API-KEY-ID") == DEMO_ALPACA_KEY_ID
            and request.headers.get("APCA-API-SECRET-KEY") == DEMO_ALPACA_SECRET
        )

    def alpaca_data_handler(self, request: httpx.Request) -> httpx.Response:
        # INFERRED error body shape: {"message": "..."}.
        if not self._alpaca_authorized(request):
            return _json(401, {"message": "unauthorized."})
        self.bars_requests += 1
        self.bars_feeds.append(request.url.params.get("feed"))
        if request.url.path != "/v2/stocks/bars":
            return _json(404, {"message": "not found"})
        if self.scenario is DemoScenario.ALPACA_SIP_FORBIDDEN:
            return _json(403, {"message": "subscription does not permit querying recent SIP data"})
        if self.scenario is DemoScenario.ALPACA_DATA_UNAVAILABLE:
            return httpx.Response(503, text="Service Unavailable")

        params = request.url.params
        try:
            symbols = [s for s in params["symbols"].split(",") if s]
            start = date.fromisoformat(params["start"][:10])
            end = date.fromisoformat(params["end"][:10])
            limit = min(int(params.get("limit", "1000")), 10_000, DEMO_BARS_PAGE_CAP)
            offset = _decode_token(params.get("page_token"))
        except (KeyError, ValueError):
            return _json(400, {"message": "invalid request parameters"})
        if params.get("timeframe") != "1Day" or params.get("feed") not in {"sip", "iex"}:
            return _json(400, {"message": "unsupported timeframe or feed"})

        # Sorted by symbol first, then timestamp (documented).
        rows = [(s, bar) for s in sorted(set(symbols)) for bar in self._bars_for(s, start, end)]
        page = rows[offset : offset + limit]
        bars: dict[str, list[dict[str, Any]]] = {}
        for symbol, bar in page:
            bars.setdefault(symbol, []).append(bar)
        next_token = _encode_token(offset + limit) if offset + limit < len(rows) else None
        return _json(200, {"bars": bars, "next_page_token": next_token, "currency": "USD"})

    def _bars_for(self, symbol: str, start: date, end: date) -> list[dict[str, Any]]:
        profile = self.bar_profiles.get(symbol)
        if profile is None:
            return []
        sessions = self.calendar.sessions_between(start, end)
        history = profile.get("history_sessions")
        missing = set(profile.get("missing_session_offsets", []))
        no_vwap = set(profile.get("no_vwap_session_offsets", []))
        bars = []
        for index, session in enumerate(sessions):
            back = len(sessions) - 1 - index
            if (history is not None and back >= history) or back in missing:
                continue
            rng = random.Random(zlib.crc32(f"{symbol}:{session.isoformat()}".encode()))
            close = profile["price"] if back == 0 else profile["price"] * (1 + rng.uniform(-0.05, 0.05))
            open_ = close * (1 + rng.uniform(-0.025, 0.025))
            high = max(open_, close) * (1 + rng.uniform(0.001, 0.03))
            low = min(open_, close) * (1 - rng.uniform(0.001, 0.03))
            vwap = rng.uniform(low, high)
            turnover = profile["avg_weekly_turnover_usd"] / 5 * rng.uniform(0.7, 1.3)
            volume = int(turnover / vwap)
            bar: dict[str, Any] = {
                # INFERRED: daily bar timestamps mark New York midnight, expressed in UTC.
                "t": datetime.combine(session, time.min, tzinfo=NEW_YORK)
                .astimezone(UTC)
                .strftime("%Y-%m-%dT%H:%M:%SZ"),
                "o": round(open_, 4),
                "h": round(high, 4),
                "l": round(low, 4),
                "c": round(close, 4),
                "v": volume,
                "n": max(1, volume // rng.randint(80, 220)),
                "vw": round(vwap, 6),
            }
            if back in no_vwap:
                del bar["vw"]
            bars.append(bar)
        return bars

    # ---------------------------------------------------------------- Alpaca trading (assets)

    def alpaca_trading_handler(self, request: httpx.Request) -> httpx.Response:
        if not self._alpaca_authorized(request):
            return _json(401, {"message": "unauthorized."})
        prefix = "/v2/assets/"
        if not request.url.path.startswith(prefix):
            return _json(404, {"message": "not found"})
        symbol = unquote(request.url.path[len(prefix) :])
        if self.scenario is DemoScenario.ALPACA_PARTIAL_OUTAGE and symbol in PARTIAL_OUTAGE_SYMBOLS:
            return httpx.Response(500, text="Internal Server Error")
        asset = self.assets.get(symbol)
        if asset is None:
            return _json(404, {"message": "asset not found"})
        return _json(200, asset)


def _catalyst_matches(
    record: dict[str, Any],
    date_min: date | None,
    date_max: date | None,
    cap_min: float | None,
    cap_max: float | None,
) -> bool:
    raw_date = record.get("catalyst_date")
    # INFERRED: date filters are inclusive. Whether BPIQ returns null-dated records under a date
    # filter is not documented; they are returned here so the local exclusion path is exercised.
    if raw_date is not None and (date_min or date_max):
        value = date.fromisoformat(raw_date)
        if (date_min and value < date_min) or (date_max and value > date_max):
            return False
    cap = (record.get("company") or {}).get("market_cap")
    if cap_min is not None or cap_max is not None:
        if cap is None:
            return False
        if isinstance(cap, (int, float)) and not isinstance(cap, bool):
            if (cap_min is not None and cap < cap_min) or (cap_max is not None and cap > cap_max):
                return False
    return True


def _opt_date(value: str | None) -> date | None:
    return date.fromisoformat(value) if value else None


def _opt_float(value: str | None) -> float | None:
    return float(value) if value else None


def _encode_token(offset: int) -> str:
    return base64.urlsafe_b64encode(f"demo:{offset}".encode()).decode()


def _decode_token(token: str | None) -> int:
    if not token:
        return 0
    decoded = base64.urlsafe_b64decode(token.encode()).decode()
    if not decoded.startswith("demo:"):
        raise ValueError("bad token")
    return int(decoded.removeprefix("demo:"))
