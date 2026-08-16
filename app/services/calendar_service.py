"""Calendar events.

Three kinds of event share one shape so the frontend can render them uniformly:

* `dose`        — one scheduled dose of a medication
* `appointment` — a medical appointment
* `treatment`   — the start or the end day of a treatment

Performance
-----------
Every query is bounded to the range the user is actually looking at, and the
range itself is capped at `CALENDAR_MAX_RANGE_DAYS`. Looking at August never
loads September, let alone a year of doses.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from sqlalchemy import or_, select
from sqlalchemy.orm import Session, selectinload

from app.config import CALENDAR_MAX_RANGE_DAYS
from app.models.models import (
    Appointment,
    Medication,
    MedicationDose,
    MedicationStatus,
)
from app.services.errors import ValidationError
from app.utils.timeutil import end_of_day, iso, start_of_day

# What the `filter` query parameter accepts.
FILTERS = ("all", "medications", "appointments", "treatments")


def _clamp_range(start: date, end: date) -> tuple[date, date]:
    if end < start:
        raise ValidationError({"range": "validation.date_invalid"})
    if (end - start).days > CALENDAR_MAX_RANGE_DAYS:
        raise ValidationError({"range": "validation.range_too_wide"})
    return start, end


def build_calendar(
    db: Session,
    start: date,
    end: date,
    scope: str = "all",
    medication_id: int | None = None,
    doctor_id: int | None = None,
) -> dict:
    """Every event between `start` and `end` (both inclusive)."""
    start, end = _clamp_range(start, end)
    scope = scope if scope in FILTERS else "all"
    events: list[dict] = []

    if scope in ("all", "medications"):
        events.extend(_dose_events(db, start, end, medication_id))
    if scope in ("all", "appointments"):
        events.extend(_appointment_events(db, start, end, doctor_id))
    if scope in ("all", "treatments"):
        events.extend(_treatment_events(db, start, end, medication_id))

    events.sort(key=lambda item: (item["date"], item["time"] or "", item["title"]))
    return {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "scope": scope,
        "events": events,
        "counts": {
            kind: sum(1 for event in events if event["type"] == kind)
            for kind in ("dose", "appointment", "treatment")
        },
    }


def _dose_events(
    db: Session, start: date, end: date, medication_id: int | None
) -> list[dict]:
    stmt = (
        select(MedicationDose)
        .options(selectinload(MedicationDose.medication))
        .where(
            MedicationDose.scheduled_at >= start_of_day(start),
            MedicationDose.scheduled_at <= end_of_day(end),
        )
        .order_by(MedicationDose.scheduled_at)
    )
    if medication_id:
        stmt = stmt.where(MedicationDose.medication_id == medication_id)

    events = []
    for dose in db.execute(stmt).scalars().all():
        medication = dose.medication
        events.append(
            {
                "type": "dose",
                "id": dose.id,
                "date": dose.scheduled_at.date().isoformat(),
                "time": dose.scheduled_at.strftime("%H:%M"),
                "datetime": iso(dose.scheduled_at),
                "title": medication.name if medication else "",
                "status": dose.status,
                "snoozed_until": iso(dose.snoozed_until),
                "medication_id": dose.medication_id,
                # Where a click goes.
                "href": f"/medications/{dose.medication_id}",
            }
        )
    return events


def _appointment_events(
    db: Session, start: date, end: date, doctor_id: int | None
) -> list[dict]:
    stmt = (
        select(Appointment)
        .options(selectinload(Appointment.doctor))
        .where(
            Appointment.scheduled_at >= start_of_day(start),
            Appointment.scheduled_at <= end_of_day(end),
        )
        .order_by(Appointment.scheduled_at)
    )
    if doctor_id:
        stmt = stmt.where(Appointment.doctor_id == doctor_id)

    events = []
    for appointment in db.execute(stmt).scalars().unique().all():
        events.append(
            {
                "type": "appointment",
                "id": appointment.id,
                "date": appointment.scheduled_at.date().isoformat(),
                "time": appointment.scheduled_at.strftime("%H:%M"),
                "datetime": iso(appointment.scheduled_at),
                "title": appointment.doctor.name if appointment.doctor else "",
                "subtitle": appointment.treatment,
                "doctor_id": appointment.doctor_id,
                "href": f"/appointments/{appointment.id}",
            }
        )
    return events


def _treatment_events(
    db: Session, start: date, end: date, medication_id: int | None
) -> list[dict]:
    """The day a treatment starts and the day it ends, when they fall in range."""
    stmt = select(Medication).where(
        or_(
            Medication.start_date.between(start, end),
            Medication.end_date.between(start, end),
        )
    )
    if medication_id:
        stmt = stmt.where(Medication.id == medication_id)

    events = []
    for medication in db.execute(stmt).scalars().all():
        if start <= medication.start_date <= end:
            events.append(
                {
                    "type": "treatment",
                    "id": medication.id,
                    "date": medication.start_date.isoformat(),
                    "time": None,
                    "datetime": None,
                    "title": medication.name,
                    "boundary": "start",
                    "status": medication.status,
                    "medication_id": medication.id,
                    "href": f"/medications/{medication.id}",
                }
            )
        if medication.end_date and start <= medication.end_date <= end:
            events.append(
                {
                    "type": "treatment",
                    "id": medication.id,
                    "date": medication.end_date.isoformat(),
                    "time": None,
                    "datetime": None,
                    "title": medication.name,
                    "boundary": "end",
                    "status": medication.status,
                    "medication_id": medication.id,
                    "href": f"/medications/{medication.id}",
                }
            )
    return events


# --------------------------------------------------------------------------- #
# Range helpers, so the frontend and the tests agree on what a "month" is
# --------------------------------------------------------------------------- #
def month_range(anchor: date) -> tuple[date, date]:
    """The whole grid a month view shows: complete weeks, Monday to Sunday."""
    first = anchor.replace(day=1)
    if first.month == 12:
        next_first = first.replace(year=first.year + 1, month=1)
    else:
        next_first = first.replace(month=first.month + 1)
    last = next_first - timedelta(days=1)
    return first - timedelta(days=first.weekday()), last + timedelta(days=6 - last.weekday())


def week_range(anchor: date) -> tuple[date, date]:
    monday = anchor - timedelta(days=anchor.weekday())
    return monday, monday + timedelta(days=6)


def day_range(anchor: date) -> tuple[date, date]:
    return anchor, anchor


def range_for(view: str, anchor: date) -> tuple[date, date]:
    if view == "week":
        return week_range(anchor)
    if view == "day":
        return day_range(anchor)
    return month_range(anchor)


def parse_anchor(value: str | None, fallback: datetime) -> date:
    if not value:
        return fallback.date()
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        raise ValidationError({"anchor": "validation.date_invalid"}) from None
