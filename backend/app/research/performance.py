"""Forward paper-trade rules and performance statistics.

Paper-trade rules confirmed 2026-09-14:
- Candidates: companies that Qualify in a saved scan and have a dated primary catalyst.
- Entry: the OPEN of the first XNYS session strictly after the scan's latest completed (price)
  session. A scan that used a session's close never enters in that same session.
- Exit: the CLOSE of the last XNYS session strictly before the primary catalyst date. If the
  provider revises the date before exit, the planned exit moves with it and the change is logged.
  No stops or intraday fills are simulated.
- Size: equal notional per trade (PAPER_NOTIONAL_USD); results are reported as returns.
- Costs: 0.10% commission-equivalent + 0.25% slippage per side, applied adversely.
- Gaps: fills use the actual open/close, including gaps.
- Missing prices: a missing entry or exit bar marks the trade price_unavailable. Prices are never
  interpolated or taken from another session.

Hypothetical, actual, paper and planned trades are always summarised separately.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from datetime import date, datetime

from app.domain.models import DailyBar
from app.domain.research import CategoryStats, OutcomeRow, PerformanceSummary, Trade, TradeKind, TradeStatus

PAPER_NOTIONAL_USD = 1_000.0
PAPER_COST_PCT = 0.10
PAPER_SLIPPAGE_PCT = 0.25
SMALL_SAMPLE = 20

PERFORMANCE_CONVENTIONS = [
    "Net return = exit × (1 − cost − slippage) ÷ (entry × (1 + cost + slippage)) − 1, per side percentages as entered on the trade.",
    "Win = net return > 0. Average win/loss are means of net returns within each group.",
    "Compounded return and maximum drawdown chain closed trades in exit-date order as if each used the full equity; overlapping trades are not modelled.",
    "Benchmark-relative = trade net return − benchmark price return over the same holding period "
    "(benchmark close on the exit date ÷ benchmark open on the entry date − 1, no costs).",
    "Open positions are listed separately and never counted in win rate or averages.",
]


def net_return(entry: float, exit_: float, cost_pct: float, slippage_pct: float) -> float:
    side = (cost_pct + slippage_pct) / 100
    return (exit_ * (1 - side)) / (entry * (1 + side)) - 1


def paper_exit_date(catalyst_date: date, sessions_before: Callable[[date, int], date | None]) -> date | None:
    """Last XNYS session strictly before the catalyst date."""
    return sessions_before(catalyst_date, 1)


def resolve_trade(
    trade: Trade,
    *,
    bars: dict[date, DailyBar],
    latest_session: date,
    now: datetime,
    catalyst_date: date | None,
    sessions_before: Callable[[date, int], date | None],
) -> Trade:
    """Advance an automated paper trade using only bars up to `latest_session`."""
    if trade.kind is not TradeKind.PAPER or trade.status in (TradeStatus.CLOSED, TradeStatus.CANCELLED, TradeStatus.PRICE_UNAVAILABLE):
        return trade
    t = trade.model_copy(deep=True)

    if catalyst_date is not None and t.status in (TradeStatus.PENDING_ENTRY, TradeStatus.OPEN):
        new_exit = paper_exit_date(catalyst_date, sessions_before)
        if new_exit != t.planned_exit_date and (t.exit_date is None):
            t.history.append(f"{now.date()}: planned exit {t.planned_exit_date} → {new_exit} (catalyst date now {catalyst_date}).")
            t.planned_exit_date = new_exit

    if t.status is TradeStatus.PENDING_ENTRY and t.entry_date is not None:
        if t.planned_exit_date is None or t.planned_exit_date < t.entry_date:
            t.status = TradeStatus.CANCELLED
            t.history.append(f"{now.date()}: cancelled — catalyst revision leaves no session between entry {t.entry_date} and exit.")
            t.updated_at = now
            return t
        if t.entry_date <= latest_session:
            bar = bars.get(t.entry_date)
            if bar is None:
                t.status = TradeStatus.PRICE_UNAVAILABLE
                t.history.append(f"{now.date()}: no bar for entry session {t.entry_date}; no price substituted.")
                t.updated_at = now
                return t
            t.entry_price = bar.open
            t.entry_basis = f"Open of {t.entry_date} (first session after scan price session)"
            t.shares = t.notional_usd / bar.open if t.notional_usd else None
            t.status = TradeStatus.OPEN
            t.history.append(f"{now.date()}: entered at open {bar.open:.4f} on {t.entry_date}.")

    if t.status is TradeStatus.OPEN and t.planned_exit_date is not None and t.planned_exit_date <= latest_session:
        bar = bars.get(t.planned_exit_date)
        if bar is None:
            t.status = TradeStatus.PRICE_UNAVAILABLE
            t.history.append(f"{now.date()}: no bar for exit session {t.planned_exit_date}; no price substituted.")
        else:
            entry_bar = bars.get(t.entry_date) if t.entry_date else None
            if entry_bar is not None and t.entry_price and abs(entry_bar.open / t.entry_price - 1) > 0.005:
                # Split-adjusted history changed (e.g. a split during the hold): re-read entry from the same series as the exit.
                t.history.append(
                    f"{now.date()}: entry re-based {t.entry_price:.4f} → {entry_bar.open:.4f} from the split-adjusted series used for the exit."
                )
                t.entry_price = entry_bar.open
            t.exit_date = t.planned_exit_date
            t.exit_price = bar.close
            t.exit_basis = f"Close of {t.planned_exit_date} (last session before catalyst)"
            t.status = TradeStatus.CLOSED
            t.history.append(f"{now.date()}: exited at close {bar.close:.4f} on {t.exit_date}.")
    t.updated_at = now
    return t


def summarize(
    trades: list[Trade],
    kind: TradeKind,
    *,
    benchmark_return: Callable[[date, date], float | None],
    latest_close: Callable[[str], tuple[date, float] | None],
) -> PerformanceSummary:
    group = [t for t in trades if t.kind is kind]
    closed = [
        t for t in group if t.status is TradeStatus.CLOSED and t.entry_price and t.exit_price and t.entry_date and t.exit_date
    ]
    open_ = [t for t in group if t.status is TradeStatus.OPEN]
    unresolved = [t for t in group if t.status in (TradeStatus.PRICE_UNAVAILABLE, TradeStatus.PENDING_ENTRY)]

    rows: list[OutcomeRow] = []
    for t in sorted(closed, key=lambda t: (t.exit_date, t.id)):
        r = net_return(t.entry_price, t.exit_price, t.cost_pct_per_side, t.slippage_pct_per_side)  # type: ignore[arg-type]
        b = benchmark_return(t.entry_date, t.exit_date)  # type: ignore[arg-type]
        rows.append(
            OutcomeRow(
                trade_id=t.id,
                ticker=t.ticker,
                category=t.catalyst_category or "Uncategorized",
                entry_date=t.entry_date,  # type: ignore[arg-type]
                exit_date=t.exit_date,  # type: ignore[arg-type]
                net_return=r,
                benchmark_return=b,
                relative_return=r - b if b is not None else None,
            )
        )

    wins = [r.net_return for r in rows if r.net_return > 0]
    losses = [r.net_return for r in rows if r.net_return <= 0]
    equity, peak, max_dd = 1.0, 1.0, 0.0
    for r in rows:
        equity *= 1 + r.net_return
        peak = max(peak, equity)
        max_dd = min(max_dd, equity / peak - 1)
    rel = [r.relative_return for r in rows if r.relative_return is not None]

    cats: dict[str, list[float]] = defaultdict(list)
    for r in rows:
        cats[r.category].append(r.net_return)

    open_positions = []
    for t in open_:
        mark = latest_close(t.ticker)
        unrealized = (
            net_return(t.entry_price, mark[1], t.cost_pct_per_side, t.slippage_pct_per_side) if mark and t.entry_price else None
        )
        open_positions.append(
            {
                "trade_id": t.id,
                "ticker": t.ticker,
                "entry_date": t.entry_date,
                "entry_price": t.entry_price,
                "planned_exit_date": t.planned_exit_date,
                "mark_date": mark[0] if mark else None,
                "mark_close": mark[1] if mark else None,
                "unrealized_net_return": unrealized,
            }
        )

    warnings = []
    if 0 < len(rows) < SMALL_SAMPLE:
        warnings.append(f"Only {len(rows)} completed trade(s). Too few to draw conclusions about the strategy.")
    if not rows:
        warnings.append("No completed trades yet.")
    if kind is TradeKind.PAPER:
        warnings.append("Forward tracking only. Results are not a historical backtest and do not validate the strategy.")
    if unresolved:
        warnings.append(f"{len(unresolved)} trade(s) are pending or have unavailable prices and are excluded from statistics.")

    return PerformanceSummary(
        kind=kind,
        closed_count=len(rows),
        open_count=len(open_),
        unresolved_count=len(unresolved),
        observation_start=min((r.entry_date for r in rows), default=None),
        observation_end=max((r.exit_date for r in rows), default=None),
        win_rate=len(wins) / len(rows) if rows else None,
        average_win=sum(wins) / len(wins) if wins else None,
        average_loss=sum(losses) / len(losses) if losses else None,
        total_compounded_return=equity - 1 if rows else None,
        max_drawdown=max_dd if rows else None,
        worst=sorted(rows, key=lambda r: r.net_return)[:3],
        benchmark_relative_average=sum(rel) / len(rel) if rel else None,
        benchmark_coverage=len(rel),
        by_category=[
            CategoryStats(
                category=c,
                count=len(v),
                win_rate=sum(1 for x in v if x > 0) / len(v),
                average_net_return=sum(v) / len(v),
            )
            for c, v in sorted(cats.items())
        ],
        open_positions=open_positions,
        conventions=PERFORMANCE_CONVENTIONS,
        warnings=warnings,
    )
