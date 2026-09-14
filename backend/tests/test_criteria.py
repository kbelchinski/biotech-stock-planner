from datetime import UTC, date, datetime, timedelta

import pytest

from app.domain.models import (
    CatalystType,
    CompanyProfile,
    CriterionKey,
    CriterionResult,
    CriterionStatus,
    Eligibility,
    FinancialSnapshot,
    ListingInfo,
    PriceHistory,
    ScanCriteria,
    SourceRef,
)
from app.providers.bpiq.normalize import NormalizedCatalyst, normalize_catalyst
from app.screening.criteria import CompanyInputs, ScanContext, evaluate_company, overall_eligibility
from app.screening.market_calendar import MarketCalendar
from tests.helpers import SCAN_TODAY, bar, bpiq_record

calendar = MarketCalendar()
LATEST = SCAN_TODAY
WEEKS = calendar.completed_weeks(LATEST, 4)
SRC = SourceRef(provider="test")


def catalyst(days: int, stage="Phase 3", event="Topline data", record_id=1):
    raw = bpiq_record(record_id, "TEST", catalyst_date=(SCAN_TODAY + timedelta(days=days)).isoformat(), stage=stage, event=event)
    normalized = normalize_catalyst(raw, datetime(2026, 9, 14, tzinfo=UTC))
    assert isinstance(normalized, NormalizedCatalyst)
    return normalized.catalyst


def full_history(close=10.0, volume=100_000, vwap=10.0, skip=()):
    sessions = [s for w in WEEKS for s in w.sessions] + [LATEST]
    bars = [bar(s, close=close, volume=volume, vwap=vwap) for s in sessions if s not in skip]
    return PriceHistory(
        ticker="TEST", feed="sip", adjustment="raw", requested_start=WEEKS[0].week_start, requested_end=LATEST, bars=bars, source=SRC
    )


def passing_inputs(**overrides) -> CompanyInputs:
    values = dict(
        ticker="TEST",
        profile=CompanyProfile(ticker="TEST", name="Test Inc.", market_cap_usd=500e6, provider_last_price="10", source=SRC),
        catalysts=[catalyst(75)],
        price_history=full_history(),
        listing=ListingInfo(ticker="TEST", exchange="NASDAQ", status="active", asset_class="us_equity", tradable=True, name=None, source=SRC),
        financials=FinancialSnapshot(ticker="TEST", cash_usd=240e6, monthly_net_burn_usd=10e6, as_of=date(2026, 6, 30), source=SRC),
    )
    values.update(overrides)
    return CompanyInputs(**values)


def evaluate(inputs: CompanyInputs, **criteria):
    criteria.setdefault("runway_enabled", True)
    ctx = ScanContext(today=SCAN_TODAY, criteria=ScanCriteria(**criteria), latest_session=LATEST, weeks=WEEKS)
    return evaluate_company(inputs, ctx)


def status_of(result, key: CriterionKey) -> CriterionStatus:
    return next(c.status for c in result.criteria if c.key is key)


def test_all_criteria_pass():
    result = evaluate(passing_inputs())
    assert result.eligibility is Eligibility.QUALIFIES
    assert {c.status for c in result.criteria} == {CriterionStatus.PASS}
    assert result.price_bars
    assert result.price_bars[-1].close == 10.0


@pytest.mark.parametrize(("days", "expected"), [(59, "fail"), (60, "pass"), (75, "pass"), (90, "pass"), (91, "fail")])
def test_catalyst_window_is_inclusive(days, expected):
    result = evaluate(passing_inputs(catalysts=[catalyst(days)]))
    assert status_of(result, CriterionKey.CATALYST).value == expected


def test_deselected_catalyst_type_fails():
    result = evaluate(
        passing_inputs(catalysts=[catalyst(75, stage="NDA Filing", event="PDUFA")]),
        catalyst_types=[CatalystType.PHASE2_RESULTS, CatalystType.PHASE3_RESULTS],
    )
    assert status_of(result, CriterionKey.CATALYST) is CriterionStatus.FAIL


def test_multiple_catalysts_one_qualifying_passes_and_all_are_kept():
    cats = [catalyst(30, record_id=1), catalyst(70, stage="NDA Filing", event="PDUFA", record_id=2), catalyst(80, event="Enrollment complete", record_id=3)]
    result = evaluate(passing_inputs(catalysts=cats))
    assert status_of(result, CriterionKey.CATALYST) is CriterionStatus.PASS
    assert len(result.catalysts) == 3
    assert result.primary_catalyst_event_id == "bpiq:catalyst:2"


def test_unrecognized_classification_in_window_is_unknown_not_fail():
    result = evaluate(passing_inputs(catalysts=[catalyst(75, stage="Phase 2/3")]))
    assert status_of(result, CriterionKey.CATALYST) is CriterionStatus.UNKNOWN
    assert result.eligibility is Eligibility.INSUFFICIENT_DATA


@pytest.mark.parametrize(("cap", "expected"), [(49_999_999, "fail"), (50_000_000, "pass"), (2_000_000_000, "pass"), (2_000_000_001, "fail"), (None, "unknown")])
def test_market_cap_bounds_inclusive(cap, expected):
    profile = CompanyProfile(ticker="TEST", name="Test", market_cap_usd=cap, provider_last_price=None, source=SRC)
    assert status_of(evaluate(passing_inputs(profile=profile)), CriterionKey.MARKET_CAP).value == expected


@pytest.mark.parametrize(("close", "expected"), [(1.0, "fail"), (0.99, "fail"), (1.0001, "pass")])
def test_price_must_be_strictly_above_threshold(close, expected):
    assert status_of(evaluate(passing_inputs(price_history=full_history(close=close))), CriterionKey.PRICE).value == expected


def test_price_without_latest_session_bar_is_unknown_not_stale():
    result = evaluate(passing_inputs(price_history=full_history(skip={LATEST})))
    price = next(c for c in result.criteria if c.key is CriterionKey.PRICE)
    assert price.status is CriterionStatus.UNKNOWN
    assert "Older closes are not used" in price.explanation
    assert result.price is None


@pytest.mark.parametrize(
    ("cash", "burn", "expected"),
    [(120e6, 10e6, "pass"), (119e6, 10e6, "fail"), (50e6, 0.0, "pass"), (50e6, -2e6, "pass"), (None, 10e6, "unknown"), (120e6, None, "unknown")],
)
def test_runway(cash, burn, expected):
    fin = FinancialSnapshot(ticker="TEST", cash_usd=cash, monthly_net_burn_usd=burn, as_of=date(2026, 6, 30), source=SRC)
    assert status_of(evaluate(passing_inputs(financials=fin)), CriterionKey.RUNWAY).value == expected


def test_missing_financials_is_unknown_with_reason():
    result = evaluate(passing_inputs(financials=None, financials_unavailable_reason="Not available from providers."))
    runway = next(c for c in result.criteria if c.key is CriterionKey.RUNWAY)
    assert runway.status is CriterionStatus.UNKNOWN
    assert runway.explanation == "Not available from providers."
    assert result.eligibility is Eligibility.INSUFFICIENT_DATA


def test_mock_financials_are_labelled():
    fin = FinancialSnapshot(ticker="TEST", cash_usd=240e6, monthly_net_burn_usd=10e6, as_of=None, source=SourceRef(provider="mock", is_mock=True))
    result = evaluate(passing_inputs(financials=fin))
    runway = next(c for c in result.criteria if c.key is CriterionKey.RUNWAY)
    assert runway.explanation.startswith("DEMO MOCK DATA")
    assert result.runway_is_mock


def test_disabled_criterion_is_not_applied_and_does_not_block():
    result = evaluate(passing_inputs(financials=None), runway_enabled=False)
    assert status_of(result, CriterionKey.RUNWAY) is CriterionStatus.NOT_APPLIED
    assert result.eligibility is Eligibility.QUALIFIES


@pytest.mark.parametrize(
    ("exchange", "status", "expected"),
    [("NASDAQ", "active", "pass"), ("NYSEARCA", "active", "pass"), ("OTC", "active", "fail"), ("NYSE", "inactive", "fail")],
)
def test_listing(exchange, status, expected):
    listing = ListingInfo(ticker="TEST", exchange=exchange, status=status, asset_class="us_equity", tradable=True, name=None, source=SRC)
    assert status_of(evaluate(passing_inputs(listing=listing)), CriterionKey.LISTING).value == expected


def test_listing_not_found_is_unknown():
    assert status_of(evaluate(passing_inputs(listing=None)), CriterionKey.LISTING) is CriterionStatus.UNKNOWN


def _criterion(status: CriterionStatus) -> CriterionResult:
    return CriterionResult(key=CriterionKey.PRICE, label="x", status=status, observed=None, threshold="", explanation="")


@pytest.mark.parametrize(
    ("statuses", "expected"),
    [
        (["pass", "pass"], Eligibility.QUALIFIES),
        (["pass", "not_applied"], Eligibility.QUALIFIES),
        (["pass", "unknown"], Eligibility.INSUFFICIENT_DATA),
        (["fail", "unknown"], Eligibility.DOES_NOT_QUALIFY),
        (["not_applied", "fail"], Eligibility.DOES_NOT_QUALIFY),
    ],
)
def test_overall_eligibility(statuses, expected):
    assert overall_eligibility([_criterion(CriterionStatus(s)) for s in statuses]) is expected
