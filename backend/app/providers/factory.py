"""Assemble provider adapters for live or demo mode.

Live and demo differ only in credentials and httpx transport. Live mode never loads fixtures
or mock financials; demo mode never uses real credentials.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import date

import httpx

from app.config import BPIQ_RATE_LIMITS_PER_MIN, BpiqAccessTier, Settings
from app.domain.models import DataMode
from app.fixtures.transport import (
    DEMO_ALPACA_KEY_ID,
    DEMO_ALPACA_SECRET,
    DEMO_BPIQ_KEY,
    DEMO_BPIQ_PAGE_SIZE,
    DemoProviderServer,
    DemoScenario,
    load_mock_financials,
)
from app.providers.alpaca.assets import AlpacaAssetsClient
from app.providers.alpaca.market_data import ALPACA_HINTS, AlpacaMarketDataClient
from app.providers.alpaca.normalize import MARKET_DATA_PROVIDER, TRADING_PROVIDER
from app.providers.bpiq.client import BPIQ_HINTS, BpiqClient
from app.providers.bpiq.normalize import PROVIDER as BPIQ_PROVIDER
from app.providers.errors import ErrorKind
from app.providers.financials import FinancialsProvider, MockFinancialsProvider, UnavailableFinancialsProvider
from app.providers.http import ErrorHints, ProviderHttpClient, RateLimiter, RetryPolicy, Sleep
from app.screening.market_calendar import MarketCalendar

ASSETS_HINTS = {
    ErrorKind.AUTH: (
        "Check the Alpaca keys and ALPACA_ACCOUNT_TYPE: paper keys work only with paper-api.alpaca.markets, "
        "live keys only with api.alpaca.markets."
    ),
    ErrorKind.PERMISSION: "This Alpaca account cannot read the assets endpoint.",
    ErrorKind.RATE_LIMITED: "Alpaca Trading API rate limit reached. Retry shortly.",
    ErrorKind.UNAVAILABLE: "Alpaca Trading API is unavailable. Listing could not be verified.",
    ErrorKind.TIMEOUT: "Alpaca Trading API did not respond in time.",
}


class ConfigurationError(Exception):
    def __init__(self, problems: list[str]) -> None:
        super().__init__("; ".join(problems))
        self.problems = problems


@dataclass
class ProviderBundle:
    mode: DataMode
    bpiq: BpiqClient
    market_data: AlpacaMarketDataClient
    assets: AlpacaAssetsClient
    financials: FinancialsProvider
    http_clients: list[ProviderHttpClient] = field(default_factory=list)
    demo_server: DemoProviderServer | None = None

    async def aclose(self) -> None:
        for client in self.http_clients:
            await client.aclose()


def live_configuration_problems(settings: Settings) -> list[str]:
    problems = []
    if not settings.bpiq_configured:
        problems.append("BPIQ_API_KEY is not set.")
    if not settings.alpaca_configured:
        problems.append("ALPACA_API_KEY_ID and ALPACA_API_SECRET_KEY must both be set.")
    return problems


def build_live_providers(
    settings: Settings,
    *,
    sleep: Sleep = asyncio.sleep,
    transports: dict[str, httpx.AsyncBaseTransport] | None = None,
) -> ProviderBundle:
    problems = live_configuration_problems(settings)
    if problems:
        raise ConfigurationError(problems)
    assert settings.bpiq_api_key and settings.alpaca_api_key_id and settings.alpaca_api_secret_key
    transports = transports or {}
    return _assemble(
        mode=DataMode.LIVE,
        settings=settings,
        bpiq_key=settings.bpiq_api_key.get_secret_value().strip(),
        alpaca_key_id=settings.alpaca_api_key_id.get_secret_value().strip(),
        alpaca_secret=settings.alpaca_api_secret_key.get_secret_value().strip(),
        access_tier=settings.bpiq_access_tier,
        bpiq_page_size=settings.bpiq_page_size,
        financials=UnavailableFinancialsProvider(),
        transports=transports,
        sleep=sleep,
    )


def build_demo_providers(
    settings: Settings,
    *,
    today: date,
    calendar: MarketCalendar,
    scenario: DemoScenario = DemoScenario.NORMAL,
    sleep: Sleep = asyncio.sleep,
) -> ProviderBundle:
    server = DemoProviderServer(today=today, scenario=scenario, calendar=calendar)
    bundle = _assemble(
        mode=DataMode.DEMO,
        settings=settings,
        bpiq_key=DEMO_BPIQ_KEY,
        alpaca_key_id=DEMO_ALPACA_KEY_ID,
        alpaca_secret=DEMO_ALPACA_SECRET,
        access_tier=BpiqAccessTier.APEX_TRIAL if scenario is DemoScenario.BPIQ_TRIAL else BpiqAccessTier.APEX_PAID,
        bpiq_page_size=DEMO_BPIQ_PAGE_SIZE,
        financials=MockFinancialsProvider(load_mock_financials()),
        transports={
            "bpiq": httpx.MockTransport(server.bpiq_handler),
            "alpaca_data": httpx.MockTransport(server.alpaca_data_handler),
            "alpaca_trading": httpx.MockTransport(server.alpaca_trading_handler),
        },
        sleep=sleep,
    )
    bundle.demo_server = server
    return bundle


def _assemble(
    *,
    mode: DataMode,
    settings: Settings,
    bpiq_key: str,
    alpaca_key_id: str,
    alpaca_secret: str,
    access_tier: BpiqAccessTier,
    bpiq_page_size: int,
    financials: FinancialsProvider,
    transports: dict[str, httpx.AsyncBaseTransport],
    sleep: Sleep,
) -> ProviderBundle:
    retry = RetryPolicy(max_retries=settings.http_max_retries)

    def http(provider: str, headers: dict[str, str], key: str, limiter: RateLimiter, hints: ErrorHints):
        client = httpx.AsyncClient(
            headers={**headers, "Accept": "application/json"},
            timeout=httpx.Timeout(settings.http_timeout_seconds),
            # Redirects are not followed so credentials are never forwarded to another host.
            follow_redirects=False,
            transport=transports.get(key),
        )
        return ProviderHttpClient(
            provider=provider, client=client, retry=retry, rate_limiter=limiter, hints=hints, sleep=sleep
        )

    alpaca_headers = {"APCA-API-KEY-ID": alpaca_key_id, "APCA-API-SECRET-KEY": alpaca_secret}
    bpiq_http = http(
        BPIQ_PROVIDER,
        {"Authorization": f"Token {bpiq_key}"},
        "bpiq",
        RateLimiter(BPIQ_RATE_LIMITS_PER_MIN[access_tier], 60.0, sleep=sleep),
        BPIQ_HINTS,
    )
    data_http = http(
        MARKET_DATA_PROVIDER,
        alpaca_headers,
        "alpaca_data",
        RateLimiter(settings.alpaca_rate_limit_per_min, 60.0, sleep=sleep),
        ALPACA_HINTS,
    )
    trading_http = http(
        TRADING_PROVIDER,
        alpaca_headers,
        "alpaca_trading",
        RateLimiter(settings.alpaca_rate_limit_per_min, 60.0, sleep=sleep),
        ASSETS_HINTS,
    )
    return ProviderBundle(
        mode=mode,
        bpiq=BpiqClient(
            bpiq_http, base_url=settings.bpiq_base_url, access_tier=access_tier, page_size=bpiq_page_size
        ),
        market_data=AlpacaMarketDataClient(data_http, base_url=settings.alpaca_data_base_url, feed="sip"),
        assets=AlpacaAssetsClient(trading_http, base_url=settings.alpaca_trading_base_url),
        financials=financials,
        http_clients=[bpiq_http, data_http, trading_http],
    )
