from datetime import date

import pytest

from app.domain.models import CriterionKey, CriterionStatus, PriceHistory, ScanCriteria, SourceRef
from app.screening.criteria import CompanyInputs, ScanContext, liquidity_criterion
from app.screening.liquidity import compute_weekly_turnover
from app.screening.market_calendar import MarketCalendar
from tests.helpers import SCAN_TODAY, bar

calendar = MarketCalendar()
WEEKS = calendar.completed_weeks(SCAN_TODAY, 4)  # last week is Labor Day week (4 sessions)
SESSIONS = [s for w in WEEKS for s in w.sessions]


def test_weekly_sums_include_holiday_shortened_week():
    bars = [bar(s, volume=1_000, vwap=20.0) for s in SESSIONS]
    calc = compute_weekly_turnover(bars, WEEKS)
    assert [w.total_usd for w in calc.weeks] == [100_000, 100_000, 100_000, 80_000]
    assert calc.average_usd == 95_000
    assert not calc.is_lower_bound
    assert "VWAP" in calc.method and "APPROXIMATION" not in calc.method


def test_missing_vwap_uses_labelled_close_times_volume_approximation():
    bars = [bar(s, close=10.0, volume=1_000, vwap=(None if s == SESSIONS[0] else 20.0)) for s in SESSIONS]
    calc = compute_weekly_turnover(bars, WEEKS)
    assert calc.weeks[0].total_usd == 10_000 + 4 * 20_000
    assert calc.approximated_sessions == 1
    assert "APPROXIMATION" in calc.method


def test_missing_sessions_make_total_a_lower_bound_not_zero():
    missing = {SESSIONS[2], SESSIONS[10]}
    calc = compute_weekly_turnover([bar(s, volume=1_000, vwap=20.0) for s in SESSIONS if s not in missing], WEEKS)
    assert calc.is_lower_bound
    assert calc.missing_sessions == sorted(missing)
    assert not calc.weeks[0].complete and calc.weeks[1].complete is True


def _criterion(bars, threshold=2_000_000):
    history = PriceHistory(
        ticker="T", feed="sip", adjustment="raw", requested_start=WEEKS[0].week_start, requested_end=SCAN_TODAY, bars=bars, source=SourceRef(provider="t")
    )
    ctx = ScanContext(today=SCAN_TODAY, criteria=ScanCriteria(liquidity_min_avg_weekly_usd=threshold), latest_session=SCAN_TODAY, weeks=WEEKS)
    result, _ = liquidity_criterion(CompanyInputs(ticker="T", profile=None, catalysts=[], price_history=history), ctx)
    assert result.key is CriterionKey.LIQUIDITY
    return result


def _bars_with_weekly_total(total: float, skip=()):
    # Every week totals exactly `total` regardless of session count.
    out = []
    for week in WEEKS:
        per_day = total / len(week.sessions)
        out += [bar(s, volume=per_day / 10.0, vwap=10.0) for s in week.sessions if s not in skip]
    return out


@pytest.mark.parametrize(("weekly", "expected"), [(2_000_000, CriterionStatus.FAIL), (2_000_010, CriterionStatus.PASS), (1_000_000, CriterionStatus.FAIL)])
def test_threshold_is_strictly_above(weekly, expected):
    assert _criterion(_bars_with_weekly_total(weekly)).status is expected


def test_incomplete_history_above_threshold_passes_as_lower_bound():
    result = _criterion(_bars_with_weekly_total(10_000_000, skip={SESSIONS[0]}))
    assert result.status is CriterionStatus.PASS
    assert result.observed.startswith("≥")


def test_incomplete_history_below_threshold_is_unknown():
    result = _criterion(_bars_with_weekly_total(2_100_000, skip={SESSIONS[0], SESSIONS[1], SESSIONS[5]}))
    assert result.status is CriterionStatus.UNKNOWN


def test_no_history_is_unknown_not_zero():
    result = _criterion([bar(date(2026, 9, 14))])  # only a bar outside the four completed weeks
    assert result.status is CriterionStatus.UNKNOWN
    assert "not zero" in result.explanation
