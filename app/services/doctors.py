"""Doctors.

New in v2. A doctor's name, specialty and phone are stored once, here, and an
appointment only keeps a reference — so correcting a phone number fixes it
everywhere at once.

The chain the application models is::

    Doctor -> Appointment -> Medication

A medication is linked to appointments (many-to-many, since the same medication
can be reviewed at several visits), never to a doctor directly.
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.models.models import Appointment, Doctor
from app.services.errors import NotFoundError, ValidationError
from app.utils.timeutil import iso, now_local


def list_doctors(db: Session, search: str | None = None) -> list[Doctor]:
    stmt = select(Doctor).options(selectinload(Doctor.appointments))
    if search:
        needle = f"%{search.strip().lower()}%"
        stmt = stmt.where(
            func.lower(Doctor.name).like(needle)
            | func.lower(func.coalesce(Doctor.occupation, "")).like(needle)
        )
    return list(db.execute(stmt.order_by(Doctor.name)).scalars().unique().all())


def get_doctor(db: Session, doctor_id: int) -> Doctor:
    doctor = db.get(Doctor, doctor_id)
    if doctor is None:
        raise NotFoundError("doctor.not_found")
    return doctor


def _validate(data: dict) -> dict:
    fields: dict[str, str] = {}
    clean: dict = {}

    name = (data.get("name") or "").strip()
    if not name:
        fields["name"] = "validation.doctor_name_required"
    clean["name"] = name[:160]
    clean["occupation"] = (data.get("occupation") or "").strip()[:160] or None
    clean["phone"] = (data.get("phone") or "").strip()[:60] or None
    clean["notes"] = (data.get("notes") or "").strip()[:2000] or None

    if fields:
        raise ValidationError(fields)
    return clean


def create_doctor(db: Session, data: dict) -> Doctor:
    clean = _validate(data)
    doctor = Doctor(**clean)
    db.add(doctor)
    db.flush()
    return doctor


def update_doctor(db: Session, doctor_id: int, data: dict) -> Doctor:
    doctor = get_doctor(db, doctor_id)
    clean = _validate(data)
    for key, value in clean.items():
        setattr(doctor, key, value)
    doctor.updated_at = now_local()
    db.flush()
    return doctor


def delete_doctor(db: Session, doctor_id: int) -> None:
    """Refuse while the doctor still has appointments.

    Silently cascading would delete visit history the user did not ask to lose,
    so the UI is told to explain the situation instead.
    """
    doctor = get_doctor(db, doctor_id)
    count = db.execute(
        select(func.count(Appointment.id)).where(Appointment.doctor_id == doctor.id)
    ).scalar_one()
    if count:
        raise ValidationError(
            {"doctor": "validation.doctor_has_appointments"},
            message_key="validation.doctor_has_appointments",
        )
    db.delete(doctor)
    db.flush()


def serialize_doctor(doctor: Doctor, *, include_appointments: bool = False) -> dict:
    data = {
        "id": doctor.id,
        "name": doctor.name,
        "occupation": doctor.occupation,
        "phone": doctor.phone,
        "notes": doctor.notes,
        "appointment_count": len(doctor.appointments),
        "created_at": iso(doctor.created_at),
    }
    if include_appointments:
        from app.services.appointments import serialize_appointment

        data["appointments"] = [
            serialize_appointment(appointment, include_details=False)
            for appointment in sorted(
                doctor.appointments, key=lambda a: a.scheduled_at, reverse=True
            )
        ]
    return data
