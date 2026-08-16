"""Turns due doses and due appointment reminders into notifications.

Flow
----
1. `run_tick()` is called every minute by the background scheduler (and once at
   startup, which is how the app catches up after being closed).
2. Anything due creates a row in `notifications`. That row is the durable
   record: it survives a restart, so a Windows toast is sent exactly once and
   the browser can still pick the reminder up later.
3. Windows toasts are sent immediately from the background thread.
4. The browser polls `/api/notifications/pending` and shows whatever it has not
   displayed yet.

Catch-up policy: when the app has been off for a while, only reminders from the
last `CATCH_UP_WINDOW_MINUTES` are actually shown. Older ones are marked as
handled without notifying, so starting the app after a weekend does not produce
a wall of stale toasts. The doses themselves are still visible (as "missed") in
the UI.
"""

from __future__ import annotations

import json
import logging
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.i18n import t
from app.models.models import (
    Appointment,
    AppointmentReminder,
    DoseStatus,
    Medication,
    MedicationDose,
    MedicationStatus,
    Notification,
    NotificationType,
    ReminderKind,
)
from app.services.scheduling import (
    complete_finished_medications,
    mark_overdue_doses_as_missed,
)
from app.services.settings_service import get_settings
from app.services.textformat import format_datetime, format_dose, format_quantity
from app.utils.timeutil import now_local

logger = logging.getLogger(__name__)

CATCH_UP_WINDOW_MINUTES = 180
# Notifications older than this are no longer offered to the browser.
BROWSER_BACKLOG_MINUTES = 12 * 60


def run_tick(db: Session, *, send_windows: bool = True) -> dict:
    """One scheduler pass. Returns a small summary (used by tests and logs)."""
    settings = get_settings(db)
    summary = {
        "completed_medications": complete_finished_medications(db),
        "missed_doses": mark_overdue_doses_as_missed(db, settings.missed_after_minutes),
        "dose_notifications": 0,
        "appointment_notifications": 0,
        "windows_sent": 0,
    }

    if settings.medication_reminders:
        summary["dose_notifications"] = _queue_dose_notifications(db)
    if settings.appointment_reminders:
        summary["appointment_notifications"] = _queue_appointment_notifications(db)

    if send_windows and settings.windows_notifications:
        summary["windows_sent"] = _send_windows_notifications(db)

    _purge_old_notifications(db)
    db.commit()
    return summary


def _purge_old_notifications(db: Session, keep_days: int = 30) -> int:
    """Keep the notification queue from growing forever."""
    cutoff = now_local() - timedelta(days=keep_days)
    old = (
        db.execute(select(Notification).where(Notification.fire_at < cutoff))
        .scalars()
        .all()
    )
    for row in old:
        db.delete(row)
    if old:
        db.flush()
    return len(old)


# --------------------------------------------------------------------------- #
# Queueing
# --------------------------------------------------------------------------- #
def _queue_dose_notifications(db: Session) -> int:
    now = now_local()
    horizon = now - timedelta(minutes=CATCH_UP_WINDOW_MINUTES)

    due = (
        db.execute(
            select(MedicationDose)
            .options(selectinload(MedicationDose.medication))
            .join(Medication)
            .where(
                MedicationDose.notified_at.is_(None),
                MedicationDose.scheduled_at <= now,
                MedicationDose.status.in_(
                    [DoseStatus.SCHEDULED.value, DoseStatus.MISSED.value]
                ),
                Medication.status == MedicationStatus.ACTIVE.value,
            )
            .order_by(MedicationDose.scheduled_at)
        )
        .scalars()
        .all()
    )

    created = 0
    for dose in due:
        dose.notified_at = now
        if dose.scheduled_at < horizon:
            continue  # too old to be worth showing
        medication = dose.medication
        db.add(
            Notification(
                type=NotificationType.DOSE.value,
                reference_id=dose.id,
                fire_at=dose.scheduled_at,
                title_key="notification.medication_title",
                body_key="notification.medication_body",
                payload=json.dumps(
                    {
                        "medication_id": medication.id,
                        "name": medication.name,
                        "dose_amount": medication.dose_amount,
                        "dose_unit": medication.dose_unit,
                        "quantity": medication.quantity,
                        "form": medication.form,
                    },
                    ensure_ascii=False,
                ),
            )
        )
        created += 1
    if due:
        db.flush()
    return created


_REMINDER_BODY_KEYS = {
    ReminderKind.DAYS_3.value: "notification.appointment_body_days_3",
    ReminderKind.DAY_1.value: "notification.appointment_body_day_1",
    ReminderKind.HOURS_3.value: "notification.appointment_body_hours_3",
}


def _queue_appointment_notifications(db: Session) -> int:
    now = now_local()
    horizon = now - timedelta(minutes=CATCH_UP_WINDOW_MINUTES)

    due = (
        db.execute(
            select(AppointmentReminder)
            .options(selectinload(AppointmentReminder.appointment))
            .join(Appointment)
            .where(
                AppointmentReminder.sent_at.is_(None),
                AppointmentReminder.remind_at <= now,
            )
            .order_by(AppointmentReminder.remind_at)
        )
        .scalars()
        .all()
    )

    created = 0
    for reminder in due:
        # `sent_at` is set for everything that is due, even when nothing is
        # shown, so a reminder is examined exactly once and never sticks in the
        # queue forever.
        reminder.sent_at = now
        appointment = reminder.appointment
        if reminder.remind_at < horizon or appointment.scheduled_at < now:
            continue
        db.add(
            Notification(
                type=NotificationType.APPOINTMENT.value,
                reference_id=appointment.id,
                fire_at=reminder.remind_at,
                title_key="notification.appointment_title",
                body_key=_REMINDER_BODY_KEYS[reminder.kind],
                payload=json.dumps(
                    {
                        "appointment_id": appointment.id,
                        "doctor": appointment.doctor_name,
                        "scheduled_at": appointment.scheduled_at.isoformat(),
                        "kind": reminder.kind,
                    },
                    ensure_ascii=False,
                ),
            )
        )
        created += 1
    if due:
        db.flush()
    return created


# --------------------------------------------------------------------------- #
# Rendering & delivery
# --------------------------------------------------------------------------- #
def render_notification(notification: Notification, language: str) -> dict:
    """Build the title/body of a notification in the requested language."""
    payload = json.loads(notification.payload) if notification.payload else {}
    title = t(notification.title_key, language)

    if notification.type == NotificationType.DOSE.value:
        body = t(
            notification.body_key,
            language,
            name=payload.get("name", ""),
            dose=format_dose(
                payload.get("dose_amount", ""), payload.get("dose_unit", "mg"), language
            ),
            quantity=format_quantity(
                payload.get("quantity", 1), payload.get("form", "other"), language
            ),
        )
    else:
        scheduled_at = payload.get("scheduled_at")
        from app.utils.timeutil import parse_datetime

        when = parse_datetime(scheduled_at) if scheduled_at else None
        body = t(
            notification.body_key,
            language,
            doctor=payload.get("doctor", ""),
            datetime=format_datetime(when, language) if when else "",
        )

    return {
        "id": notification.id,
        "type": notification.type,
        "reference_id": notification.reference_id,
        "fire_at": notification.fire_at.isoformat(),
        "title": title,
        "body": body,
        "payload": payload,
    }


def _system_language() -> str:
    """Language for Windows toasts when Settings is on "automatic".

    There is no browser involved when the toast is sent, so the machine's own
    locale is the closest equivalent.
    """
    import locale

    from app.i18n import normalize_language

    try:
        code = locale.getlocale()[0] or locale.getdefaultlocale()[0] or ""
    except Exception:
        code = ""
    return normalize_language(code.replace("_", "-"))


def _send_windows_notifications(db: Session) -> int:
    from app.notifications import windows

    if not windows.is_available():
        return 0

    settings = get_settings(db)
    language = settings.language or _system_language()

    pending = (
        db.execute(
            select(Notification)
            .where(Notification.windows_sent_at.is_(None))
            .order_by(Notification.fire_at)
            .limit(20)
        )
        .scalars()
        .all()
    )

    horizon = now_local() - timedelta(minutes=CATCH_UP_WINDOW_MINUTES)
    sent = 0
    for notification in pending:
        notification.windows_sent_at = now_local()
        if notification.fire_at < horizon:
            # Stale (the app was closed, or this channel was switched off for a
            # while): mark it handled instead of flooding the desktop.
            continue
        rendered = render_notification(notification, language)
        ok, error = windows.send_toast(rendered["title"], rendered["body"])
        if ok:
            sent += 1
        else:
            notification.error = error
            logger.warning("Windows notification failed: %s", error)
    if pending:
        db.flush()
    return sent


def pending_for_browser(db: Session, language: str) -> list[dict]:
    cutoff = now_local() - timedelta(minutes=BROWSER_BACKLOG_MINUTES)
    rows = (
        db.execute(
            select(Notification)
            .where(
                Notification.browser_delivered_at.is_(None),
                Notification.fire_at >= cutoff,
            )
            .order_by(Notification.fire_at)
            .limit(20)
        )
        .scalars()
        .all()
    )
    return [render_notification(row, language) for row in rows]


def mark_browser_delivered(db: Session, ids: list[int]) -> int:
    if not ids:
        return 0
    rows = (
        db.execute(select(Notification).where(Notification.id.in_(ids)))
        .scalars()
        .all()
    )
    for row in rows:
        row.browser_delivered_at = now_local()
    db.flush()
    return len(rows)
