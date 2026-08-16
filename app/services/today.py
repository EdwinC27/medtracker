"""The "Today" view.

Answers one question — *what do I need to do today?* — in one query pass:
every dose scheduled for today in chronological order, today's appointments,
what is next, which treatments are ending, and anything overdue.

It replaces the v2 dashboard payload and keeps the same keys where they meant
the same thing, so nothing that already worked had to be rewritten.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models.models import (
    Appointment,
    DoseStatus,
    Medication,
    MedicationDose,
    MedicationStatus,
)
from app.services.appointments import next_appointment, serialize_appointment
from app.services.medications import serialize_dose, serialize_medication
from app.services.settings_service import get_settings
from app.utils.timeutil import end_of_day, now_local, start_of_day


def build_today(db: Session, reference: datetime | None = None) -> dict:
    now = reference or now_local()
    today = now.date()
    settings = get_settings(db)

    active = list(
        db.execute(
            select(Medication)
            .options(selectinload(Medication.doses), selectinload(Medication.appointments))
            .where(Medication.status == MedicationStatus.ACTIVE.value)
            .order_by(Medication.name)
        )
        .scalars()
        .unique()
        .all()
    )

    todays_doses = list(
        db.execute(
            select(MedicationDose)
            .options(selectinload(MedicationDose.medication))
            .where(
                MedicationDose.scheduled_at >= start_of_day(today),
                MedicationDose.scheduled_at <= end_of_day(today),
                # A dose from before the medication was registered is history,
                # not a task. Showing it here would present the user with
                # something they were never able to do.
                MedicationDose.status != DoseStatus.BEFORE_REGISTRATION.value,
            )
            .order_by(MedicationDose.scheduled_at)
        )
        .scalars()
        .all()
    )

    upcoming_dose = (
        db.execute(
            select(MedicationDose)
            .options(selectinload(MedicationDose.medication))
            .where(
                MedicationDose.status == DoseStatus.SCHEDULED.value,
                MedicationDose.scheduled_at >= now,
            )
            .order_by(MedicationDose.scheduled_at)
            .limit(1)
        )
        .scalars()
        .first()
    )

    overdue = list(
        db.execute(
            select(MedicationDose)
            .options(selectinload(MedicationDose.medication))
            .where(
                MedicationDose.status == DoseStatus.SCHEDULED.value,
                MedicationDose.scheduled_at < now,
            )
            .order_by(MedicationDose.scheduled_at)
        )
        .scalars()
        .all()
    )

    todays_appointments = list(
        db.execute(
            select(Appointment)
            .options(selectinload(Appointment.doctor), selectinload(Appointment.medications))
            .where(
                Appointment.scheduled_at >= start_of_day(today),
                Appointment.scheduled_at <= end_of_day(today),
            )
            .order_by(Appointment.scheduled_at)
        )
        .scalars()
        .unique()
        .all()
    )

    # An open-ended treatment has no end date, so it can never be "ending soon".
    ending_soon = [
        medication
        for medication in active
        if medication.end_date is not None
        and 0 <= (medication.end_date - today).days <= settings.ending_soon_days
    ]

    appointment = next_appointment(db, now)
    taken_today = sum(1 for dose in todays_doses if dose.status == DoseStatus.TAKEN.value)
    pending_today = sum(
        1 for dose in todays_doses if dose.status == DoseStatus.SCHEDULED.value
    )

    return {
        "now": now.isoformat(),
        "date": today.isoformat(),
        "active_medications": [serialize_medication(m, reference=now) for m in active],
        "todays_doses": [serialize_dose(dose) for dose in todays_doses],
        "todays_summary": {
            "taken": taken_today,
            "pending": pending_today,
            "total": len(todays_doses),
        },
        "todays_appointments": [serialize_appointment(a) for a in todays_appointments],
        "next_dose": serialize_dose(upcoming_dose) if upcoming_dose else None,
        "overdue_doses": [serialize_dose(dose) for dose in overdue],
        "ending_soon": [serialize_medication(m, reference=now) for m in ending_soon],
        "next_appointment": serialize_appointment(appointment) if appointment else None,
        "ending_soon_days": settings.ending_soon_days,
    }
