import math
from datetime import UTC, date, datetime, timedelta

from app.domain.research import Trade, TradeKind, TradeStatus
from app.research.performance import net_return, resolve_trade, summarize
from app.screening.market_calendar import MarketCalendar
from tests.helpers import SCAN_NOW, bar

calendar = MarketCalendar()


def trade(kind=TradeKind.PAPER, status=TradeStatus.PENDING_ENTRY, **kw):
    base = dict(
        id=kw.pop("id", "t1"), mode="demo", kind=kind, status=status, ticker=kw.pop("ticker", "AAAA"),
        created_at=SCAN_NOW, updated_at=SCAN_NOW, cost_pct_per_side=0.10, slippage_pct_per_side=0.25, notional_usd=1000.0,
    )
    base.update(kw)
    return Trade(**base)


def test_net_return_applies_costs_on_both_sides():
    assert math.isclose(net_return(10, 12, 0.10, 0.25), 12 * 0.9965 / (10 * 1.0035) - 1)


def test_paper_trade_enters_at_open_exits_at_close_before_catalyst():
    t = trade(entry_date=date(2026, 9, 15), planned_exit_date=date(2026, 9, 18))
    bars = {
        date(2026, 9, 15): bar(date(2026, 9, 15), close=11).model_copy(update={"open": 10.5}),
        date(2026, 9, 18): bar(date(2026, 9, 18), close=12),
    }
    # Only Sep 14 is complete: nothing may be filled yet (no look-ahead).
    unchanged = resolve_trade(t, bars=bars, latest_session=date(2026, 9, 14), now=SCAN_NOW, catalyst_date=date(2026, 9, 21), sessions_before=calendar.sessions_before)
    assert unchanged.status is TradeStatus.PENDING_ENTRY and unchanged.entry_price is None
    opened = resolve_trade(t, bars=bars, latest_session=date(2026, 9, 15), now=SCAN_NOW, catalyst_date=date(2026, 9, 21), sessions_before=calendar.sessions_before)
    assert opened.status is TradeStatus.OPEN and opened.entry_price == 10.5
    closed = resolve_trade(opened, bars=bars, latest_session=date(2026, 9, 18), now=SCAN_NOW, catalyst_date=date(2026, 9, 21), sessions_before=calendar.sessions_before)
    assert closed.status is TradeStatus.CLOSED and closed.exit_price == 12 and closed.exit_date == date(2026, 9, 18)


def test_missing_entry_bar_is_price_unavailable_not_substituted():
    t = trade(entry_date=date(2026, 9, 15), planned_exit_date=date(2026, 9, 18))
    out = resolve_trade(t, bars={date(2026, 9, 16): bar(date(2026, 9, 16))}, latest_session=date(2026, 9, 16), now=SCAN_NOW, catalyst_date=date(2026, 9, 21), sessions_before=calendar.sessions_before)
    assert out.status is TradeStatus.PRICE_UNAVAILABLE and out.entry_price is None


def test_catalyst_revision_moves_exit_or_cancels():
    t = trade(entry_date=date(2026, 9, 15), planned_exit_date=date(2026, 9, 18))
    moved = resolve_trade(t, bars={}, latest_session=date(2026, 9, 14), now=SCAN_NOW, catalyst_date=date(2026, 10, 1), sessions_before=calendar.sessions_before)
    assert moved.planned_exit_date == date(2026, 9, 30) and "planned exit" in moved.history[-1]
    cancelled = resolve_trade(t, bars={}, latest_session=date(2026, 9, 14), now=SCAN_NOW, catalyst_date=date(2026, 9, 15), sessions_before=calendar.sessions_before)
    assert cancelled.status is TradeStatus.CANCELLED


def test_summaries_keep_kinds_separate_and_report_sample_size():
    closed = dict(status=TradeStatus.CLOSED, entry_date=date(2026, 8, 3), exit_date=date(2026, 8, 20), catalyst_category="PDUFA decision")
    trades = [
        trade(TradeKind.ACTUAL, id="a1", entry_price=10, exit_price=12, **closed),
        trade(TradeKind.ACTUAL, id="a2", entry_price=10, exit_price=8, **{**closed, "exit_date": date(2026, 8, 25)}),
        trade(TradeKind.HYPOTHETICAL, id="h1", entry_price=10, exit_price=30, **closed),
        trade(TradeKind.ACTUAL, id="a3", status=TradeStatus.OPEN, entry_price=10, entry_date=date(2026, 9, 1)),
    ]
    summary = summarize(trades, TradeKind.ACTUAL, benchmark_return=lambda a, b: 0.01, latest_close=lambda t: (date(2026, 9, 14), 11.0))
    assert summary.closed_count == 2 and summary.open_count == 1
    assert summary.win_rate == 0.5
    assert summary.worst[0].trade_id == "a2"
    assert summary.max_drawdown < 0
    assert summary.benchmark_coverage == 2
    assert summary.by_category[0].category == "PDUFA decision"
    assert any("Only 2" in w for w in summary.warnings)
    assert summary.open_positions[0]["unrealized_net_return"] is not None
    hypo = summarize(trades, TradeKind.HYPOTHETICAL, benchmark_return=lambda a, b: None, latest_close=lambda t: None)
    assert hypo.closed_count == 1 and hypo.win_rate == 1.0 and hypo.benchmark_coverage == 0
