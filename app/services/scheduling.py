"""Dose schedule calculation and dose lifecycle.

This is the heart of the application, so it is kept free of any web or database
framework concern where possible: `generate_dose_times` is a pure function and
is what the tests exercise most.

Rules implemented here
----------------------
* Doses run from `first_dose_time` on `start_date`, every `frequency_hours`,
  up to and including the last slot that still falls on `end_date`.
* All arithmetic is on naive local datetimes, so a dose stays on the same
  wall-clock hour across a daylight-saving change.
* Regeneration never touches a dose that the user (or the "missed" rule)
  already marked, and never touches the past when `from_time` is given.
* Nothing in this module ever sets a dose to "taken" — only the user can.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta

from sqlalchemy import or_, select
from sqlalchemy.orm import Session, selectinload

from app.config import DOSE_HORIZON_DAYS, TAKEN_CONFIRMATION_MINUTES
from app.models.models import (
    DoseStatus,
    Medication,
    MedicationDose,
    MedicationStatus,
)
from app.utils.timeutil import combine, end_of_day, now_local, today_local

# Safety valve against an impossible schedule (a typo in the end date should not
# insert a million rows).
MAX_DOSES_PER_MEDICATION = 20_000

logger = logging.getLogger(__name__)


def horizon_date(reference: date | None = None) -> date:
    """How far ahead an open-ended treatment generates doses."""
    return (reference or today_local()) + timedelta(days=DOSE_HORIZON_DAYS)


def generate_dose_times(
    start_date: date,
    end_date: date | None,
    first_dose_time: time,
    frequency_hours: int,
    horizon: date | None = None,
    not_before: datetime | None = None,
) -> list[datetime]:
    """Every dose datetime for a treatment, in chronological order.

    `end_date=None` means an open-ended treatment: doses are produced up to
    `horizon` (60 days out by default) and the scheduler tops them up as time
    passes, so the table never grows without bound.
    """
    if frequency_hours <= 0:
        raise ValueError("frequency_hours must be positive")
    if end_date is not None and end_date < start_date:
        raise ValueError("end_date cannot be before start_date")

    last_day = end_date if end_date is not None else max(
        horizon or horizon_date(), start_date
    )

    first = combine(start_date, first_dose_time)
    limit = end_of_day(last_day)
    step = timedelta(hours=frequency_hours)

    current = first
    if not_before is not None and not_before > first:
        # Jump straight to the first slot at or after `not_before` instead of
        # walking there one step at a time. Without this, a long-running
        # open-ended treatment would re-walk its whole history on every tick
        # and eventually trip MAX_DOSES_PER_MEDICATION.
        skipped = int((not_before - first) // step)
        current = first + step * skipped
        while current < not_before:
            current += step

    times: list[datetime] = []
    while current <= limit:
        times.append(current)
        if len(times) >= MAX_DOSES_PER_MEDICATION:
            raise ValueError("schedule produces too many doses")
        current += step
    return times


def expected_dose_times(
    medication: Medication,
    horizon: date | None = None,
    not_before: datetime | None = None,
) -> list[datetime]:
    return generate_dose_times(
        medication.start_date,
        medication.end_date,
        medication.first_dose_time,
        medication.frequency_hours,
        horizon=horizon,
        not_before=not_before,
    )


def _purge_notifications(db: Session, dose_ids: list[int]) -> None:
    """A deleted dose takes its notifications with it - SQLite reuses ids."""
    from app.notifications.dispatcher import purge_dose_notifications

    purge_dose_notifications(db, dose_ids)


def registered_at(medication: Medication) -> datetime:
    """The instant the medication started existing in the application.

    This is what separates history from schedule. It is deliberately the
    medication's own `created_at` and not "today": a treatment entered at 12:00
    for a dose that was due at 10:00 the same morning has one historical dose,
    not a whole historical day.
    """
    return medication.created_at or now_local()


def initial_dose_status(scheduled_at: datetime, registered: datetime) -> str:
    """What a freshly generated dose starts as.

    A dose whose time had already passed when the medication was registered was
    never something the application could remind about or the user could mark,
    so it is recorded as history instead of as a pending task. The comparison is
    on the full datetime, not the date: 07:00 and 08:00 are historical for a
    medication added at 08:30, and 09:00 is not.
    """
    if scheduled_at < registered:
        return DoseStatus.BEFORE_REGISTRATION.value
    return DoseStatus.SCHEDULED.value


def rebuild_doses(
    db: Session,
    medication: Medication,
    from_time: datetime | None = None,
) -> tuple[int, int]:
    """Align stored doses with the medication's schedule.

    `from_time=None`  -> full (re)generation, used when a medication is created.
    `from_time=<dt>`  -> only doses strictly after `dt` are added or removed,
                         which is how edits and settings changes stay safe for
                         treatments already in progress.

    Returns `(added, removed)`.
    """
    existing = {dose.scheduled_at: dose for dose in medication.doses}

    if medication.status == MedicationStatus.ACTIVE.value:
        # When only the future is being rebuilt there is no reason to compute
        # the past as well.
        expected = set(expected_dose_times(medication, not_before=from_time))
    else:
        # Suspended / completed treatments keep their history but have no
        # upcoming doses, so nothing is ever notified for them.
        expected = set()

    removed = 0
    for scheduled_at, dose in existing.items():
        if from_time is not None and scheduled_at <= from_time:
            continue
        if dose.status != DoseStatus.SCHEDULED.value:
            continue  # never delete something already taken/skipped/missed
        if scheduled_at not in expected:
            _purge_notifications(db, [dose.id])
            db.delete(dose)
            medication.doses.remove(dose)
            removed += 1

    registered = registered_at(medication)
    added = 0
    for scheduled_at in sorted(expected):
        if from_time is not None and scheduled_at <= from_time:
            continue
        if scheduled_at in existing:
            continue
        medication.doses.append(
            MedicationDose(
                scheduled_at=scheduled_at,
                status=initial_dose_status(scheduled_at, registered),
            )
        )
        added += 1

    db.flush()
    return added, removed


def clear_upcoming_doses(
    db: Session, medication: Medication, from_time: datetime | None = None
) -> int:
    """Remove not-yet-marked doses in the future (used by suspend/complete)."""
    boundary = from_time or now_local()
    removed = 0
    for dose in list(medication.doses):
        if dose.scheduled_at > boundary and dose.status == DoseStatus.SCHEDULED.value:
            _purge_notifications(db, [dose.id])
            db.delete(dose)
            medication.doses.remove(dose)
            removed += 1
    db.flush()
    return removed


def mark_overdue_doses_as_missed(db: Session, grace_minutes: int) -> int:
    """A dose left unmarked `grace_minutes` after its time becomes "missed".

    This is the only automatic status change on a dose, and it can only ever
    produce "missed" — never "taken". The scheduler calls the richer version in
    `app/notifications/dispatcher.py`, which also queues the overdue alert;
    this one stays for direct use and for the tests.
    """
    now = now_local()
    cutoff = now - timedelta(minutes=max(grace_minutes, 0))
    stale = (
        db.execute(
            select(MedicationDose).where(
                MedicationDose.status == DoseStatus.SCHEDULED.value,
                MedicationDose.scheduled_at <= cutoff,
                # A running snooze keeps the dose pending, so the reminder the
                # user asked for is not thrown away.
                or_(
                    MedicationDose.snoozed_until.is_(None),
                    MedicationDose.snoozed_until <= now,
                ),
            )
        )
        .scalars()
        .all()
    )
    for dose in stale:
        dose.status = DoseStatus.MISSED.value
        dose.status_changed_at = now
        dose.snoozed_until = None
    if stale:
        db.flush()
    return len(stale)


def extend_open_ended_schedules(db: Session) -> int:
    """Top up the doses of treatments that have no end date.

    Called on every tick. Only adds what the rolling horizon is missing, so it
    is cheap and does nothing on most passes.
    """
    medications = (
        db.execute(
            select(Medication)
            .options(selectinload(Medication.doses))
            .where(
                Medication.status == MedicationStatus.ACTIVE.value,
                Medication.end_date.is_(None),
            )
        )
        .scalars()
        .unique()
        .all()
    )
    total = 0
    for medication in medications:
        try:
            added, _removed = rebuild_doses(db, medication, from_time=now_local())
            total += added
        except ValueError as exc:
            # One impossible schedule must not take the whole tick down with it.
            logger.warning(
                "Could not extend the schedule of medication %s: %s", medication.id, exc
            )
    return total


def complete_finished_medications(db: Session) -> int:
    """Move active treatments whose end date has passed to "completed"."""
    today = now_local().date()
    finished = (
        db.execute(
            select(Medication).where(
                Medication.status == MedicationStatus.ACTIVE.value,
                Medication.end_date.is_not(None),
                Medication.end_date < today,
            )
        )
        .scalars()
        .all()
    )
    for medication in finished:
        medication.status = MedicationStatus.COMPLETED.value
        medication.completed_at = now_local()
    if finished:
        db.flush()
    return len(finished)


def next_dose_for(medication: Medication, reference: datetime | None = None) -> MedicationDose | None:
    reference = reference or now_local()
    upcoming = [
        dose
        for dose in medication.doses
        if dose.status == DoseStatus.SCHEDULED.value and dose.scheduled_at >= reference
    ]
    return min(upcoming, key=lambda dose: dose.scheduled_at) if upcoming else None


def doses_per_day(frequency_hours: int) -> float:
    return round(24 / frequency_hours, 2)


# --------------------------------------------------------------------------- #
# Confirmation rules (v2)
#
# Both live here, in Python, so they are covered by the test-suite; the
# frontend asks the API for the threshold rather than re-deriving the rule.
# --------------------------------------------------------------------------- #
def taken_confirmation_threshold(scheduled_at: datetime) -> datetime:
    """From this moment on, marking the dose as taken needs no confirmation."""
    return scheduled_at - timedelta(minutes=TAKEN_CONFIRMATION_MINUTES)


def requires_taken_confirmation(
    scheduled_at: datetime, reference: datetime | None = None
) -> bool:
    """True when the dose is being taken more than 30 minutes early.

    `now < scheduled - 30 min`  -> ask
    `now >= scheduled - 30 min` -> just do it (including any time after the
    scheduled hour, however late).
    """
    now = reference or now_local()
    return now < taken_confirmation_threshold(scheduled_at)


def requires_complete_confirmation(
    medication: Medication, reference: date | None = None
) -> bool:
    """True when finishing a treatment early, i.e. before its end date.

    An open-ended treatment (no end date) always asks, because there is no
    natural finishing point to compare against.
    """
    if medication.status != MedicationStatus.ACTIVE.value:
        return False
    today = reference or today_local()
    if medication.end_date is None:
        return True
    return today < medication.end_date
