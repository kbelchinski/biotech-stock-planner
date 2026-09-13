from datetime import date, datetime

from app.screening.market_calendar import NEW_YORK, MarketCalendar

calendar = MarketCalendar()


def ny(*args: int) -> datetime:
    return datetime(*args, tzinfo=NEW_YORK)


def test_regular_session_counts_as_completed_only_after_bar_settles():
    assert calendar.latest_completed_session(ny(2026, 9, 14, 20, 0)) == date(2026, 9, 11)
    assert calendar.latest_completed_session(ny(2026, 9, 14, 20, 30)) == date(2026, 9, 14)


def test_early_close_session_settles_earlier():
    # Day after Thanksgiving 2025 closes at 13:00 ET.
    assert calendar.latest_completed_session(ny(2025, 11, 28, 17, 0)) == date(2025, 11, 26)
    assert calendar.latest_completed_session(ny(2025, 11, 28, 17, 30)) == date(2025, 11, 28)


def test_weekend_uses_friday_session():
    assert calendar.latest_completed_session(ny(2026, 9, 13, 12, 0)) == date(2026, 9, 11)


def test_holiday_shortened_week_counts_as_completed_week():
    weeks = calendar.completed_weeks(date(2025, 11, 28), 4)
    assert [w.week_start for w in weeks] == [date(2025, 11, 3), date(2025, 11, 10), date(2025, 11, 17), date(2025, 11, 24)]
    assert weeks[-1].sessions == (date(2025, 11, 24), date(2025, 11, 25), date(2025, 11, 26), date(2025, 11, 28))


def test_partially_elapsed_week_is_excluded():
    weeks = calendar.completed_weeks(date(2025, 11, 26), 4)
    assert [w.week_start for w in weeks] == [date(2025, 10, 27), date(2025, 11, 3), date(2025, 11, 10), date(2025, 11, 17)]


def test_labor_day_week_and_monday_scan():
    weeks = calendar.completed_weeks(date(2026, 9, 14), 4)
    assert [w.week_start for w in weeks] == [date(2026, 8, 17), date(2026, 8, 24), date(2026, 8, 31), date(2026, 9, 7)]
    assert len(weeks[-1].sessions) == 4
    assert date(2026, 9, 7) not in weeks[-1].sessions
