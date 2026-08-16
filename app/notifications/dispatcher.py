"""Decides what is due and hands it to the channels.

One scheduler, three channels
-----------------------------
`run_tick()` is the only thing that decides *what* should be announced. It
writes a row in `notifications` for each event, and the channels (Windows
toasts, the browser queue, e-mail) each drain that same table. Adding e-mail in
v2 did not add a second scheduler.

Every dose produces up to seven events, each individually switchable in
Settings::

    -30 min   -15 min   -5 min   dose time   +15 min   +30 min   overdue

Two guarantees
--------------
* **No duplicates across restarts.** Every event carries a `dedupe_key` such as
  ``dose:412:before_15``, protected by a unique index. Re-running a tick, or
  restarting the whole application, can never queue the same event twice.
* **No reminders for a dose you already handled.** Only doses still in the
  `scheduled` state are considered, so marking one Taken or Skipped silently
  cancels every reminder it had left.

Catch-up policy: when the app has been off for a while, only events from the
last `CATCH_UP_WINDOW_MINUTES` are shown. Older ones are recorded as handled
without notifying, so starting the app after a weekend does not produce a wall
of stale alerts.
"""

from __future__ import annotations

import json
import logging
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.config import DOSE_NOTIFICATION_OFFSETS
from app.i18n import t
from app.models.models import (
    Appointment,
    AppointmentReminder,
    DoseNotificationKind,
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
    extend_open_ended_schedules,
)
from app.services.settings_service import get_settings
from app.services.textformat import format_datetime, format_dose, format_quantity, format_time
from app.utils.timeutil import now_local, parse_datetime

logger = logging.getLogger(__name__)

CATCH_UP_WINDOW_MINUTES = 180
BROWSER_BACKLOG_MINUTES = 12 * 60

_OFFSET_MINUTES = dict(DOSE_NOTIFICATION_OFFSETS)


# --------------------------------------------------------------------------- #
# The tick
# --------------------------------------------------------------------------- #
def run_tick(db: Session, *, send_windows: bool = True, send_email: bool = True) -> dict:
    """One scheduler pass. Returns a small summary (used by tests and logs)."""
    settings = get_settings(db)
    summary = {
        "completed_medications": complete_finished_medications(db),
        "extended_schedules": extend_open_ended_schedules(db),
        "missed_doses": 0,
        "dose_notifications": 0,
        "appointment_notifications": 0,
        "windows_sent": 0,
        "emails_sent": 0,
    }

    if settings.medication_reminders:
        summary["dose_notifications"] = _queue_dose_notifications(db, settings)

    # Overdue runs after the offsets so the +15 / +30 reminders of a dose that
    # is about to expire still go out on the same tick.
    summary["missed_doses"] = _mark_overdue_doses(db, settings)

    if settings.appointment_reminders:
        summary["appointment_notifications"] = _queue_appointment_notifications(db)

    # Commit the queue BEFORE anything is delivered. Sending a toast or an
    # e-mail cannot be undone, so the row that records it must already be
    # durable — otherwise a later failure would roll back the bookkeeping for a
    # message the user has already seen, and the next tick would send it again.
    db.commit()

    if send_windows and settings.windows_notifications:
        summary["windows_sent"] = _send_windows_notifications(db, settings)
    if send_email and settings.email_notifications:
        summary["emails_sent"] = _send_email_notifications(db, settings)

    _purge_old_notifications(db)
    db.commit()
    return summary


# --------------------------------------------------------------------------- #
# Queueing: doses
# --------------------------------------------------------------------------- #
def enabled_dose_offsets(settings) -> list[tuple[str, int]]:
    return [
        (kind, minutes)
        for kind, minutes in DOSE_NOTIFICATION_OFFSETS
        if getattr(settings, f"dose_{kind}", True)
    ]


def _dose_payload(dose: MedicationDose, medication: Medication) -> str:
    return json.dumps(
        {
            "dose_id": dose.id,
            "medication_id": medication.id,
            "name": medication.name,
            "dose_amount": medication.dose_amount,
            "dose_unit": medication.dose_unit,
            "quantity": medication.quantity,
            "form": medication.form,
            "scheduled_at": dose.scheduled_at.isoformat(),
        },
        ensure_ascii=False,
    )


def _existing_keys(db: Session, keys: list[str]) -> set[str]:
    if not keys:
        return set()
    found: set[str] = set()
    # Chunked so a long-running instance never builds an oversized IN (...).
    for start in range(0, len(keys), 400):
        chunk = keys[start : start + 400]
        found |= {
            row
            for (row,) in db.execute(
                select(Notification.dedupe_key).where(Notification.dedupe_key.in_(chunk))
            ).all()
        }
    return found


def _queue_dose_notifications(db: Session, settings) -> int:
    now = now_local()
    catch_up_floor = now - timedelta(minutes=CATCH_UP_WINDOW_MINUTES)
    offsets = enabled_dose_offsets(settings)
    if not offsets:
        return 0

    widest_before = -min(minutes for _kind, minutes in offsets)
    widest_after = max(minutes for _kind, minutes in offsets)

    # Only doses whose window can still overlap "now" are worth looking at.
    candidates = (
        db.execute(
            select(MedicationDose)
            .options(selectinload(MedicationDose.medication))
            .join(Medication)
            .where(
                MedicationDose.status == DoseStatus.SCHEDULED.value,
                Medication.status == MedicationStatus.ACTIVE.value,
                MedicationDose.scheduled_at
                >= catch_up_floor - timedelta(minutes=widest_after),
                MedicationDose.scheduled_at <= now + timedelta(minutes=widest_before),
            )
            .order_by(MedicationDose.scheduled_at)
        )
        .scalars()
        .all()
    )
    if not candidates:
        return 0

    planned: list[tuple[MedicationDose, str, int, object]] = []
    for dose in candidates:
        for kind, minutes in offsets:
            fire_at = dose.scheduled_at + timedelta(minutes=minutes)
            if fire_at <= now:
                planned.append((dose, kind, minutes, fire_at))

    known = _existing_keys(db, [f"dose:{d.id}:{k}" for d, k, _m, _f in planned])

    created = 0
    for dose, kind, minutes, fire_at in planned:
        key = f"dose:{dose.id}:{kind}"
        if key in known:
            continue
        known.add(key)
        stale = fire_at < catch_up_floor
        db.add(
            Notification(
                type=NotificationType.DOSE.value,
                kind=kind,
                dedupe_key=key,
                reference_id=dose.id,
                fire_at=fire_at,
                title_key="notification.medication_title",
                body_key="notification.dose_body",
                payload=_dose_payload(dose, dose.medication),
                # Recorded as already handled on every channel: the event is
                # kept for the audit trail but nothing is delivered.
                windows_sent_at=now if stale else None,
                browser_delivered_at=now if stale else None,
                email_sent_at=now if stale else None,
            )
        )
        dose.notified_at = now
        if not stale:
            created += 1
    db.flush()
    return created


def _mark_overdue_doses(db: Session, settings) -> int:
    """Scheduled -> missed once the grace period has passed (2 h by default).

    This is the only automatic status change on a dose, and it can only ever
    produce "missed" - never "taken".
    """
    now = now_local()
    grace = max(int(settings.missed_after_minutes or 0), 0)
    cutoff = now - timedelta(minutes=grace)
    catch_up_floor = now - timedelta(minutes=CATCH_UP_WINDOW_MINUTES)

    stale = (
        db.execute(
            select(MedicationDose)
            .options(selectinload(MedicationDose.medication))
            .where(
                MedicationDose.status == DoseStatus.SCHEDULED.value,
                # "<=" so a dose is overdue exactly when the grace period has
                # elapsed, not a minute later.
                MedicationDose.scheduled_at <= cutoff,
            )
        )
        .scalars()
        .all()
    )
    if not stale:
        return 0

    known = _existing_keys(db, [f"dose:{dose.id}:overdue" for dose in stale])

    for dose in stale:
        dose.status = DoseStatus.MISSED.value
        dose.status_changed_at = now

        if not settings.dose_overdue or not settings.medication_reminders:
            continue
        medication = dose.medication
        if medication is None or medication.status != MedicationStatus.ACTIVE.value:
            continue
        key = f"dose:{dose.id}:overdue"
        if key in known:
            continue
        known.add(key)
        fire_at = dose.scheduled_at + timedelta(minutes=grace)
        was_stale = fire_at < catch_up_floor
        db.add(
            Notification(
                type=NotificationType.DOSE.value,
                kind=DoseNotificationKind.OVERDUE.value,
                dedupe_key=key,
                reference_id=dose.id,
                fire_at=fire_at,
                title_key="notification.medication_title",
                body_key="notification.dose_body",
                payload=_dose_payload(dose, medication),
                windows_sent_at=now if was_stale else None,
                browser_delivered_at=now if was_stale else None,
                email_sent_at=now if was_stale else None,
            )
        )
    db.flush()
    return len(stale)


# --------------------------------------------------------------------------- #
# Queueing: appointments
# --------------------------------------------------------------------------- #
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
            .options(
                selectinload(AppointmentReminder.appointment).selectinload(
                    Appointment.doctor
                )
            )
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
    if not due:
        return 0

    # The instant is part of the key: moving an appointment produces a genuinely
    # new reminder, which the old row must not block.
    def appointment_key(reminder):
        return f"appointment:{reminder.appointment_id}:{reminder.kind}:{reminder.remind_at:%Y%m%d%H%M}"

    known = _existing_keys(db, [appointment_key(r) for r in due])

    created = 0
    for reminder in due:
        # `sent_at` is set for everything that is due, even when nothing is
        # shown, so a reminder is examined exactly once and never sticks in the
        # queue forever.
        reminder.sent_at = now
        appointment = reminder.appointment
        if reminder.remind_at < horizon or appointment.scheduled_at < now:
            continue
        key = appointment_key(reminder)
        if key in known:
            continue
        known.add(key)
        db.add(
            Notification(
                type=NotificationType.APPOINTMENT.value,
                kind=reminder.kind,
                dedupe_key=key,
                reference_id=appointment.id,
                fire_at=reminder.remind_at,
                title_key="notification.appointment_title",
                body_key=_REMINDER_BODY_KEYS[reminder.kind],
                payload=json.dumps(
                    {
                        "appointment_id": appointment.id,
                        "doctor": appointment.doctor.name if appointment.doctor else "",
                        "scheduled_at": appointment.scheduled_at.isoformat(),
                        "location": appointment.location,
                        "treatment": appointment.treatment,
                        "kind": reminder.kind,
                    },
                    ensure_ascii=False,
                ),
            )
        )
        created += 1
    db.flush()
    return created


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #
def _dose_lead(kind: str, language: str) -> str:
    minutes = _OFFSET_MINUTES.get(kind)
    if kind == DoseNotificationKind.OVERDUE.value:
        return t("notification.dose_lead_overdue", language)
    if minutes is None or minutes == 0:
        return t("notification.dose_lead_at", language)
    if minutes < 0:
        return t("notification.dose_lead_before", language, minutes=-minutes)
    return t("notification.dose_lead_after", language, minutes=minutes)


def _dose_summary(payload: dict, language: str) -> str:
    """"500 mg — 1 capsule", or just one half of it if the other is missing."""
    parts = []
    if payload.get("dose_amount"):
        parts.append(format_dose(payload["dose_amount"], payload.get("dose_unit") or "mg", language))
    if payload.get("quantity"):
        parts.append(format_quantity(payload["quantity"], payload.get("form") or "other", language))
    return " — ".join(parts)


def render_notification(notification: Notification, language: str) -> dict:
    """Short form, for a Windows toast or a browser notification."""
    payload = json.loads(notification.payload) if notification.payload else {}
    title = t(notification.title_key, language)

    if notification.type == NotificationType.DOSE.value:
        scheduled = parse_datetime(payload.get("scheduled_at"))
        body = t(
            "notification.dose_body",
            language,
            lead=_dose_lead(notification.kind or "at_time", language),
            name=payload.get("name", ""),
            details=_dose_summary(payload, language),
            time=format_time(scheduled, language) if scheduled else "",
        ).strip()
    else:
        when = parse_datetime(payload.get("scheduled_at"))
        body = t(
            notification.body_key,
            language,
            doctor=payload.get("doctor", ""),
            datetime=format_datetime(when, language) if when else "",
        )

    return {
        "id": notification.id,
        "type": notification.type,
        "kind": notification.kind,
        "reference_id": notification.reference_id,
        "fire_at": notification.fire_at.isoformat(),
        "title": title,
        "body": body,
        "payload": payload,
    }


def render_email(notification: Notification, language: str) -> tuple[str, str]:
    """Long form, for e-mail: `(subject, body)`, fully translated."""
    payload = json.loads(notification.payload) if notification.payload else {}

    if notification.type == NotificationType.DOSE.value:
        scheduled = parse_datetime(payload.get("scheduled_at"))
        name = payload.get("name", "")
        subject = t("email.dose_subject", language, name=name)
        lines = [
            t("notification.medication_title", language),
            "",
            _dose_lead(notification.kind or "at_time", language),
            "",
            name,
        ]
        details = _dose_summary(payload, language)
        if details:
            lines += ["", f"{t('medication.dose', language)}:", details]
        if scheduled:
            lines += [
                "",
                f"{t('notification.scheduled_time', language)}:",
                format_time(scheduled, language),
                format_datetime(scheduled, language),
            ]
        lines += ["", "--", t("app.disclaimer_short", language)]
        return subject, "\n".join(lines)

    when = parse_datetime(payload.get("scheduled_at"))
    doctor = payload.get("doctor", "")
    subject = t("email.appointment_subject", language, doctor=doctor)
    lines = [
        t("notification.appointment_title", language),
        "",
        t(notification.body_key, language, doctor=doctor,
          datetime=format_datetime(when, language) if when else ""),
    ]
    if payload.get("location"):
        lines += ["", f"{t('appointment.location', language)}:", payload["location"]]
    if payload.get("treatment"):
        lines += ["", f"{t('appointment.treatment', language)}:", payload["treatment"]]
    lines += ["", "--", t("app.disclaimer_short", language)]
    return subject, "\n".join(lines)


# --------------------------------------------------------------------------- #
# Delivery
# --------------------------------------------------------------------------- #
def _system_language() -> str:
    """Language for toasts and e-mail when Settings is on "automatic".

    There is no browser involved when these are sent, so the machine's own
    locale is the closest equivalent.
    """
    import locale

    from app.i18n import normalize_language

    try:
        code = locale.getlocale()[0] or locale.getdefaultlocale()[0] or ""
    except Exception:
        code = ""
    return normalize_language(code.replace("_", "-"))


def effective_language(settings) -> str:
    return settings.language or _system_language()


def _pending(db: Session, column, limit: int = 20) -> list[Notification]:
    return list(
        db.execute(
            select(Notification)
            .where(column.is_(None))
            .order_by(Notification.fire_at)
            .limit(limit)
        )
        .scalars()
        .all()
    )


def _send_windows_notifications(db: Session, settings) -> int:
    from app.notifications import windows

    if not windows.is_available():
        return 0

    language = effective_language(settings)
    horizon = now_local() - timedelta(minutes=CATCH_UP_WINDOW_MINUTES)

    sent = 0
    for notification in _pending(db, Notification.windows_sent_at):
        stale = notification.fire_at < horizon
        rendered = None if stale else render_notification(notification, language)

        # Claim it first and commit, so a crash mid-send can only ever lose a
        # notification — never repeat one the user already saw.
        notification.windows_sent_at = now_local()
        db.commit()
        if stale:
            continue  # too old: recorded as handled, nothing shown

        ok, error = windows.send_toast(rendered["title"], rendered["body"])
        if ok:
            sent += 1
        else:
            notification.error = error
            db.commit()
            logger.warning("Windows notification failed: %s", error)
    return sent


def _send_email_notifications(db: Session, settings) -> int:
    from app.notifications.email import config_from_settings, send_email

    config = config_from_settings(settings)
    if not config.is_complete:
        return 0

    language = effective_language(settings)
    horizon = now_local() - timedelta(minutes=CATCH_UP_WINDOW_MINUTES)

    sent = 0
    for notification in _pending(db, Notification.email_sent_at, limit=10):
        stale = notification.fire_at < horizon
        rendered = None if stale else render_email(notification, language)

        notification.email_sent_at = now_local()
        db.commit()
        if stale:
            continue

        ok, error = send_email(config, rendered[0], rendered[1])
        if ok:
            sent += 1
        else:
            notification.error = error
            db.commit()
            logger.warning("E-mail notification failed: %s", error)
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


def cancel_pending_dose_notifications(db: Session, dose_ids: list[int]) -> int:
    """Stop anything still undelivered for these doses.

    Marking a dose Taken keeps it out of future ticks, but a reminder queued a
    moment earlier could still be waiting for the browser to poll, or for the
    next e-mail pass. The rows are marked handled on every channel rather than
    deleted, so the dedupe key survives and the audit trail stays intact.
    """
    if not dose_ids:
        return 0
    now = now_local()
    rows = (
        db.execute(
            select(Notification).where(
                Notification.type == NotificationType.DOSE.value,
                Notification.reference_id.in_(dose_ids),
            )
        )
        .scalars()
        .all()
    )
    cancelled = 0
    for row in rows:
        if row.windows_sent_at and row.browser_delivered_at and row.email_sent_at:
            continue
        row.windows_sent_at = row.windows_sent_at or now
        row.browser_delivered_at = row.browser_delivered_at or now
        row.email_sent_at = row.email_sent_at or now
        cancelled += 1
    if cancelled:
        db.flush()
    return cancelled


def mark_browser_delivered(db: Session, ids: list[int]) -> int:
    if not ids:
        return 0
    rows = db.execute(select(Notification).where(Notification.id.in_(ids))).scalars().all()
    for row in rows:
        row.browser_delivered_at = now_local()
    db.flush()
    return len(rows)


def _purge_old_notifications(db: Session, keep_days: int = 30) -> int:
    """Keep the notification queue from growing forever."""
    cutoff = now_local() - timedelta(days=keep_days)
    old = db.execute(select(Notification).where(Notification.fire_at < cutoff)).scalars().all()
    for row in old:
        db.delete(row)
    if old:
        db.flush()
    return len(old)
