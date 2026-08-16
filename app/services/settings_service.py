"""Read and update the single settings row."""

from __future__ import annotations

from datetime import time

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import APP_VERSION, DB_PATH, FREQUENCY_OPTIONS
from app.i18n import available_languages, normalize_language
from app.models.models import Medication, MedicationStatus, Settings
from app.services.errors import ValidationError
from app.services.scheduling import rebuild_doses
from app.utils.timeutil import now_local, parse_time

SETTINGS_ID = 1


def ensure_settings(db: Session) -> Settings:
    settings = db.get(Settings, SETTINGS_ID)
    if settings is None:
        settings = Settings(id=SETTINGS_ID, default_first_dose_time=time(10, 0))
        db.add(settings)
        db.flush()
    return settings


def get_settings(db: Session) -> Settings:
    return ensure_settings(db)


def settings_to_dict(settings: Settings) -> dict:
    return {
        "language": settings.language,
        "default_first_dose_time": settings.default_first_dose_time.strftime("%H:%M"),
        "ending_soon_days": settings.ending_soon_days,
        "missed_after_minutes": settings.missed_after_minutes,
        "windows_notifications": settings.windows_notifications,
        "browser_notifications": settings.browser_notifications,
        "medication_reminders": settings.medication_reminders,
        "appointment_reminders": settings.appointment_reminders,
        "appt_reminder_days_3": settings.appt_reminder_days_3,
        "appt_reminder_day_1": settings.appt_reminder_day_1,
        "appt_reminder_hours_3": settings.appt_reminder_hours_3,
        "available_languages": available_languages(),
        "frequency_options": list(FREQUENCY_OPTIONS),
        "database_path": str(DB_PATH),
        "version": APP_VERSION,
    }


def active_language(db: Session, browser_language: str | None) -> str:
    """The language to use right now: the saved choice, else the browser's."""
    settings = get_settings(db)
    if settings.language:
        return normalize_language(settings.language)
    return normalize_language(browser_language)


def _as_bool(value, current: bool) -> bool:
    if value is None:
        return current
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def update_settings(db: Session, data: dict) -> tuple[Settings, int]:
    """Apply a settings patch.

    Returns `(settings, recalculated_doses)`. Changing the global first dose
    time realigns the UPCOMING doses of every active medication (the behaviour
    chosen for this project); doses already marked as taken, skipped or missed
    are never modified, and neither is anything in the past.
    """
    settings = ensure_settings(db)
    fields: dict[str, str] = {}
    recalculated = 0

    if "language" in data:
        raw = data.get("language")
        if raw in (None, "", "auto"):
            settings.language = None
        else:
            normalized = normalize_language(raw)
            if str(raw).lower().split("-")[0] != normalized:
                fields["language"] = "validation.language_invalid"
            else:
                settings.language = normalized

    new_first_dose: time | None = None
    if "default_first_dose_time" in data:
        try:
            parsed = parse_time(data.get("default_first_dose_time"))
        except (ValueError, TypeError):
            parsed = None
            fields["default_first_dose_time"] = "validation.time_invalid"
        if parsed is None and "default_first_dose_time" not in fields:
            fields["default_first_dose_time"] = "validation.first_dose_required"
        elif parsed is not None:
            new_first_dose = parsed

    if "ending_soon_days" in data:
        try:
            days = int(data.get("ending_soon_days"))
            if not 0 <= days <= 60:
                raise ValueError
            settings.ending_soon_days = days
        except (TypeError, ValueError):
            fields["ending_soon_days"] = "validation.ending_soon_range"

    if "missed_after_minutes" in data:
        try:
            minutes = int(data.get("missed_after_minutes"))
            if not 5 <= minutes <= 1440:
                raise ValueError
            settings.missed_after_minutes = minutes
        except (TypeError, ValueError):
            fields["missed_after_minutes"] = "validation.missed_range"

    if fields:
        raise ValidationError(fields)

    for key in (
        "windows_notifications",
        "browser_notifications",
        "medication_reminders",
        "appointment_reminders",
        "appt_reminder_days_3",
        "appt_reminder_day_1",
        "appt_reminder_hours_3",
    ):
        if key in data:
            setattr(settings, key, _as_bool(data.get(key), getattr(settings, key)))

    if new_first_dose is not None and new_first_dose != settings.default_first_dose_time:
        settings.default_first_dose_time = new_first_dose
        recalculated = _realign_active_medications(db, new_first_dose)
    elif new_first_dose is not None:
        settings.default_first_dose_time = new_first_dose

    settings.updated_at = now_local()
    db.flush()
    return settings, recalculated


def _realign_active_medications(db: Session, new_time: time) -> int:
    """Move every active treatment onto the new base hour, future doses only."""
    boundary = now_local()
    medications = (
        db.execute(
            select(Medication).where(Medication.status == MedicationStatus.ACTIVE.value)
        )
        .scalars()
        .all()
    )
    total_added = 0
    for medication in medications:
        if medication.first_dose_time == new_time:
            continue
        medication.first_dose_time = new_time
        added, _removed = rebuild_doses(db, medication, from_time=boundary)
        total_added += added
    return total_added


def count_active_medications(db: Session) -> int:
    return len(
        db.execute(
            select(Medication.id).where(
                Medication.status == MedicationStatus.ACTIVE.value
            )
        )
        .scalars()
        .all()
    )
