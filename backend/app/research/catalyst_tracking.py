"""Merge fresh provider catalysts into the tracked set and describe what changed.

Pure functions: no I/O. The caller persists the returned catalysts, revisions and notifications.

Rules:
- A catalyst is keyed by its provider record id (`event_id`), never by ticker+date.
- Previous values are preserved in revisions; nothing is overwritten silently.
- "Not returned" is reported only when the provider query was complete and should have covered
  the event (e.g. inside the Apex trial horizon). It is never labelled a cancellation.
- Reminders count calendar days or XNYS trading sessions, as configured.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from app.domain.models import CATALYST_TYPE_LABELS, Catalyst, DataMode
from app.domain.research import (
    CatalystRevision,
    DatePrecision,
    MonitoringSettings,
    Notification,
    OutcomeRecord,
    TrackedCatalyst,
    TrackedStatus,
)


@dataclass
class MergeResult:
    catalysts: list[TrackedCatalyst]
    revisions: list[CatalystRevision] = field(default_factory=list)
    notifications: list[Notification] = field(default_factory=list)


def _type_label(c: TrackedCatalyst | Catalyst) -> str:
    if c.catalyst_type:
        return CATALYST_TYPE_LABELS[c.catalyst_type]
    return c.stage_event_label or " ".join(filter(None, [c.stage_label, c.event_label])) or "Unlabelled catalyst"


def _day(value: date | None) -> str:
    return value.isoformat() if value else "undated"


def notification_id(dedupe_key: str) -> str:
    return hashlib.sha256(dedupe_key.encode()).hexdigest()[:24]


def make_notification(
    *, mode: DataMode, now: datetime, ticker: str, event_id: str | None, kind: str, title: str, body: str, dedupe_key: str
) -> Notification:
    return Notification(
        id=notification_id(f"{mode.value}|{dedupe_key}"),
        mode=mode,
        created_at=now,
        ticker=ticker,
        event_id=event_id,
        kind=kind,  # type: ignore[arg-type]
        title=title,
        body=body,
        dedupe_key=dedupe_key,
    )


def to_tracked(c: Catalyst, *, mode: DataMode, now: datetime, flags: dict[str, bool | None] | None = None) -> TrackedCatalyst:
    return TrackedCatalyst(
        mode=mode,
        event_id=c.event_id,
        provider_record_id=c.provider_record_id,
        ticker=c.ticker,
        drug_name=c.drug_name,
        indications=c.indications,
        stage_label=c.stage_label,
        event_label=c.event_label,
        stage_event_label=c.stage_event_label,
        catalyst_type=c.catalyst_type,
        classification_status=c.classification_status,
        classification_reason=c.classification_reason,
        catalyst_date=c.catalyst_date,
        date_precision=DatePrecision.PROVIDER_DATE if c.catalyst_date else DatePrecision.UNDATED,
        note=c.note,
        source_url=c.source_url,
        provider_flags=flags or {},
        status=TrackedStatus.ACTIVE,
        first_seen_at=now,
        last_seen_at=now,
        source=c.source,
    )


def merge_catalysts(
    *,
    mode: DataMode,
    ticker: str,
    previous: Iterable[TrackedCatalyst],
    fetched: Iterable[Catalyst],
    flags: dict[str, dict[str, bool | None]],
    now: datetime,
    today: date,
    query_complete: bool,
    covered_until: date | None,
) -> MergeResult:
    """Merge one ticker's complete fetch into its tracked catalysts.

    `covered_until` is the last date the provider query could return (None = unbounded).
    """
    prior = {c.event_id: c for c in previous}
    fresh = {c.event_id: c for c in fetched}
    result = MergeResult(catalysts=[])

    for event_id, cat in fresh.items():
        old = prior.get(event_id)
        new = to_tracked(cat, mode=mode, now=now, flags=flags.get(event_id))
        if old is None:
            result.revisions.append(
                CatalystRevision(event_id=event_id, ticker=ticker, observed_at=now, kind="first_seen", current=_day(cat.catalyst_date))
            )
            result.catalysts.append(_with_date_status(new, today))
            continue

        new = new.model_copy(update={"first_seen_at": old.first_seen_at, "outcome": old.outcome})
        if old.catalyst_date != new.catalyst_date:
            result.revisions.append(
                CatalystRevision(
                    event_id=event_id,
                    ticker=ticker,
                    observed_at=now,
                    kind="date_changed",
                    field="catalyst_date",
                    previous=_day(old.catalyst_date),
                    current=_day(new.catalyst_date),
                )
            )
            result.notifications.append(
                make_notification(
                    mode=mode,
                    now=now,
                    ticker=ticker,
                    event_id=event_id,
                    kind="date_changed",
                    title=f"{ticker}: {_type_label(new)} date revised",
                    body=f"Provider date changed from {_day(old.catalyst_date)} to {_day(new.catalyst_date)}.",
                    dedupe_key=f"date|{event_id}|{_day(old.catalyst_date)}|{_day(new.catalyst_date)}",
                )
            )
        if (old.catalyst_type, old.stage_label, old.event_label) != (new.catalyst_type, new.stage_label, new.event_label):
            result.revisions.append(
                CatalystRevision(
                    event_id=event_id,
                    ticker=ticker,
                    observed_at=now,
                    kind="type_changed",
                    field="stage/event",
                    previous=f"{old.stage_label or '—'} / {old.event_label or '—'}",
                    current=f"{new.stage_label or '—'} / {new.event_label or '—'}",
                )
            )
            result.notifications.append(
                make_notification(
                    mode=mode,
                    now=now,
                    ticker=ticker,
                    event_id=event_id,
                    kind="type_changed",
                    title=f"{ticker}: catalyst stage/event relabelled",
                    body=f"{old.stage_label or '—'} / {old.event_label or '—'} → {new.stage_label or '—'} / {new.event_label or '—'}.",
                    dedupe_key=f"type|{event_id}|{old.stage_label}|{old.event_label}|{new.stage_label}|{new.event_label}",
                )
            )
        if (old.note or "") != (new.note or ""):
            result.revisions.append(
                CatalystRevision(
                    event_id=event_id, ticker=ticker, observed_at=now, kind="note_changed", field="note", previous=old.note, current=new.note
                )
            )
        if old.status is TrackedStatus.NOT_RETURNED:
            result.revisions.append(CatalystRevision(event_id=event_id, ticker=ticker, observed_at=now, kind="returned_again"))
            result.notifications.append(
                make_notification(
                    mode=mode,
                    now=now,
                    ticker=ticker,
                    event_id=event_id,
                    kind="returned_again",
                    title=f"{ticker}: catalyst returned by provider again",
                    body=f"{_type_label(new)} ({_day(new.catalyst_date)}) is listed again after being absent.",
                    dedupe_key=f"back|{event_id}|{now.isoformat()}",
                )
            )
        result.catalysts.append(_with_date_status(new, today))

    for event_id, old in prior.items():
        if event_id in fresh:
            continue
        should_be_covered = (
            query_complete
            and old.status is TrackedStatus.ACTIVE
            and (old.catalyst_date is None or old.catalyst_date >= today)
            and (covered_until is None or old.catalyst_date is None or old.catalyst_date <= covered_until)
        )
        if should_be_covered:
            result.revisions.append(
                CatalystRevision(event_id=event_id, ticker=ticker, observed_at=now, kind="not_returned", previous=_day(old.catalyst_date))
            )
            result.notifications.append(
                make_notification(
                    mode=mode,
                    now=now,
                    ticker=ticker,
                    event_id=event_id,
                    kind="not_returned",
                    title=f"{ticker}: catalyst no longer returned by BPIQ",
                    body=(
                        f"{_type_label(old)} ({_day(old.catalyst_date)}) was absent from a complete provider query. "
                        "This may be a cancellation, completion, or a data change; it is not confirmed."
                    ),
                    dedupe_key=f"gone|{event_id}|{_day(old.catalyst_date)}",
                )
            )
            result.catalysts.append(old.model_copy(update={"status": TrackedStatus.NOT_RETURNED, "not_returned_since": now}))
        else:
            # Outside the query's reach (e.g. already dated in the past): keep it unchanged.
            result.catalysts.append(_with_date_status(old, today))

    result.catalysts.sort(key=lambda c: (c.catalyst_date or date.max, c.provider_record_id))
    return result


def _with_date_status(c: TrackedCatalyst, today: date) -> TrackedCatalyst:
    if c.status is TrackedStatus.ACTIVE and c.catalyst_date is not None and c.catalyst_date < today:
        return c.model_copy(update={"status": TrackedStatus.DATE_PASSED})
    return c


def attach_outcomes(
    *, mode: DataMode, catalysts: list[TrackedCatalyst], outcomes: list[OutcomeRecord], now: datetime
) -> MergeResult:
    """Match historical records to tracked catalysts.

    BPIQ documents no link between upcoming and historical record ids, so a match requires the same
    drug name (case-insensitive) and a historical date within 14 calendar days of the tracked date.
    Matches are labelled with their basis and should be verified from the source link.
    """
    result = MergeResult(catalysts=[])
    used: set[int] = set()
    for cat in catalysts:
        if cat.outcome is not None or not cat.drug_name or cat.catalyst_date is None:
            result.catalysts.append(cat)
            continue
        candidates = [
            o
            for o in outcomes
            if o.provider_record_id not in used
            and o.drug_name
            and o.drug_name.strip().lower() == cat.drug_name.strip().lower()
            and o.catalyst_date is not None
            and abs((o.catalyst_date - cat.catalyst_date).days) <= 14
        ]
        if not candidates:
            result.catalysts.append(cat)
            continue
        match = min(candidates, key=lambda o: abs((o.catalyst_date - cat.catalyst_date).days))  # type: ignore[operator]
        used.add(match.provider_record_id)
        match = match.model_copy(
            update={
                "match_basis": (
                    f"Same drug name; historical date {match.catalyst_date} is within 14 days of tracked date "
                    f"{cat.catalyst_date}. Heuristic match — verify with the source."
                )
            }
        )
        result.catalysts.append(cat.model_copy(update={"outcome": match}))
        result.revisions.append(
            CatalystRevision(
                event_id=cat.event_id, ticker=cat.ticker, observed_at=now, kind="outcome_reported", current=match.text
            )
        )
        result.notifications.append(
            make_notification(
                mode=mode,
                now=now,
                ticker=cat.ticker,
                event_id=cat.event_id,
                kind="outcome_reported",
                title=f"{cat.ticker}: possible reported outcome for {cat.drug_name}",
                body=(match.text or "BPIQ historical record found.") + " (Heuristic match — verify with the source.)",
                dedupe_key=f"outcome|{cat.event_id}|{match.provider_record_id}",
            )
        )
    return result


def reminder_date(event_date: date, offset: int, unit: str, sessions_before: Callable[[date, int], date | None]) -> date | None:
    if unit == "calendar":
        return event_date - timedelta(days=offset)
    return sessions_before(event_date, offset)


def due_reminders(
    *,
    mode: DataMode,
    catalysts: Iterable[TrackedCatalyst],
    settings: MonitoringSettings,
    today: date,
    now: datetime,
    sessions_before: Callable[[date, int], date | None],
) -> list[Notification]:
    """Reminders whose trigger date has arrived and whose event is still ahead. Deduplicated by key."""
    out: list[Notification] = []
    for cat in catalysts:
        if cat.catalyst_date is None or cat.catalyst_date < today or cat.status is not TrackedStatus.ACTIVE:
            continue
        for rule in settings.reminder_rules:
            trigger = reminder_date(cat.catalyst_date, rule.offset, rule.unit, sessions_before)
            if trigger is None or trigger > today:
                continue
            unit = "calendar day(s)" if rule.unit == "calendar" else "trading session(s)"
            days_left = (cat.catalyst_date - today).days
            out.append(
                make_notification(
                    mode=mode,
                    now=now,
                    ticker=cat.ticker,
                    event_id=cat.event_id,
                    kind="reminder",
                    title=f"{cat.ticker}: {_type_label(cat)} on {cat.catalyst_date.isoformat()}",
                    body=(
                        f"Reminder set {rule.offset} {unit} before the provider date. "
                        f"{days_left} calendar day(s) remain. Date precision is not documented by BPIQ."
                    ),
                    dedupe_key=f"reminder|{cat.event_id}|{cat.catalyst_date.isoformat()}|{rule.offset}{rule.unit}",
                )
            )
    return out


def earlier_events(catalysts: Iterable[TrackedCatalyst], *, reference: date | None, today: date) -> list[str]:
    """Event ids of upcoming or undated catalysts dated before `reference` (e.g. the planned exit or the primary event)."""
    if reference is None:
        return []
    return [
        c.event_id
        for c in catalysts
        if c.status is TrackedStatus.ACTIVE and c.catalyst_date is not None and today <= c.catalyst_date < reference
    ]
