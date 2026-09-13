"""US equity market dates in America/New_York, using the XNYS exchange calendar."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from functools import lru_cache
from zoneinfo import ZoneInfo

import exchange_calendars as xcals
import pandas as pd

NEW_YORK = ZoneInfo("America/New_York")

# A session's daily bar is treated as final this long after the regular close. Whether Alpaca
# daily bars include extended-hours trades is not documented, so wait until post-market
# (4 hours after close) has ended, plus Alpaca's 15-minute free-plan SIP delay.
BAR_SETTLE_AFTER_CLOSE = timedelta(hours=4, minutes=15)


@dataclass(frozen=True)
class TradingWeek:
    week_start: date  # Monday
    week_end: date  # Sunday
    sessions: tuple[date, ...]


@lru_cache(maxsize=1)
def _calendar() -> xcals.ExchangeCalendar:
    return xcals.get_calendar("XNYS")


class MarketCalendar:
    def __init__(self, calendar: xcals.ExchangeCalendar | None = None) -> None:
        self._cal = calendar or _calendar()

    def sessions_between(self, start: date, end: date) -> list[date]:
        if end < start:
            return []
        first = max(start, self._cal.first_session.date())
        last = min(end, self._cal.last_session.date())
        if last < first:
            return []
        return [ts.date() for ts in self._cal.sessions_in_range(pd.Timestamp(first), pd.Timestamp(last))]

    def is_session(self, day: date) -> bool:
        return bool(self._cal.is_session(pd.Timestamp(day)))

    def session_close(self, session: date) -> datetime:
        return self._cal.session_close(pd.Timestamp(session)).to_pydatetime().astimezone(NEW_YORK)

    def latest_completed_session(self, now: datetime) -> date:
        """Latest session whose daily bar is considered final at `now`."""
        now_ny = now.astimezone(NEW_YORK)
        day = now_ny.date()
        for session in reversed(self.sessions_between(day - timedelta(days=14), day)):
            if self.session_close(session) + BAR_SETTLE_AFTER_CLOSE <= now_ny:
                return session
        raise ValueError(f"No completed session found before {now_ny.isoformat()}")

    def completed_weeks(self, latest_completed: date, count: int) -> list[TradingWeek]:
        """The `count` most recent Monday–Sunday weeks whose sessions are all completed.

        Holiday-shortened weeks count as completed weeks. Returned oldest first.
        """
        weeks: list[TradingWeek] = []
        week_start = latest_completed - timedelta(days=latest_completed.weekday())
        # The current week counts only if no session remains after `latest_completed`.
        if self.sessions_between(latest_completed + timedelta(days=1), week_start + timedelta(days=6)):
            week_start -= timedelta(days=7)
        guard = 0
        while len(weeks) < count:
            week_end = week_start + timedelta(days=6)
            sessions = self.sessions_between(week_start, week_end)
            if sessions:
                weeks.append(TradingWeek(week_start, week_end, tuple(sessions)))
            week_start -= timedelta(days=7)
            guard += 1
            if guard > count + 10:
                raise ValueError("Could not find enough trading weeks in the calendar range")
        return list(reversed(weeks))


def new_york_today(now: datetime) -> date:
    return now.astimezone(NEW_YORK).date()


def new_york_midnight(day: date) -> datetime:
    return datetime.combine(day, time.min, tzinfo=NEW_YORK)
