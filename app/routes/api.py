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

from fastapi import APIRouter, Depends, File, Request, Response, UploadFile
from sqlalchemy.orm import Session

from app.config import (
    ALLOWED_IMAGE_EXTENSIONS,
    APP_VERSION,
    CALENDAR_VIEWS,
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
from app.routes import lock_cache
from app.routes.deps import get_language
from app.services import appointments as appointment_service
from app.services import doctors as doctor_service
from app.services import medications as medication_service
from app.services.today import build_today
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
    """Everything the frontend needs on first paint: catalog + settings.

    Reachable while the application is locked, because the lock screen needs
    its translations — but then it carries the catalog and nothing else. The
    settings include e-mail addresses and schedule details, and none of that is
    anybody's business before the PIN is entered.
    """
    from app.services import applock

    settings = get_settings(db)
    if applock.is_locked(settings, token=request.cookies.get(applock.COOKIE_NAME, "")):
        return {
            "language": language,
            "language_is_explicit": settings.language is not None,
            "catalog": get_catalog(language),
            "settings": None,
            "locked": True,
            "languages": available_languages(),
            "version": APP_VERSION,
        }

    return {
        "language": language,
        "language_is_explicit": settings.language is not None,
        "catalog": get_catalog(language),
        "settings": settings_to_dict(settings),
        "locked": False,
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
@router.get("/today")
def today(db: Session = Depends(get_db)):
    """Everything the home screen needs: doses, appointments, what is next."""
    return build_today(db)


@router.get("/dashboard")
def dashboard(db: Session = Depends(get_db)):
    """Kept so nothing that pointed at the v2 endpoint breaks."""
    return build_today(db)


# --------------------------------------------------------------------------- #
# Calendar
# --------------------------------------------------------------------------- #
@router.get("/calendar")
def calendar(
    view: str = "month",
    anchor: str | None = None,
    scope: str = "all",
    medication_id: int | None = None,
    doctor_id: int | None = None,
    db: Session = Depends(get_db),
):
    """Events for the visible range only - never the whole history."""
    from app.services.calendar_service import build_calendar, parse_anchor, range_for
    from app.utils.timeutil import now_local

    view = view if view in CALENDAR_VIEWS else "month"
    day = parse_anchor(anchor, now_local())
    start, end = range_for(view, day)
    payload = build_calendar(db, start, end, scope, medication_id, doctor_id)
    payload["view"] = view
    payload["anchor"] = day.isoformat()
    return payload


# --------------------------------------------------------------------------- #
# Medical timeline
# --------------------------------------------------------------------------- #
@router.get("/timeline")
def timeline(
    order: str = "newest",
    scope: str = "all",
    doctor_id: int | None = None,
    medication_id: int | None = None,
    limit: int = 100,
    offset: int = 0,
    kind: str = "all",
    db: Session = Depends(get_db),
):
    from app.services.timeline import build_timeline

    return build_timeline(
        db, order, scope, doctor_id, medication_id, limit, offset, kind
    )


# --------------------------------------------------------------------------- #
# Search (read-only)
# --------------------------------------------------------------------------- #
@router.get("/search")
def search_everything(q: str = "", db: Session = Depends(get_db)):
    from app.services.search import search

    return search(db, q)


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


@router.post("/doses/{dose_id}/snooze")
async def snooze_dose(dose_id: int, request: Request, db: Session = Depends(get_db)):
    """Push the reminder back without moving the dose itself."""
    body = await request.json()
    try:
        minutes = int(body.get("minutes", 0))
    except (TypeError, ValueError):
        raise ValidationError({"minutes": "validation.snooze_invalid"}) from None
    dose = medication_service.snooze_dose(db, dose_id, minutes)
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
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    (UPLOAD_DIR / name).write_bytes(content)
    return {"image_path": name, "image_url": f"/api/uploads/{name}"}


@router.get("/uploads/{name}")
def serve_upload(name: str):
    """Serve a medication photograph.

    A route rather than a mounted static folder, because `/static/` is on the
    lock's allow-list — it has to be, or the lock screen would have no
    stylesheet — and a photograph of someone's medication is medical data. Going
    through the API means the same middleware that guards `/api/medications`
    guards the picture attached to it.
    """
    from fastapi.responses import FileResponse

    from app.services.errors import NotFoundError

    candidate = (UPLOAD_DIR / name).resolve()
    try:
        inside = candidate.parent == UPLOAD_DIR.resolve()
    except OSError:
        inside = False
    if not inside or not candidate.is_file():
        raise NotFoundError()
    return FileResponse(candidate)


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


# --------------------------------------------------------------------------- #
# Notification centre
# --------------------------------------------------------------------------- #
@router.get("/notifications/history")
def notifications_history(
    unread_only: bool = False,
    limit: int = 50,
    offset: int = 0,
    db: Session = Depends(get_db),
    language: str = Depends(get_language),
):
    from app.notifications.dispatcher import notification_history

    return notification_history(db, language, unread_only, min(max(limit, 1), 200), max(offset, 0))


@router.get("/notifications/unread-count")
def notifications_unread(db: Session = Depends(get_db)):
    from app.notifications.dispatcher import unread_count

    return {"unread": unread_count(db)}


@router.post("/notifications/read")
async def notifications_read(request: Request, db: Session = Depends(get_db)):
    """Mark the given notifications read, or all of them when ids is omitted."""
    from app.notifications.dispatcher import mark_read, unread_count

    body = await request.json()
    ids = body.get("ids")
    count = mark_read(db, [int(i) for i in ids] if ids else None)
    db.commit()
    return {"ok": True, "marked": count, "unread": unread_count(db)}


# --------------------------------------------------------------------------- #
# Backups
# --------------------------------------------------------------------------- #
@router.get("/backups")
def backups(db: Session = Depends(get_db)):
    from app.services.backup import status as backup_status

    return backup_status(get_settings(db))


@router.post("/backups")
def create_backup_now(db: Session = Depends(get_db)):
    from app.services.backup import MANUAL, create_backup, prune_backups

    settings = get_settings(db)
    try:
        backup = create_backup(settings, MANUAL)
    except Exception as exc:
        # The live database is never touched by a backup attempt, so there is
        # nothing to roll back — only something to record and report. What is
        # recorded is a translation key, never an English sentence: System
        # Status renders it in whichever language the user is reading.
        from app.services.errors import AppError

        settings.last_backup_error = (
            exc.message_key if isinstance(exc, AppError) else "error.backup_failed"
        )
        db.commit()
        raise
    settings.last_backup_at = backup.created_at
    settings.last_backup_error = None
    prune_backups(settings)
    db.commit()
    return {"ok": True, "backup": backup.to_dict(), "message": "message.backup_created"}


@router.post("/backups/restore")
async def restore_backup_route(request: Request, db: Session = Depends(get_db)):
    """Replace the live database with a backup, after copying the current one."""
    from app.services.backup import restore_backup

    body = await request.json()
    name = (body.get("name") or "").strip()
    if not name:
        raise ValidationError({"backup": "validation.backup_not_found"})
    result = restore_backup(db, name)
    return {"ok": True, **result, "message": "message.backup_restored"}


@router.delete("/backups/{name}")
def delete_backup(name: str, db: Session = Depends(get_db)):
    from app.services.backup import find_backup

    settings = get_settings(db)
    target = find_backup(settings, name)
    target.path.unlink()
    return {"ok": True, "message": "message.backup_deleted"}


# --------------------------------------------------------------------------- #
# Export / import
# --------------------------------------------------------------------------- #
@router.post("/export")
async def export_data(
    request: Request, db: Session = Depends(get_db), language: str = Depends(get_language)
):
    """Generate a file and hand back a one-shot download link."""
    from app.services.export_service import cleanup_exports, export

    body = await request.json()
    cleanup_exports()
    path = export(db, (body.get("format") or "json").lower(), body.get("datasets"), language)
    return {
        "ok": True,
        "file": path.name,
        "size": path.stat().st_size,
        "download_url": f"/api/export/{path.name}",
    }


@router.get("/export/{name}")
def download_export(name: str):
    from fastapi.responses import FileResponse

    from app.config import EXPORT_DIR

    candidate = (EXPORT_DIR / Path(name).name).resolve()
    if candidate.parent != EXPORT_DIR.resolve() or not candidate.is_file():
        raise NotFoundError()
    media = {
        ".json": "application/json",
        ".csv": "text/csv; charset=utf-8",
        ".pdf": "application/pdf",
        ".zip": "application/zip",
    }.get(candidate.suffix, "application/octet-stream")
    return FileResponse(candidate, media_type=media, filename=candidate.name)


@router.post("/import/preview")
async def import_preview(file: UploadFile = File(...), db: Session = Depends(get_db)):
    """Validate the file and describe what it would replace. Changes nothing."""
    from app.services.import_service import parse_payload, preview

    payload = parse_payload(await file.read())
    return {"ok": True, "preview": preview(db, payload)}


@router.post("/import")
async def import_data(
    file: UploadFile = File(...),
    include_settings: bool = False,
    db: Session = Depends(get_db),
):
    """Replace the database contents with the file, after a safety backup."""
    from app.services.backup import PRE_IMPORT, create_backup
    from app.services.import_service import apply_import, parse_payload

    payload = parse_payload(await file.read())
    settings = get_settings(db)
    safety = create_backup(settings, PRE_IMPORT)

    result = apply_import(db, payload, include_settings)
    db.commit()
    return {
        "ok": True,
        "imported": result,
        "safety_backup": safety.to_dict(),
        "message": "message.import_done",
    }


@router.get("/health")
def health(db: Session = Depends(get_db)):
    """Small, cheap, and reachable while the application is locked.

    The desktop launcher polls this to find out whether the database and the
    scheduler came up, so it must not depend on either of them succeeding.
    """
    from app.services.system_status import health as collect_health

    return collect_health(db)


@router.get("/system/status")
def system_status(db: Session = Depends(get_db)):
    """The System Status page. Read-only: nothing here sends or writes anything.

    The v3 shape is kept alongside the new one so nothing that read the old
    fields breaks.
    """
    from app.config import DB_PATH
    from app.services.system_status import collect

    payload = collect(db)
    payload.update(
        {
            "version": APP_VERSION,
            "scheduler": background_scheduler.status(),
            "windows_notifications_available": windows_notifier.is_available(),
            "windows_unavailable_reason": windows_notifier.unavailable_reason(),
            "database_path": str(DB_PATH),
            "medication_count": db.query(Medication).count(),
        }
    )
    return payload


# --------------------------------------------------------------------------- #
# App lock
# --------------------------------------------------------------------------- #
def _hand_out_unlock_cookie(response: Response) -> None:
    """Give this browser the proof that it is the one that unlocked.

    A random per-unlock token, HttpOnly so no script can read it, SameSite=strict
    so another site cannot ride on it, and never persisted — it exists only in
    the running process, so closing the application invalidates every copy.
    """
    from app.services import applock

    token = applock.current_token()
    if token:
        response.set_cookie(
            applock.COOKIE_NAME, token,
            httponly=True, samesite="strict", path="/",
        )
    else:
        response.delete_cookie(applock.COOKIE_NAME, path="/")


def _unlock_token(request: Request) -> str:
    from app.services import applock

    return request.cookies.get(applock.COOKIE_NAME, "")


@router.get("/lock/state")
def lock_state(request: Request, db: Session = Depends(get_db)):
    """Reachable while locked - it is what the lock screen reads."""
    from app.services import applock

    settings = get_settings(db)
    state = applock.state(settings, token=_unlock_token(request))
    state["retry_in_seconds"] = applock.seconds_until_retry(settings)
    return state


@router.post("/lock/unlock")
async def lock_unlock(request: Request, response: Response, db: Session = Depends(get_db)):
    from app.services import applock

    body = await request.json()
    settings = get_settings(db)
    state = applock.attempt_unlock(db, settings, body.get("pin"))
    _hand_out_unlock_cookie(response)
    lock_cache.invalidate()
    return {"ok": True, **state}


@router.post("/lock/lock")
def lock_now(db: Session = Depends(get_db)):
    from app.services import applock

    applock.lock()
    lock_cache.invalidate()
    return {"ok": True, **applock.state(get_settings(db))}


@router.post("/lock/enable")
async def lock_enable(request: Request, response: Response, db: Session = Depends(get_db)):
    from app.services import applock

    body = await request.json()
    settings = get_settings(db)
    state = applock.enable(db, settings, body.get("pin"), body.get("confirm_pin"))
    db.commit()
    _hand_out_unlock_cookie(response)
    lock_cache.invalidate()
    return {"ok": True, "message": "message.app_lock_enabled", **state}


@router.post("/lock/change")
async def lock_change(request: Request, response: Response, db: Session = Depends(get_db)):
    from app.services import applock

    body = await request.json()
    settings = get_settings(db)
    state = applock.change(
        db, settings, body.get("current_pin"), body.get("pin"), body.get("confirm_pin")
    )
    db.commit()
    lock_cache.invalidate()
    return {"ok": True, "message": "message.pin_changed", **state}


@router.post("/lock/disable")
async def lock_disable(request: Request, response: Response, db: Session = Depends(get_db)):
    from app.services import applock

    body = await request.json()
    settings = get_settings(db)
    state = applock.disable(db, settings, body.get("current_pin"))
    db.commit()
    _hand_out_unlock_cookie(response)
    lock_cache.invalidate()
    return {"ok": True, "message": "message.app_lock_disabled", **state}


@router.post("/lock/auto")
async def lock_auto(request: Request, db: Session = Depends(get_db)):
    from app.services import applock

    body = await request.json()
    settings = get_settings(db)
    state = applock.set_auto_lock(db, settings, body.get("auto_lock_minutes"))
    db.commit()
    lock_cache.invalidate()
    return {"ok": True, **state}


@router.post("/lock/activity")
def lock_activity():
    """The browser saying a human just did something.

    Auto-lock cannot infer this from traffic: the page polls itself every thirty
    seconds whether anyone is there or not. So the interface reports real input
    — a click, a key — and only that resets the idle clock.
    """
    from app.services import applock

    applock.touch()
    return {"ok": True}


__all__ = ["router", "NotFoundError"]
