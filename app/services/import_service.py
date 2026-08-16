"""Import a previously exported JSON file.

Strategy: **full replace**, chosen deliberately over merging.

An import here means "put this machine back into the state that file
describes" — moving to a new PC, or rolling back. Merging would have to guess
whether two medications called "Amoxicillin" are the same treatment, and a
wrong guess silently corrupts a medical history. Replace has one obvious
meaning, cannot create duplicates, and is fully reversible because a safety
backup is taken before anything is written.

The flow is always: validate -> preview -> confirm -> safety backup -> replace.
"""

from __future__ import annotations

import json
import logging

from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.models.models import (
    Appointment,
    AppointmentMedication,
    AppointmentReminder,
    Doctor,
    Medication,
    MedicationDose,
    Notification,
)
from app.services.errors import ValidationError
from app.services.settings_service import ensure_settings
from app.utils.timeutil import now_local, parse_date, parse_datetime, parse_time

logger = logging.getLogger(__name__)

REQUIRED_KEYS = ("doctors", "medications", "appointments")
SUPPORTED_VERSIONS = (1,)


# --------------------------------------------------------------------------- #
# Validation and preview
# --------------------------------------------------------------------------- #
def parse_payload(raw: str | bytes | dict) -> dict:
    """Turn the uploaded file into a dict, or fail with a translated error."""
    if isinstance(raw, dict):
        payload = raw
    else:
        text = raw.decode("utf-8-sig") if isinstance(raw, bytes) else raw
        try:
            payload = json.loads(text)
        except (ValueError, UnicodeDecodeError):
            raise ValidationError({"file": "validation.import_not_json"}) from None

    if not isinstance(payload, dict):
        raise ValidationError({"file": "validation.import_not_json"})
    if payload.get("format") != "medtracker-export":
        raise ValidationError({"file": "validation.import_wrong_format"})
    if payload.get("version") not in SUPPORTED_VERSIONS:
        raise ValidationError({"file": "validation.import_unsupported_version"})
    for key in REQUIRED_KEYS:
        if not isinstance(payload.get(key), list):
            raise ValidationError({"file": "validation.import_incomplete"})

    _check_references(payload)
    return payload


def _check_references(payload: dict) -> None:
    """Every foreign key in the file must point at something in the file, and
    every row must carry the fields the schema insists on.

    This runs before anything is deleted. A file that only fails halfway through
    the insert would surface as a generic database error after the wipe, which
    reads like a bug in the application rather than a problem with the file.
    """
    doctor_ids = {d.get("id") for d in payload.get("doctors", [])}
    medication_ids = {m.get("id") for m in payload.get("medications", [])}
    appointment_ids = {a.get("id") for a in payload.get("appointments", [])}

    for medication in payload.get("medications", []):
        if not all(
            medication.get(field)
            for field in ("id", "start_date", "frequency_hours", "first_dose_time")
        ):
            raise ValidationError({"file": "validation.import_incomplete"})

    for appointment in payload.get("appointments", []):
        if not appointment.get("id") or not appointment.get("scheduled_at"):
            raise ValidationError({"file": "validation.import_incomplete"})

    for doctor in payload.get("doctors", []):
        if not doctor.get("id"):
            raise ValidationError({"file": "validation.import_incomplete"})

    # The database has a unique index on (medication_id, scheduled_at); two
    # doses in the same slot would fail on insert, after the wipe.
    slots = set()
    for dose in payload.get("medication_doses", []):
        if not dose.get("scheduled_at"):
            raise ValidationError({"file": "validation.import_incomplete"})
        slot = (dose.get("medication_id"), dose.get("scheduled_at"))
        if slot in slots:
            raise ValidationError({"file": "validation.import_duplicate_dose"})
        slots.add(slot)

    for appointment in payload.get("appointments", []):
        if appointment.get("doctor_id") not in doctor_ids:
            raise ValidationError({"file": "validation.import_broken_reference"})
        follow_up = appointment.get("follow_up_of_id")
        if follow_up is not None and follow_up not in appointment_ids:
            raise ValidationError({"file": "validation.import_broken_reference"})

    for dose in payload.get("medication_doses", []):
        if dose.get("medication_id") not in medication_ids:
            raise ValidationError({"file": "validation.import_broken_reference"})

    for link in payload.get("appointment_medications", []):
        if (link.get("appointment_id") not in appointment_ids
                or link.get("medication_id") not in medication_ids):
            raise ValidationError({"file": "validation.import_broken_reference"})


def preview(db: Session, payload: dict) -> dict:
    """What the file holds versus what is here now, so the user can compare."""
    counts = {
        "doctors": len(payload.get("doctors", [])),
        "medications": len(payload.get("medications", [])),
        "medication_doses": len(payload.get("medication_doses", [])),
        "appointments": len(payload.get("appointments", [])),
    }
    current = {
        "doctors": db.query(Doctor).count(),
        "medications": db.query(Medication).count(),
        "medication_doses": db.query(MedicationDose).count(),
        "appointments": db.query(Appointment).count(),
    }
    return {
        "exported_at": payload.get("exported_at"),
        "app_version": payload.get("app_version"),
        "incoming": counts,
        "current": current,
        "includes_settings": "settings" in payload,
        "medication_names": [m.get("name") for m in payload.get("medications", [])][:20],
        "doctor_names": [d.get("name") for d in payload.get("doctors", [])][:20],
    }


# --------------------------------------------------------------------------- #
# Applying
# --------------------------------------------------------------------------- #
def apply_import(db: Session, payload: dict, import_settings: bool = False) -> dict:
    """Replace the contents of the database with the file's.

    The caller is responsible for having taken the safety backup — the API
    route does it, and the tests assert that it happened.
    """
    _check_references(payload)

    # Wipe in dependency order. Notifications go too: they reference dose ids
    # that are about to change meaning.
    db.execute(delete(AppointmentMedication))
    db.execute(delete(AppointmentReminder))
    db.execute(delete(MedicationDose))
    db.execute(delete(Notification))
    db.execute(delete(Appointment))
    db.execute(delete(Medication))
    db.execute(delete(Doctor))
    db.flush()

    for row in payload.get("doctors", []):
        db.add(Doctor(
            id=row.get("id"), name=(row.get("name") or "").strip() or "—",
            occupation=row.get("occupation"), phone=row.get("phone"),
            notes=row.get("notes"),
            created_at=parse_datetime(row.get("created_at")) or now_local(),
            updated_at=now_local(),
        ))

    for row in payload.get("medications", []):
        db.add(Medication(
            id=row.get("id"), name=(row.get("name") or "").strip() or "—",
            image_path=row.get("image_path"),
            dose_amount=row.get("dose_amount"), dose_unit=row.get("dose_unit"),
            quantity=row.get("quantity"), form=row.get("form"),
            comments=row.get("comments"),
            start_date=parse_date(row.get("start_date")),
            end_date=parse_date(row.get("end_date")),
            frequency_hours=int(row.get("frequency_hours") or 8),
            first_dose_time=parse_time(row.get("first_dose_time")) or parse_time("10:00"),
            status=row.get("status") or "active",
            suspended_at=parse_datetime(row.get("suspended_at")),
            completed_at=parse_datetime(row.get("completed_at")),
            created_at=parse_datetime(row.get("created_at")) or now_local(),
            updated_at=now_local(),
        ))
    db.flush()

    # Appointments first without the self-reference, then wire the follow-ups,
    # so the order of rows in the file cannot matter.
    for row in payload.get("appointments", []):
        db.add(Appointment(
            id=row.get("id"), doctor_id=row.get("doctor_id"),
            scheduled_at=parse_datetime(row.get("scheduled_at")),
            location=row.get("location"), treatment=row.get("treatment"),
            notes=row.get("notes"),
            next_appointment_at=parse_datetime(row.get("next_appointment_at")),
            follow_up_of_id=None,
            reminder_days_3=bool(row.get("reminder_days_3", True)),
            reminder_day_1=bool(row.get("reminder_day_1", True)),
            reminder_hours_3=bool(row.get("reminder_hours_3", True)),
            created_at=parse_datetime(row.get("created_at")) or now_local(),
            updated_at=now_local(),
        ))
    db.flush()

    for row in payload.get("appointments", []):
        if row.get("follow_up_of_id"):
            appointment = db.get(Appointment, row.get("id"))
            if appointment is not None:
                appointment.follow_up_of_id = row.get("follow_up_of_id")

    for row in payload.get("medication_doses", []):
        db.add(MedicationDose(
            id=row.get("id"), medication_id=row.get("medication_id"),
            scheduled_at=parse_datetime(row.get("scheduled_at")),
            status=row.get("status") or "scheduled",
            marked_at=parse_datetime(row.get("marked_at")),
            status_changed_at=parse_datetime(row.get("status_changed_at")),
            snoozed_until=parse_datetime(row.get("snoozed_until")),
        ))

    for row in payload.get("appointment_reminders", []):
        db.add(AppointmentReminder(
            appointment_id=row.get("appointment_id"), kind=row.get("kind"),
            remind_at=parse_datetime(row.get("remind_at")),
            sent_at=parse_datetime(row.get("sent_at")),
        ))

    for link in payload.get("appointment_medications", []):
        db.add(AppointmentMedication(
            appointment_id=link.get("appointment_id"),
            medication_id=link.get("medication_id"),
        ))
    db.flush()

    if import_settings and isinstance(payload.get("settings"), dict):
        _apply_settings(db, payload["settings"])

    db.flush()
    logger.info(
        "Import applied: %s doctors, %s medications, %s appointments",
        len(payload.get("doctors", [])), len(payload.get("medications", [])),
        len(payload.get("appointments", [])),
    )
    return {
        "doctors": db.query(Doctor).count(),
        "medications": db.query(Medication).count(),
        "medication_doses": db.query(MedicationDose).count(),
        "appointments": db.query(Appointment).count(),
    }


def _apply_settings(db: Session, incoming: dict) -> None:
    """Only the preferences that travel safely between machines."""
    settings = ensure_settings(db)
    for key in (
        "language", "ending_soon_days", "missed_after_minutes", "theme",
        "notification_history_days", "windows_notifications", "browser_notifications",
        "medication_reminders", "appointment_reminders",
        "appt_reminder_days_3", "appt_reminder_day_1", "appt_reminder_hours_3",
        "dose_before_30", "dose_before_15", "dose_before_5", "dose_at_time",
        "dose_after_15", "dose_after_30", "dose_overdue",
        "backup_enabled", "backup_frequency", "backup_keep",
    ):
        if key in incoming and incoming[key] is not None:
            setattr(settings, key, incoming[key])
    if incoming.get("default_first_dose_time"):
        parsed = parse_time(incoming["default_first_dose_time"])
        if parsed:
            settings.default_first_dose_time = parsed
    if incoming.get("backup_time"):
        parsed = parse_time(incoming["backup_time"])
        if parsed:
            settings.backup_time = parsed
