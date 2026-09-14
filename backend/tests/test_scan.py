"""End-to-end scans through the real pipeline, with demo fixtures served over mock HTTP."""

import httpx
import pytest

from app.domain.models import CriterionKey, CriterionStatus, DataMode, Eligibility, ScanCriteria, ScanOutcome
from app.fixtures.transport import DEMO_ALPACA_KEY_ID, DEMO_ALPACA_SECRET, DEMO_BPIQ_KEY, DemoProviderServer, DemoScenario
from app.providers.factory import build_demo_providers, build_live_providers
from app.providers.financials import LIVE_RUNWAY_UNAVAILABLE
from app.scan.orchestrator import ScanOrchestrator
from app.screening.market_calendar import MarketCalendar
from app.storage.repository import ScanRepository
from tests.helpers import SCAN_NOW, SleepRecorder, demo_settings

calendar = MarketCalendar()

Q, D, I = Eligibility.QUALIFIES, Eligibility.DOES_NOT_QUALIFY, Eligibility.INSUFFICIENT_DATA
EXPECTED = {
    "AURX": (Q, None),
    "BRVN": (Q, None),
    "VNTA": (Q, None),
    "WSPR": (Q, None),
    "RDGE": (Q, None),
    "TRLM": (Q, None),
    "KSTL": (D, CriterionKey.CATALYST),
    "XNTH": (D, CriterionKey.CATALYST),
    "HLMR": (D, CriterionKey.CATALYST),
    "MRDN": (D, CriterionKey.MARKET_CAP),
    "PNTX": (D, CriterionKey.PRICE),
    "SLVR": (Q, None),
    "QVLT": (D, CriterionKey.LIQUIDITY),
    "OTCB": (D, CriterionKey.LISTING),
    "NOVF": (Q, None),
    "LUMX": (I, CriterionKey.LIQUIDITY),
    "ZEPH": (I, CriterionKey.CATALYST),
}


def demo_orchestrator(tmp_path, sleep=None):
    settings = demo_settings(tmp_path)
    servers: list[DemoProviderServer] = []
    sleep = sleep or SleepRecorder()

    def factory(*, mode, today, scenario):
        bundle = build_demo_providers(settings, today=today, calendar=calendar, scenario=scenario, sleep=sleep)
        servers.append(bundle.demo_server)
        return bundle

    repo = ScanRepository(settings.database_path)
    orch = ScanOrchestrator(settings=settings, calendar=calendar, repository=repo, clock=lambda: SCAN_NOW, provider_factory=factory)
    return orch, servers, repo


async def test_demo_scan_covers_every_scenario(tmp_path):
    orch, servers, _ = demo_orchestrator(tmp_path)
    run = await orch.run(ScanCriteria())
    assert CriterionKey.RUNWAY not in run.not_applied_criteria

    assert run.mode is DataMode.DEMO
    assert run.outcome is ScanOutcome.SUCCESS
    by_ticker = {r.ticker: r for r in run.results}
    assert set(by_ticker) == set(EXPECTED)
    for ticker, (eligibility, key) in EXPECTED.items():
        result = by_ticker[ticker]
        assert result.eligibility is eligibility, ticker
        non_pass = {
            c.key
            for c in result.criteria
            if c.status not in {CriterionStatus.PASS, CriterionStatus.NOT_APPLIED}
        }
        if key is None:
            assert not non_pass, ticker
        elif ticker == "OTCB":
            # OTC names have no SIP bars, so price and liquidity are unknown as well.
            assert next(c.status for c in result.criteria if c.key is key) is CriterionStatus.FAIL
        else:
            assert non_pass == {key}, (ticker, non_pass)

    s = run.summary
    assert (s.evaluated, s.qualifying, s.failed, s.insufficient_data) == (17, 8, 7, 2)
    assert (s.undated_excluded, s.duplicate_records, s.rejected_records) == (1, 1, 0)
    assert len(by_ticker["BRVN"].catalysts) == 3
    assert by_ticker["RDGE"].turnover_method.count("APPROXIMATION") == 1
    assert by_ticker["TRLM"].turnover_is_lower_bound
    assert all(r.runway_is_mock for r in run.results if r.ticker != "NOVF")
    assert servers[0].bpiq_requests == 4  # 21 records at 6 per page
    assert servers[0].bars_requests >= 2  # multi-page bars


async def test_scan_is_persisted_and_previous_success_preserved(tmp_path):
    orch, _, repo = demo_orchestrator(tmp_path)
    ok = await orch.run(ScanCriteria())
    failed = await orch.run(ScanCriteria(), demo_scenario=DemoScenario.BPIQ_UNAVAILABLE)
    assert failed.outcome is ScanOutcome.PROVIDER_UNAVAILABLE
    assert failed.results == []
    assert repo.latest(DataMode.DEMO, successful_only=True).id == ok.id
    assert repo.latest(DataMode.DEMO, successful_only=False).id == failed.id
    assert repo.get(ok.id).criteria == ok.criteria


@pytest.mark.parametrize(
    ("scenario", "outcome"),
    [
        (DemoScenario.BPIQ_AUTH_ERROR, ScanOutcome.CONFIGURATION_ERROR),
        (DemoScenario.BPIQ_TRIAL, ScanOutcome.SUBSCRIPTION_LIMITATION),
        (DemoScenario.BPIQ_MALFORMED_RECORD, ScanOutcome.INCOMPLETE),
        (DemoScenario.BPIQ_RATE_LIMITED, ScanOutcome.SUCCESS),
        (DemoScenario.ALPACA_SIP_FORBIDDEN, ScanOutcome.SUBSCRIPTION_LIMITATION),
        (DemoScenario.ALPACA_DATA_UNAVAILABLE, ScanOutcome.INCOMPLETE),
        (DemoScenario.ALPACA_PARTIAL_OUTAGE, ScanOutcome.INCOMPLETE),
    ],
)
async def test_failure_scenarios(tmp_path, scenario, outcome):
    sleep = SleepRecorder()
    orch, servers, _ = demo_orchestrator(tmp_path, sleep)
    run = await orch.run(ScanCriteria(), demo_scenario=scenario)
    assert run.outcome is outcome
    server = servers[0]
    by_ticker = {r.ticker: r for r in run.results}

    if scenario is DemoScenario.BPIQ_TRIAL:
        assert server.bpiq_requests == 0
        assert "30 days" in run.issues[0].message
    if scenario is DemoScenario.BPIQ_RATE_LIMITED:
        assert 1.0 in sleep.calls
    if scenario is DemoScenario.ALPACA_SIP_FORBIDDEN:
        assert server.bars_feeds == ["sip"]
        assert all(r.eligibility is not Eligibility.QUALIFIES for r in run.results)
        assert all(r.price is None for r in run.results)
    if scenario is DemoScenario.ALPACA_PARTIAL_OUTAGE:
        for ticker in ("SLVR", "QVLT"):
            listing = next(c for c in by_ticker[ticker].criteria if c.key is CriterionKey.LISTING)
            assert listing.status is CriterionStatus.UNKNOWN
        assert run.issues[0].tickers == ["QVLT", "SLVR"]
    if scenario is DemoScenario.BPIQ_MALFORMED_RECORD:
        assert run.summary.rejected_records == 1
        assert "MALF" not in by_ticker


def live_orchestrator(tmp_path, **overrides):
    settings = demo_settings(
        tmp_path,
        app_mode="live",
        bpiq_api_key=DEMO_BPIQ_KEY,
        alpaca_api_key_id=DEMO_ALPACA_KEY_ID,
        alpaca_api_secret_key=DEMO_ALPACA_SECRET,
        **overrides,
    )

    def factory(*, mode, today, scenario):
        assert mode is DataMode.LIVE and scenario is None
        server = DemoProviderServer(today=today, scenario=DemoScenario.NORMAL, calendar=calendar)
        return build_live_providers(
            settings,
            sleep=SleepRecorder(),
            transports={
                "bpiq": httpx.MockTransport(server.bpiq_handler),
                "alpaca_data": httpx.MockTransport(server.alpaca_data_handler),
                "alpaca_trading": httpx.MockTransport(server.alpaca_trading_handler),
            },
        )

    return ScanOrchestrator(settings=settings, calendar=calendar, repository=ScanRepository(settings.database_path), clock=lambda: SCAN_NOW, provider_factory=factory)


async def test_live_pipeline_never_contains_mock_financials(tmp_path):
    run = await live_orchestrator(tmp_path).run(ScanCriteria(runway_enabled=True))
    assert run.mode is DataMode.LIVE
    assert '"is_mock":true' not in run.model_dump_json()
    for result in run.results:
        runway = next(c for c in result.criteria if c.key is CriterionKey.RUNWAY)
        assert runway.status is CriterionStatus.UNKNOWN
        assert runway.explanation == LIVE_RUNWAY_UNAVAILABLE
        assert not result.runway_is_mock and result.runway_months is None
    assert run.summary.qualifying == 0
    assert any("unavailable" in n for n in run.notices)


async def test_live_scan_does_not_apply_runway_by_default(tmp_path):
    run = await live_orchestrator(tmp_path).run(ScanCriteria())
    assert run.not_applied_criteria == []
    assert all(c.status is CriterionStatus.NOT_APPLIED for r in run.results for c in r.criteria if c.key is CriterionKey.RUNWAY)
    assert run.summary.qualifying == 8
    assert {"SLVR", "NOVF"} <= {r.ticker for r in run.results if r.eligibility is Eligibility.QUALIFIES}


async def test_live_mode_without_credentials_is_configuration_error(tmp_path):
    settings = demo_settings(tmp_path, app_mode="live")
    orch = ScanOrchestrator(settings=settings, calendar=calendar, repository=ScanRepository(settings.database_path), clock=lambda: SCAN_NOW)
    run = await orch.run(ScanCriteria())
    assert run.outcome is ScanOutcome.CONFIGURATION_ERROR
    assert "BPIQ_API_KEY" in run.issues[0].message
    assert run.results == []


async def test_live_mode_rejects_demo_scenarios(tmp_path):
    with pytest.raises(ValueError):
        await live_orchestrator(tmp_path).run(ScanCriteria(), demo_scenario=DemoScenario.NORMAL)
