"""Medical appointments, their reminders and their link to medications."""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models.models import (
    Appointment,
    AppointmentReminder,
    Medication,
    ReminderKind,
)
from app.services.errors import NotFoundError, ValidationError
from app.services.settings_service import get_settings
from app.utils.timeutil import iso, now_local, parse_datetime

# How far before the appointment each reminder fires.
REMINDER_OFFSETS: dict[str, timedelta] = {
    ReminderKind.DAYS_3.value: timedelta(days=3),
    ReminderKind.DAY_1.value: timedelta(days=1),
    ReminderKind.HOURS_3.value: timedelta(hours=3),
}

REMINDER_FIELDS = {
    ReminderKind.DAYS_3.value: "reminder_days_3",
    ReminderKind.DAY_1.value: "reminder_day_1",
    ReminderKind.HOURS_3.value: "reminder_hours_3",
}


def list_appointments(db: Session, scope: str | None = None) -> list[Appointment]:
    stmt = select(Appointment).options(
        selectinload(Appointment.medications), selectinload(Appointment.reminders)
    )
    now = now_local()
    if scope == "upcoming":
        stmt = stmt.where(Appointment.scheduled_at >= now).order_by(Appointment.scheduled_at)
    elif scope == "past":
        stmt = stmt.where(Appointment.scheduled_at < now).order_by(Appointment.scheduled_at.desc())
    else:
        stmt = stmt.order_by(Appointment.scheduled_at.desc())
    return list(db.execute(stmt).scalars().unique().all())


def get_appointment(db: Session, appointment_id: int) -> Appointment:
    appointment = db.get(Appointment, appointment_id)
    if appointment is None:
        raise NotFoundError("appointment.not_found")
    return appointment


def next_appointment(db: Session, reference: datetime | None = None) -> Appointment | None:
    reference = reference or now_local()
    return db.execute(
        select(Appointment)
        .options(selectinload(Appointment.medications))
        .where(Appointment.scheduled_at >= reference)
        .order_by(Appointment.scheduled_at)
        .limit(1)
    ).scalars().first()


def _validate(data: dict) -> dict:
    fields: dict[str, str] = {}
    clean: dict = {}

    doctor = (data.get("doctor_name") or "").strip()
    if not doctor:
        fields["doctor_name"] = "validation.doctor_required"
    clean["doctor_name"] = doctor[:160]

    raw = data.get("scheduled_at")
    if not raw and data.get("date"):
        raw = f"{data.get('date')}T{data.get('time') or '00:00'}"
    try:
        scheduled_at = parse_datetime(raw)
    except (TypeError, ValueError):
        scheduled_at = None
        fields["scheduled_at"] = "validation.date_invalid"
    if scheduled_at is None and "scheduled_at" not in fields:
        fields["scheduled_at"] = "validation.appointment_datetime_required"
    clean["scheduled_at"] = scheduled_at

    try:
        clean["next_appointment_at"] = parse_datetime(data.get("next_appointment_at"))
    except (TypeError, ValueError):
        fields["next_appointment_at"] = "validation.date_invalid"
        clean["next_appointment_at"] = None

    clean["location"] = (data.get("location") or "").strip()[:200] or None
    clean["treatment"] = (data.get("treatment") or "").strip()[:300] or None
    clean["notes"] = (data.get("notes") or "").strip()[:4000] or None

    if fields:
        raise ValidationError(fields)
    return clean


def _as_bool(value, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def create_appointment(db: Session, data: dict) -> Appointment:
    clean = _validate(data)
    settings = get_settings(db)
    appointment = Appointment(
        doctor_name=clean["doctor_name"],
        scheduled_at=clean["scheduled_at"],
        location=clean["location"],
        treatment=clean["treatment"],
        notes=clean["notes"],
        next_appointment_at=clean["next_appointment_at"],
        reminder_days_3=_as_bool(data.get("reminder_days_3"), settings.appt_reminder_days_3),
        reminder_day_1=_as_bool(data.get("reminder_day_1"), settings.appt_reminder_day_1),
        reminder_hours_3=_as_bool(data.get("reminder_hours_3"), settings.appt_reminder_hours_3),
    )
    db.add(appointment)
    db.flush()
    _sync_medication_links(db, appointment, data.get("medication_ids"))
    sync_reminders(db, appointment)
    db.flush()
    return appointment


def update_appointment(db: Session, appointment_id: int, data: dict) -> Appointment:
    appointment = get_appointment(db, appointment_id)
    clean = _validate(data)

    appointment.doctor_name = clean["doctor_name"]
    appointment.scheduled_at = clean["scheduled_at"]
    appointment.location = clean["location"]
    appointment.treatment = clean["treatment"]
    appointment.notes = clean["notes"]
    appointment.next_appointment_at = clean["next_appointment_at"]
    appointment.reminder_days_3 = _as_bool(data.get("reminder_days_3"), appointment.reminder_days_3)
    appointment.reminder_day_1 = _as_bool(data.get("reminder_day_1"), appointment.reminder_day_1)
    appointment.reminder_hours_3 = _as_bool(data.get("reminder_hours_3"), appointment.reminder_hours_3)

    if "medication_ids" in data:
        _sync_medication_links(db, appointment, data.get("medication_ids"))
    sync_reminders(db, appointment)
    appointment.updated_at = now_local()
    db.flush()
    return appointment


def delete_appointment(db: Session, appointment_id: int) -> None:
    appointment = get_appointment(db, appointment_id)
    appointment.medications.clear()
    db.delete(appointment)
    db.flush()


def sync_reminders(db: Session, appointment: Appointment) -> list[AppointmentReminder]:
    """Recreate the reminder rows for an appointment.

    A reminder that already fired keeps its `sent_at`, so moving an appointment
    does not resend a notification the user already saw for the same instant.
    """
    existing = {reminder.kind: reminder for reminder in appointment.reminders}
    for kind, offset in REMINDER_OFFSETS.items():
        enabled = getattr(appointment, REMINDER_FIELDS[kind])
        remind_at = appointment.scheduled_at - offset
        reminder = existing.get(kind)
        if not enabled:
            if reminder is not None:
                db.delete(reminder)
                appointment.reminders.remove(reminder)
            continue
        if reminder is None:
            appointment.reminders.append(
                AppointmentReminder(kind=kind, remind_at=remind_at)
            )
        elif reminder.remind_at != remind_at:
            reminder.remind_at = remind_at
            reminder.sent_at = None
    db.flush()
    return list(appointment.reminders)


def _sync_medication_links(db: Session, appointment: Appointment, medication_ids) -> None:
    if medication_ids is None:
        return
    from app.services.medications import parse_id_list

    ids = parse_id_list(medication_ids, "medication_ids")
    medications = (
        list(db.execute(select(Medication).where(Medication.id.in_(ids))).scalars().all())
        if ids
        else []
    )
    appointment.medications = medications


def serialize_appointment(appointment: Appointment, *, include_details: bool = True) -> dict:
    data = {
        "id": appointment.id,
        "doctor_name": appointment.doctor_name,
        "scheduled_at": iso(appointment.scheduled_at),
        "location": appointment.location,
        "treatment": appointment.treatment,
        "notes": appointment.notes,
        "next_appointment_at": iso(appointment.next_appointment_at),
        "reminder_days_3": appointment.reminder_days_3,
        "reminder_day_1": appointment.reminder_day_1,
        "reminder_hours_3": appointment.reminder_hours_3,
        "is_past": appointment.scheduled_at < now_local(),
        "medications": [
            {
                "id": medication.id,
                "name": medication.name,
                "status": medication.status,
                "dose_amount": medication.dose_amount,
                "dose_unit": medication.dose_unit,
                "quantity": medication.quantity,
                "form": medication.form,
            }
            for medication in appointment.medications
        ],
    }
    if include_details:
        data["reminders"] = [
            {
                "kind": reminder.kind,
                "remind_at": iso(reminder.remind_at),
                "sent_at": iso(reminder.sent_at),
            }
            for reminder in sorted(appointment.reminders, key=lambda r: r.remind_at)
        ]
    return data
