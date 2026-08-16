"""JSON API.

Conventions
-----------
* Every response is JSON. Errors carry *translation keys*, never sentences and
  never a stack trace: `{"error": "validation.end_before_start", "fields": {...}}`.
* Datetimes are ISO strings in local wall-clock time (no "Z", no offset).
"""

from __future__ import annotations

import secrets
from pathlib import Path

from fastapi import APIRouter, Depends, File, Request, UploadFile
from sqlalchemy.orm import Session

from app.config import (
    ALLOWED_IMAGE_EXTENSIONS,
    APP_VERSION,
    DOSE_NOTIFICATION_OFFSETS,
    FORM_OPTIONS,
    FREQUENCY_OPTIONS,
    MAX_IMAGE_BYTES,
    UNIT_OPTIONS,
    UPLOAD_DIR,
)
from app.database.db import get_db
from app.i18n import available_languages, get_catalog
from app.models.models import Medication
from app.notifications import scheduler as background_scheduler
from app.notifications import windows as windows_notifier
from app.notifications.dispatcher import (
    mark_browser_delivered,
    pending_for_browser,
    run_tick,
)
from app.routes.deps import get_language
from app.services import appointments as appointment_service
from app.services import doctors as doctor_service
from app.services import medications as medication_service
from app.services.dashboard import build_dashboard
from app.services.errors import NotFoundError, ValidationError
from app.services.settings_service import (
    count_active_medications,
    get_settings,
    settings_to_dict,
    update_settings,
)

router = APIRouter(prefix="/api")


# --------------------------------------------------------------------------- #
# Bootstrap / i18n
# --------------------------------------------------------------------------- #
@router.get("/bootstrap")
def bootstrap(
    request: Request, db: Session = Depends(get_db), language: str = Depends(get_language)
):
    """Everything the frontend needs on first paint: catalog + settings."""
    settings = get_settings(db)
    return {
        "language": language,
        "language_is_explicit": settings.language is not None,
        "catalog": get_catalog(language),
        "settings": settings_to_dict(settings),
        "options": {
            "frequencies": list(FREQUENCY_OPTIONS),
            "units": list(UNIT_OPTIONS),
            "forms": list(FORM_OPTIONS),
            "dose_offsets": [kind for kind, _minutes in DOSE_NOTIFICATION_OFFSETS],
        },
        "languages": available_languages(),
        "version": APP_VERSION,
    }


@router.get("/i18n/{language}")
def catalog(language: str):
    return get_catalog(language)


# --------------------------------------------------------------------------- #
# Dashboard
# --------------------------------------------------------------------------- #
@router.get("/dashboard")
def dashboard(db: Session = Depends(get_db)):
    return build_dashboard(db)


# --------------------------------------------------------------------------- #
# Medications
# --------------------------------------------------------------------------- #
@router.get("/medications")
def list_medications(status: str = "all", db: Session = Depends(get_db)):
    items = medication_service.list_medications(db, status)
    return {"items": [medication_service.serialize_medication(item) for item in items]}


@router.get("/medications/{medication_id}")
def get_medication(medication_id: int, db: Session = Depends(get_db)):
    medication = medication_service.get_medication(db, medication_id)
    return medication_service.serialize_medication(medication, include_doses=True)


@router.post("/medications", status_code=201)
async def create_medication(request: Request, db: Session = Depends(get_db)):
    data = await request.json()
    medication = medication_service.create_medication(db, data)
    db.commit()
    db.refresh(medication)
    return medication_service.serialize_medication(medication, include_doses=True)


@router.put("/medications/{medication_id}")
async def update_medication(
    medication_id: int, request: Request, db: Session = Depends(get_db)
):
    data = await request.json()
    medication = medication_service.update_medication(db, medication_id, data)
    db.commit()
    db.refresh(medication)
    return medication_service.serialize_medication(medication, include_doses=True)


@router.delete("/medications/{medication_id}")
def delete_medication(medication_id: int, db: Session = Depends(get_db)):
    medication_service.delete_medication(db, medication_id)
    db.commit()
    return {"ok": True, "message": "message.medication_deleted"}


@router.post("/medications/{medication_id}/suspend")
def suspend_medication(medication_id: int, db: Session = Depends(get_db)):
    medication = medication_service.suspend_medication(db, medication_id)
    db.commit()
    db.refresh(medication)
    return medication_service.serialize_medication(medication)


@router.post("/medications/{medication_id}/resume")
def resume_medication(medication_id: int, db: Session = Depends(get_db)):
    medication = medication_service.resume_medication(db, medication_id)
    db.commit()
    db.refresh(medication)
    return medication_service.serialize_medication(medication)


@router.post("/medications/{medication_id}/complete")
def complete_medication(medication_id: int, db: Session = Depends(get_db)):
    medication = medication_service.complete_medication(db, medication_id)
    db.commit()
    db.refresh(medication)
    return medication_service.serialize_medication(medication)


# --------------------------------------------------------------------------- #
# Doses
# --------------------------------------------------------------------------- #
@router.post("/doses/{dose_id}/status")
async def set_dose_status(dose_id: int, request: Request, db: Session = Depends(get_db)):
    body = await request.json()
    dose = medication_service.set_dose_status(db, dose_id, body.get("status", ""))
    db.commit()
    db.refresh(dose)
    return medication_service.serialize_dose(dose)


# --------------------------------------------------------------------------- #
# Image upload
# --------------------------------------------------------------------------- #
@router.post("/uploads/image")
async def upload_image(file: UploadFile = File(...)):
    extension = Path(file.filename or "").suffix.lower()
    if extension not in ALLOWED_IMAGE_EXTENSIONS:
        raise ValidationError({"image": "validation.image_type"})
    content = await file.read()
    if len(content) > MAX_IMAGE_BYTES:
        raise ValidationError({"image": "validation.image_too_large"})
    name = f"{secrets.token_hex(8)}{extension}"
    (UPLOAD_DIR / name).write_bytes(content)
    return {"image_path": name, "image_url": f"/static/uploads/{name}"}


# --------------------------------------------------------------------------- #
# Appointments
# --------------------------------------------------------------------------- #
@router.get("/appointments")
def list_appointments(
    scope: str = "all", doctor_id: int | None = None, db: Session = Depends(get_db)
):
    items = appointment_service.list_appointments(db, scope, doctor_id)
    return {
        "items": [appointment_service.serialize_appointment(item) for item in items]
    }


@router.get("/appointments/follow-up-options")
def follow_up_options(before: str, exclude: int | None = None, db: Session = Depends(get_db)):
    """Earlier appointments that may be selected as "follow-up of ...".

    `before` is the new appointment's own date and time, so a visit that has
    not happened yet can never appear in the list.
    """
    from app.utils.timeutil import parse_datetime

    try:
        moment = parse_datetime(before)
    except (TypeError, ValueError):
        raise ValidationError({"scheduled_at": "validation.date_invalid"}) from None
    if moment is None:
        raise ValidationError({"scheduled_at": "validation.appointment_datetime_required"})

    items = appointment_service.eligible_follow_up_targets(db, moment, exclude)
    return {
        "items": [
            {
                "id": item.id,
                "doctor_name": item.doctor.name if item.doctor else None,
                "scheduled_at": item.scheduled_at.isoformat(),
                "treatment": item.treatment,
            }
            for item in items
        ]
    }


@router.get("/appointments/{appointment_id}")
def get_appointment(appointment_id: int, db: Session = Depends(get_db)):
    appointment = appointment_service.get_appointment(db, appointment_id)
    return appointment_service.serialize_appointment(appointment)


@router.post("/appointments", status_code=201)
async def create_appointment(request: Request, db: Session = Depends(get_db)):
    data = await request.json()
    appointment = appointment_service.create_appointment(db, data)
    db.commit()
    db.refresh(appointment)
    return appointment_service.serialize_appointment(appointment)


@router.put("/appointments/{appointment_id}")
async def update_appointment(
    appointment_id: int, request: Request, db: Session = Depends(get_db)
):
    data = await request.json()
    appointment = appointment_service.update_appointment(db, appointment_id, data)
    db.commit()
    db.refresh(appointment)
    return appointment_service.serialize_appointment(appointment)


@router.delete("/appointments/{appointment_id}")
def delete_appointment(appointment_id: int, db: Session = Depends(get_db)):
    appointment_service.delete_appointment(db, appointment_id)
    db.commit()
    return {"ok": True, "message": "message.appointment_deleted"}


# --------------------------------------------------------------------------- #
# Doctors
# --------------------------------------------------------------------------- #
@router.get("/doctors")
def list_doctors(search: str | None = None, db: Session = Depends(get_db)):
    items = doctor_service.list_doctors(db, search)
    return {"items": [doctor_service.serialize_doctor(item) for item in items]}


@router.get("/doctors/{doctor_id}")
def get_doctor(doctor_id: int, db: Session = Depends(get_db)):
    doctor = doctor_service.get_doctor(db, doctor_id)
    return doctor_service.serialize_doctor(doctor, include_appointments=True)


@router.post("/doctors", status_code=201)
async def create_doctor(request: Request, db: Session = Depends(get_db)):
    data = await request.json()
    doctor = doctor_service.create_doctor(db, data)
    db.commit()
    db.refresh(doctor)
    return doctor_service.serialize_doctor(doctor)


@router.put("/doctors/{doctor_id}")
async def update_doctor(doctor_id: int, request: Request, db: Session = Depends(get_db)):
    data = await request.json()
    doctor = doctor_service.update_doctor(db, doctor_id, data)
    db.commit()
    db.refresh(doctor)
    return doctor_service.serialize_doctor(doctor)


@router.delete("/doctors/{doctor_id}")
def delete_doctor(doctor_id: int, db: Session = Depends(get_db)):
    doctor_service.delete_doctor(db, doctor_id)
    db.commit()
    return {"ok": True, "message": "message.doctor_deleted"}


# --------------------------------------------------------------------------- #
# Settings
# --------------------------------------------------------------------------- #
@router.get("/settings")
def read_settings(db: Session = Depends(get_db)):
    settings = get_settings(db)
    payload = settings_to_dict(settings)
    payload["active_medication_count"] = count_active_medications(db)
    return payload


@router.put("/settings")
async def write_settings(request: Request, db: Session = Depends(get_db)):
    data = await request.json()
    settings, recalculated = update_settings(db, data)
    db.commit()
    payload = settings_to_dict(settings)
    payload["recalculated_doses"] = recalculated
    payload["active_medication_count"] = count_active_medications(db)
    return payload


# --------------------------------------------------------------------------- #
# Notifications & system
# --------------------------------------------------------------------------- #
@router.get("/notifications/pending")
def notifications_pending(
    db: Session = Depends(get_db), language: str = Depends(get_language)
):
    settings = get_settings(db)
    if not settings.browser_notifications:
        return {"items": []}
    return {"items": pending_for_browser(db, language)}


@router.post("/notifications/delivered")
async def notifications_delivered(request: Request, db: Session = Depends(get_db)):
    body = await request.json()
    count = mark_browser_delivered(db, [int(i) for i in body.get("ids", [])])
    db.commit()
    return {"ok": True, "count": count}


@router.post("/notifications/test")
def notifications_test(
    db: Session = Depends(get_db), language: str = Depends(get_language)
):
    """Send a test Windows toast and report whether it worked."""
    from app.i18n import t

    title = t("notification.test_title", language)
    body = t("notification.test_body", language)
    sent, error = windows_notifier.send_toast(title, body)
    return {"windows_sent": sent, "error": error, "title": title, "body": body}


@router.post("/notifications/test-email")
def notifications_test_email(
    db: Session = Depends(get_db), language: str = Depends(get_language)
):
    """Send a real test message with the SMTP settings currently saved.

    The response carries the technical SMTP error verbatim when it fails,
    because that is what makes a bad host or a rejected password diagnosable —
    the UI shows it next to a translated headline.
    """
    from app.i18n import t
    from app.notifications.email import config_from_settings, send_email

    settings = get_settings(db)
    config = config_from_settings(settings)
    if not config.is_complete:
        return {"sent": False, "error": None, "reason": "validation.email_incomplete"}

    subject = t("email.test_subject", language)
    body = "\n".join(
        [
            t("notification.test_title", language),
            "",
            t("notification.test_body", language),
            "",
            "--",
            t("app.disclaimer_short", language),
        ]
    )
    sent, error = send_email(config, subject, body)
    return {"sent": sent, "error": error, "recipient": config.recipient}


@router.post("/notifications/run-now")
def notifications_run_now(db: Session = Depends(get_db)):
    """Force one scheduler pass (useful for testing reminders)."""
    return run_tick(db)


@router.get("/system/status")
def system_status(db: Session = Depends(get_db)):
    from app.config import DB_PATH

    return {
        "version": APP_VERSION,
        "scheduler": background_scheduler.status(),
        "windows_notifications_available": windows_notifier.is_available(),
        "windows_unavailable_reason": windows_notifier.unavailable_reason(),
        "database_path": str(DB_PATH),
        "medication_count": db.query(Medication).count(),
    }


__all__ = ["router", "NotFoundError"]
