from datetime import UTC, date, datetime, timedelta

from app.domain.models import DataMode, SourceRef
from app.domain.research import MonitoringSettings, OutcomeRecord, ReminderRule, TrackedStatus
from app.providers.bpiq.normalize import normalize_catalyst
from app.research import catalyst_tracking as tracking
from app.screening.market_calendar import MarketCalendar
from tests.helpers import SCAN_NOW, SCAN_TODAY, bpiq_record

calendar = MarketCalendar()
MODE = DataMode.DEMO


def cat(record_id, ticker="AAAA", **kw):
    return normalize_catalyst(bpiq_record(record_id, ticker, **kw), SCAN_NOW).catalyst


def merge(previous, fetched, *, now=SCAN_NOW, complete=True, covered_until=None, today=SCAN_TODAY):
    return tracking.merge_catalysts(
        mode=MODE, ticker="AAAA", previous=previous, fetched=fetched, flags={}, now=now, today=today,
        query_complete=complete, covered_until=covered_until,
    )


def test_first_seen_then_date_revision_preserves_previous_value():
    first = merge([], [cat(1, catalyst_date="2026-11-20")])
    assert [r.kind for r in first.revisions] == ["first_seen"]
    later = SCAN_NOW + timedelta(days=1)
    second = merge(first.catalysts, [cat(1, catalyst_date="2026-12-15")], now=later)
    revision = next(r for r in second.revisions if r.kind == "date_changed")
    assert (revision.previous, revision.current) == ("2026-11-20", "2026-12-15")
    assert second.catalysts[0].first_seen_at == SCAN_NOW
    assert second.catalysts[0].catalyst_date == date(2026, 12, 15)
    assert second.notifications[0].kind == "date_changed"
    # Same change seen again produces the same dedupe id.
    again = merge(first.catalysts, [cat(1, catalyst_date="2026-12-15")], now=later + timedelta(hours=1))
    assert again.notifications[0].id == second.notifications[0].id


def test_missing_event_marked_not_returned_only_when_query_complete_and_covered():
    base = merge([], [cat(1, catalyst_date="2026-11-20"), cat(2, catalyst_date="2026-09-30")]).catalysts
    incomplete = merge(base, [cat(2, catalyst_date="2026-09-30")], complete=False)
    assert all(c.status is TrackedStatus.ACTIVE for c in incomplete.catalysts)
    # Apex trial covers 30 days: the Nov 20 event is outside the query, so it is kept unchanged.
    trial = merge(base, [cat(2, catalyst_date="2026-09-30")], covered_until=SCAN_TODAY + timedelta(days=30))
    assert next(c for c in trial.catalysts if c.provider_record_id == 1).status is TrackedStatus.ACTIVE
    paid = merge(base, [cat(2, catalyst_date="2026-09-30")])
    gone = next(c for c in paid.catalysts if c.provider_record_id == 1)
    assert gone.status is TrackedStatus.NOT_RETURNED and gone.catalyst_date == date(2026, 11, 20)
    assert "not confirmed" in paid.notifications[0].body
    back = merge(paid.catalysts, [cat(1, catalyst_date="2026-11-20"), cat(2, catalyst_date="2026-09-30")])
    assert any(r.kind == "returned_again" for r in back.revisions)


def test_undated_catalysts_are_tracked_and_past_dates_flagged():
    result = merge([], [cat(1, catalyst_date=None), cat(2, catalyst_date="2026-09-01")])
    by_id = {c.provider_record_id: c for c in result.catalysts}
    assert by_id[1].catalyst_date is None and by_id[1].date_precision.value == "undated"
    assert by_id[2].status is TrackedStatus.DATE_PASSED


def test_reminders_calendar_vs_trading_days():
    tracked = merge([], [cat(1, catalyst_date="2026-09-17")]).catalysts
    settings = MonitoringSettings(reminder_rules=[ReminderRule(offset=3, unit="calendar"), ReminderRule(offset=3, unit="trading")])
    # Sep 14 is 3 calendar days before Sep 17; 3 trading sessions before Sep 17 is Sep 14 as well (Mon).
    due = tracking.due_reminders(mode=MODE, catalysts=tracked, settings=settings, today=SCAN_TODAY, now=SCAN_NOW, sessions_before=calendar.sessions_before)
    assert len(due) == 2
    # Event on Tue Sep 8 (after Labor Day): 3 trading sessions before is Wed Sep 2; 3 calendar days before is Sat Sep 5.
    assert calendar.sessions_before(date(2026, 9, 8), 3) == date(2026, 9, 2)
    assert tracking.reminder_date(date(2026, 9, 8), 3, "calendar", calendar.sessions_before) == date(2026, 9, 5)
    # Not yet due 20 days out.
    far = merge([], [cat(3, catalyst_date="2026-10-04")]).catalysts
    assert tracking.due_reminders(mode=MODE, catalysts=far, settings=MonitoringSettings(), today=SCAN_TODAY, now=SCAN_NOW, sessions_before=calendar.sessions_before) == []


def test_outcome_matching_is_labelled_heuristic():
    tracked = merge([], [cat(1, catalyst_date="2026-09-10")]).catalysts
    outcome = OutcomeRecord(
        provider_record_id=77, catalyst_date=date(2026, 9, 12), stage="Phase 3", drug_name="aaaa-1", text="Met primary endpoint",
        detailed_text=None, source_url=None, news_published_at=None, open_price_gap_percent=None, intra_day_price_change_percent=None,
        provider_created_at=None, provider_updated_at=None, match_basis="", source=SourceRef(provider="BPIQ"),
    )
    result = tracking.attach_outcomes(mode=MODE, catalysts=tracked, outcomes=[outcome], now=SCAN_NOW)
    assert result.catalysts[0].outcome.provider_record_id == 77
    assert "Heuristic" in result.catalysts[0].outcome.match_basis
    far = outcome.model_copy(update={"catalyst_date": date(2026, 10, 30)})
    assert tracking.attach_outcomes(mode=MODE, catalysts=tracked, outcomes=[far], now=SCAN_NOW).catalysts[0].outcome is None


def test_earlier_events_before_reference():
    tracked = merge([], [cat(1, catalyst_date="2026-10-01"), cat(2, catalyst_date="2026-11-20"), cat(3, catalyst_date=None)]).catalysts
    assert tracking.earlier_events(tracked, reference=date(2026, 11, 20), today=SCAN_TODAY) == ["bpiq:catalyst:1"]
