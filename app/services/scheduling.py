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

from datetime import date, datetime, time, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.models import (
    DoseStatus,
    Medication,
    MedicationDose,
    MedicationStatus,
)
from app.utils.timeutil import combine, end_of_day, now_local

# Safety valve against an impossible schedule (a typo in the end date should not
# insert a million rows).
MAX_DOSES_PER_MEDICATION = 20_000


def generate_dose_times(
    start_date: date,
    end_date: date,
    first_dose_time: time,
    frequency_hours: int,
) -> list[datetime]:
    """Every dose datetime for a treatment, in chronological order."""
    if frequency_hours <= 0:
        raise ValueError("frequency_hours must be positive")
    if end_date < start_date:
        raise ValueError("end_date cannot be before start_date")

    first = combine(start_date, first_dose_time)
    limit = end_of_day(end_date)
    step = timedelta(hours=frequency_hours)

    times: list[datetime] = []
    current = first
    while current <= limit:
        times.append(current)
        if len(times) >= MAX_DOSES_PER_MEDICATION:
            raise ValueError("schedule produces too many doses")
        current += step
    return times


def expected_dose_times(medication: Medication) -> list[datetime]:
    return generate_dose_times(
        medication.start_date,
        medication.end_date,
        medication.first_dose_time,
        medication.frequency_hours,
    )


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
        expected = set(expected_dose_times(medication))
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
            db.delete(dose)
            medication.doses.remove(dose)
            removed += 1

    added = 0
    for scheduled_at in sorted(expected):
        if from_time is not None and scheduled_at <= from_time:
            continue
        if scheduled_at in existing:
            continue
        medication.doses.append(
            MedicationDose(
                scheduled_at=scheduled_at,
                status=DoseStatus.SCHEDULED.value,
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
            db.delete(dose)
            medication.doses.remove(dose)
            removed += 1
    db.flush()
    return removed


def mark_overdue_doses_as_missed(db: Session, grace_minutes: int) -> int:
    """A dose left unmarked `grace_minutes` after its time becomes "missed".

    This is the only automatic status change on a dose, and it can only ever
    produce "missed" — never "taken".
    """
    cutoff = now_local() - timedelta(minutes=max(grace_minutes, 0))
    stale = (
        db.execute(
            select(MedicationDose).where(
                MedicationDose.status == DoseStatus.SCHEDULED.value,
                MedicationDose.scheduled_at < cutoff,
            )
        )
        .scalars()
        .all()
    )
    for dose in stale:
        dose.status = DoseStatus.MISSED.value
    if stale:
        db.flush()
    return len(stale)


def complete_finished_medications(db: Session) -> int:
    """Move active treatments whose end date has passed to "completed"."""
    today = now_local().date()
    finished = (
        db.execute(
            select(Medication).where(
                Medication.status == MedicationStatus.ACTIVE.value,
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
