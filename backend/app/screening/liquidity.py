"""Average weekly dollar turnover over completed trading weeks.

Daily turnover = volume × daily VWAP. If a bar has no VWAP, close × volume is used for that
session and the result is labelled as an approximation. Prices and volumes both come from
adjustment=raw bars, so they are mutually consistent.

A session with no bar is missing data, never zero activity. Because missing sessions can only
add turnover, a partial total is a lower bound.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from app.domain.models import DailyBar, WeeklyTurnover
from app.screening.market_calendar import TradingWeek


@dataclass(frozen=True)
class LiquidityComputation:
    weeks: list[WeeklyTurnover]
    average_usd: float | None
    is_lower_bound: bool
    method: str
    missing_sessions: list[date] = field(default_factory=list)
    approximated_sessions: int = 0

    @property
    def has_any_data(self) -> bool:
        return any(w.sessions_with_bars for w in self.weeks)


def daily_turnover(bar: DailyBar) -> tuple[float, bool]:
    """Return (turnover, approximated)."""
    if bar.vwap is not None:
        return bar.volume * bar.vwap, False
    return bar.volume * bar.close, True


def compute_weekly_turnover(bars: list[DailyBar], weeks: list[TradingWeek]) -> LiquidityComputation:
    by_date = {bar.session_date: bar for bar in bars}
    weekly: list[WeeklyTurnover] = []
    missing: list[date] = []
    approximated_total = 0

    for week in weeks:
        total = 0.0
        present = 0
        approximated = 0
        for session in week.sessions:
            bar = by_date.get(session)
            if bar is None:
                missing.append(session)
                continue
            value, approx = daily_turnover(bar)
            total += value
            present += 1
            approximated += int(approx)
        approximated_total += approximated
        weekly.append(
            WeeklyTurnover(
                week_start=week.week_start,
                week_end=week.week_end,
                sessions=list(week.sessions),
                sessions_with_bars=present,
                total_usd=total,
                complete=present == len(week.sessions),
                approximated_sessions=approximated,
            )
        )

    total_sessions = sum(len(w.sessions) for w in weeks)
    if approximated_total == 0:
        method = "Σ daily volume × VWAP per week, averaged over weeks"
    elif approximated_total == total_sessions - len(missing):
        method = "APPROXIMATION: Σ daily close × volume (VWAP unavailable)"
    else:
        method = (
            f"Σ daily volume × VWAP; APPROXIMATION for {approximated_total} session(s) "
            "without VWAP (close × volume)"
        )

    average = sum(w.total_usd for w in weekly) / len(weekly) if weekly else None
    return LiquidityComputation(
        weeks=weekly,
        average_usd=average,
        is_lower_bound=bool(missing),
        method=method,
        missing_sessions=missing,
        approximated_sessions=approximated_total,
    )
