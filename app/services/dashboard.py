"""Everything the dashboard needs, in a single query pass."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models.models import DoseStatus, Medication, MedicationDose, MedicationStatus
from app.services.appointments import next_appointment, serialize_appointment
from app.services.medications import serialize_dose, serialize_medication
from app.services.settings_service import get_settings
from app.utils.timeutil import end_of_day, now_local, start_of_day


def build_dashboard(db: Session) -> dict:
    now = now_local()
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

    ending_soon = [
        medication
        for medication in active
        if 0 <= (medication.end_date - today).days <= settings.ending_soon_days
    ]

    appointment = next_appointment(db, now)
    taken_today = sum(1 for dose in todays_doses if dose.status == DoseStatus.TAKEN.value)

    return {
        "now": now.isoformat(),
        "active_medications": [serialize_medication(medication, reference=now) for medication in active],
        "todays_doses": [serialize_dose(dose) for dose in todays_doses],
        "todays_summary": {"taken": taken_today, "total": len(todays_doses)},
        "next_dose": serialize_dose(upcoming_dose) if upcoming_dose else None,
        "overdue_doses": [serialize_dose(dose) for dose in overdue],
        "ending_soon": [serialize_medication(medication, reference=now) for medication in ending_soon],
        "next_appointment": serialize_appointment(appointment) if appointment else None,
        "ending_soon_days": settings.ending_soon_days,
    }
