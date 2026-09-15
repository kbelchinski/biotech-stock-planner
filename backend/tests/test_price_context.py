import math
from datetime import date

from app.domain.research import FirstSeen, MetricStatus
from app.research.price_context import compute_price_context
from app.screening.market_calendar import MarketCalendar
from tests.helpers import SCAN_TODAY, bar

calendar = MarketCalendar()
SESSIONS = calendar.sessions_between(date(2025, 8, 1), SCAN_TODAY)


def series(sessions, *, start=10.0, step=0.01, volume=1000.0):
    out = []
    for i, s in enumerate(sessions):
        b = bar(s, close=start * (1 + step) ** i, volume=volume)
        out.append(b.model_copy(update={"high": b.close * 1.01, "low": b.close * 0.99}))
    return out


def ctx(bars, bench=None, first_seen=None, first_close=None, sessions=SESSIONS):
    return compute_price_context(
        ticker="AAAA", bars=bars, benchmark_symbol="XBI", benchmark_bars=bench, sessions=sessions,
        latest_session=SCAN_TODAY, first_seen=first_seen, retrieved_at=None, first_seen_close=first_close,
    )


def metric(result, key):
    return next(m for m in result.metrics if m.key == key)


def test_returns_count_exchange_sessions_across_labor_day():
    bars = series(SESSIONS)
    result = ctx(bars, bench=series(SESSIONS, step=0.0))
    r5 = metric(result, "return_5")
    assert r5.status is MetricStatus.OK
    assert "2026-09-04" in r5.detail  # Sep 7 (Labor Day) is not a session
    assert math.isclose(r5.value, 1.01**5 - 1, rel_tol=1e-9)
    assert math.isclose(metric(result, "relative_5").value, 1.01**5 - 1, rel_tol=1e-9)
    assert result.freshness.startswith("Latest bar is the latest completed session")


def test_missing_bar_is_never_substituted():
    bars = [b for b in series(SESSIONS) if b.session_date != date(2026, 9, 4)]
    result = ctx(bars)
    assert metric(result, "return_5").status is MetricStatus.MISSING_BARS
    assert metric(result, "return_5").value is None
    assert metric(result, "from_sma_20").status is MetricStatus.MISSING_BARS
    assert metric(result, "relative_5").status is MetricStatus.MISSING_BARS
    assert metric(result, "return_20").status is MetricStatus.OK


def test_insufficient_history_and_stale_data():
    short = SESSIONS[-30:]
    bars = series(short)[:-1]  # latest session missing
    result = ctx(bars, sessions=short)
    assert metric(result, "return_60").status is MetricStatus.INSUFFICIENT_HISTORY
    assert metric(result, "volume_ratio").status is MetricStatus.INSUFFICIENT_HISTORY
    assert result.freshness.startswith("STALE")
    assert result.issues


def test_volume_ratio_against_documented_baseline():
    bars = series(SESSIONS, step=0.0)
    recent = set(SESSIONS[-5:])
    bars = [b.model_copy(update={"volume": 2000.0 if b.session_date in recent else 1000.0}) for b in bars]
    assert math.isclose(metric(ctx(bars), "volume_ratio").value, 2.0)


def test_volatility_highs_and_gaps():
    bars = series(SESSIONS, step=0.0)
    idx = len(bars) - 10
    bars[idx] = bars[idx].model_copy(update={"open": bars[idx - 1].close * 1.2})
    result = ctx(bars)
    assert [g.session for g in result.gaps] == [SESSIONS[idx]]
    assert math.isclose(result.gaps[0].gap_pct, 20.0, rel_tol=1e-6)
    assert metric(result, "volatility_20").value == 0.0
    assert math.isclose(metric(result, "from_high_60").value, 1 / 1.01 - 1, rel_tol=1e-9)
    assert metric(result, "from_high_252").status is MetricStatus.OK


def test_return_since_first_seen_uses_first_seen_session():
    bars = series(SESSIONS)
    start = SESSIONS[-11]
    seen = FirstSeen(basis="first_qualified", scan_id="abcdef123", scan_date=start, price_session=start, close=None)
    first_close = next(b.close for b in bars if b.session_date == start)
    result = ctx(bars, first_seen=seen, first_close=first_close)
    assert math.isclose(metric(result, "return_since_first_seen").value, 1.01**10 - 1, rel_tol=1e-9)
    assert metric(ctx(bars), "return_since_first_seen").status is MetricStatus.UNAVAILABLE
