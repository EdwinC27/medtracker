"""Read and update the single settings row."""

from __future__ import annotations

from datetime import time

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import (
    APP_VERSION,
    BACKUP_FREQUENCIES,
    BACKUP_KEEP_OPTIONS,
    DB_PATH,
    DOSE_NOTIFICATION_OFFSETS,
    FREQUENCY_OPTIONS,
    SNOOZE_OPTIONS,
    THEME_OPTIONS,
)
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


BOOLEAN_SETTINGS = (
    "windows_notifications",
    "browser_notifications",
    "email_notifications",
    "medication_reminders",
    "appointment_reminders",
    "appt_reminder_days_3",
    "appt_reminder_day_1",
    "appt_reminder_hours_3",
    "dose_before_30",
    "dose_before_15",
    "dose_before_5",
    "dose_at_time",
    "dose_after_15",
    "dose_after_30",
    "dose_overdue",
    "backup_enabled",
    # v4: the desktop switches. Kept in this list so they save, load and
    # round-trip exactly like every other switch on the page.
    "start_with_windows",
    "network_access",
    "https_enabled",
)


def settings_to_dict(settings: Settings) -> dict:
    from app.utils.secretstore import describe_backend

    data = {
        "language": settings.language,
        "default_first_dose_time": settings.default_first_dose_time.strftime("%H:%M"),
        "ending_soon_days": settings.ending_soon_days,
        "missed_after_minutes": settings.missed_after_minutes,
        # --- e-mail ---
        "email_recipient": settings.email_recipient,
        "email_sender": settings.email_sender,
        "smtp_host": settings.smtp_host,
        "smtp_port": settings.smtp_port,
        "smtp_username": settings.smtp_username,
        # The password itself is never sent to the browser; only whether one is
        # stored, and how it is protected on this machine.
        "smtp_password_set": bool(settings.smtp_password_protected),
        "smtp_security": settings.smtp_security,
        "secret_backend": describe_backend(),
        # --- appearance & history (v3) ---
        "theme": settings.theme,
        "notification_history_days": settings.notification_history_days,
        # --- backups (v3) ---
        "backup_frequency": settings.backup_frequency,
        "backup_time": settings.backup_time.strftime("%H:%M"),
        "backup_keep": settings.backup_keep,
        "backup_location": settings.backup_location,
        "last_backup_at": settings.last_backup_at.isoformat() if settings.last_backup_at else None,
        # --- reference data for the forms ---
        "available_languages": available_languages(),
        "frequency_options": list(FREQUENCY_OPTIONS),
        "dose_offsets": [kind for kind, _minutes in DOSE_NOTIFICATION_OFFSETS],
        "snooze_options": list(SNOOZE_OPTIONS),
        "theme_options": list(THEME_OPTIONS),
        "backup_frequencies": list(BACKUP_FREQUENCIES),
        "backup_keep_options": list(BACKUP_KEEP_OPTIONS),
        "database_path": str(DB_PATH),
        "version": APP_VERSION,
    }
    for key in BOOLEAN_SETTINGS:
        data[key] = bool(getattr(settings, key))

    # v4: what the machine actually does, alongside what the setting says.
    from app.desktop import startup as desktop_startup

    data["startup"] = desktop_startup.read_state().to_dict()
    data["app_lock_enabled"] = bool(settings.app_lock_enabled and settings.pin_hash)
    data["auto_lock_minutes"] = int(settings.auto_lock_minutes or 0)
    return data


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

    if "theme" in data:
        value = (data.get("theme") or "system").strip().lower()
        if value not in THEME_OPTIONS:
            fields["theme"] = "validation.theme_invalid"
        else:
            settings.theme = value

    if "notification_history_days" in data:
        try:
            days = int(data.get("notification_history_days"))
            if not 7 <= days <= 3650:
                raise ValueError
            settings.notification_history_days = days
        except (TypeError, ValueError):
            fields["notification_history_days"] = "validation.history_range"

    if "backup_frequency" in data:
        value = (data.get("backup_frequency") or "daily").strip().lower()
        if value not in BACKUP_FREQUENCIES:
            fields["backup_frequency"] = "validation.backup_frequency_invalid"
        else:
            settings.backup_frequency = value

    if "backup_time" in data:
        try:
            parsed = parse_time(data.get("backup_time"))
        except (TypeError, ValueError):
            parsed = None
        if parsed is None:
            fields["backup_time"] = "validation.time_invalid"
        else:
            settings.backup_time = parsed

    if "backup_keep" in data:
        try:
            keep = int(data.get("backup_keep"))
            if not 1 <= keep <= 365:
                raise ValueError
            settings.backup_keep = keep
        except (TypeError, ValueError):
            fields["backup_keep"] = "validation.backup_keep_range"

    if "backup_location" in data and not fields:
        from app.services.backup import validate_location

        try:
            settings.backup_location = validate_location(data.get("backup_location"))
        except ValidationError as exc:
            fields.update(exc.fields)

    if fields:
        raise ValidationError(fields)

    for key in BOOLEAN_SETTINGS:
        if key in data:
            setattr(settings, key, _as_bool(data.get(key), getattr(settings, key)))

    if "start_with_windows" in data:
        # A stored preference that does nothing would be a lie, so the registry
        # is changed here too. A machine that refuses the write says so instead
        # of silently disagreeing with the switch.
        from app.desktop import startup as desktop_startup

        state = desktop_startup.apply(bool(settings.start_with_windows))
        if state.supported and state.error:
            raise ValidationError({"start_with_windows": "validation.startup_failed"})

    _apply_email_settings(settings, data)

    if new_first_dose is not None and new_first_dose != settings.default_first_dose_time:
        settings.default_first_dose_time = new_first_dose
        recalculated = _realign_active_medications(db, new_first_dose)
    elif new_first_dose is not None:
        settings.default_first_dose_time = new_first_dose

    settings.updated_at = now_local()
    db.flush()
    return settings, recalculated


def _apply_email_settings(settings: Settings, data: dict) -> None:
    """Store the SMTP configuration; the password goes through the secret store.

    Three cases for the password field:
      * absent from the payload  -> leave whatever is stored alone
      * empty string             -> forget the stored password
      * any other value          -> encrypt and replace it
    """
    from app.utils.secretstore import clear as clear_secret
    from app.utils.secretstore import protect

    fields: dict[str, str] = {}

    if "email_recipient" in data:
        value = (data.get("email_recipient") or "").strip()
        if value and not _looks_like_email(value):
            fields["email_recipient"] = "validation.email_invalid"
        settings.email_recipient = value or None

    if "email_sender" in data:
        value = (data.get("email_sender") or "").strip()
        if value and not _looks_like_email(value):
            fields["email_sender"] = "validation.email_invalid"
        settings.email_sender = value or None

    if "smtp_host" in data:
        settings.smtp_host = (data.get("smtp_host") or "").strip()[:200] or None

    if "smtp_port" in data:
        try:
            port = int(data.get("smtp_port") or 587)
            if not 1 <= port <= 65535:
                raise ValueError
            settings.smtp_port = port
        except (TypeError, ValueError):
            fields["smtp_port"] = "validation.port_invalid"

    if "smtp_username" in data:
        settings.smtp_username = (data.get("smtp_username") or "").strip()[:320] or None

    if "smtp_security" in data:
        value = (data.get("smtp_security") or "starttls").strip().lower()
        settings.smtp_security = value if value in {"starttls", "ssl", "none"} else "starttls"

    # Turning the channel on without somewhere to send to is a configuration
    # mistake worth catching at save time rather than at 3 a.m.
    if settings.email_notifications and not (settings.smtp_host and settings.email_recipient):
        fields["email_notifications"] = "validation.email_incomplete"

    # Every check has to pass BEFORE the password is written: `protect()` may
    # touch the filesystem, and a rejected save must not leave a secret behind.
    if fields:
        raise ValidationError(fields)

    if "smtp_password" in data:
        password = data.get("smtp_password")
        if password is None or password == "":
            settings.smtp_password_protected = None
            clear_secret()
        else:
            settings.smtp_password_protected = protect(str(password))


def _looks_like_email(value: str) -> bool:
    return "@" in value and "." in value.split("@")[-1] and len(value) <= 320


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
