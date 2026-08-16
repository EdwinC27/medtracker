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

from sqlalchemy import and_, delete, func, or_, select, update
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
from app.services.textformat import (
    format_date,
    format_datetime,
    format_dose,
    format_quantity,
    format_time,
)
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
        "snooze_notifications": 0,
        "appointment_notifications": 0,
        "backup": None,
        "windows_sent": 0,
        "emails_sent": 0,
    }

    if settings.medication_reminders:
        summary["dose_notifications"] = _queue_dose_notifications(db, settings)
        summary["snooze_notifications"] = _queue_snooze_notifications(db)

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

    summary["backup"] = _run_backup(db)
    _purge_old_notifications(db, settings.notification_history_days)
    db.commit()
    return summary


def _run_backup(db: Session) -> dict | None:
    """Automatic backups live in the same tick as everything else, so there is
    still exactly one background worker to start and stop."""
    from app.services.backup import run_scheduled_backup

    try:
        return run_scheduled_backup(db)
    except Exception as exc:  # noqa: BLE001 - a failed backup must not stop the tick
        logger.warning("Scheduled backup failed: %s", exc)
        return None


# --------------------------------------------------------------------------- #
# Queueing: doses
# --------------------------------------------------------------------------- #
def enabled_dose_offsets(settings) -> list[tuple[str, int]]:
    return [
        (kind, minutes)
        for kind, minutes in DOSE_NOTIFICATION_OFFSETS
        if getattr(settings, f"dose_{kind}", True)
    ]


def dose_number(db: Session, dose: MedicationDose) -> int:
    """Which dose of this treatment this is: 1 for the first, 2 for the next...

    Counted over the medication's own schedule, so "Dose #14" means the same
    thing every time it is written and does not shift when a later dose is
    added. Ties on the same instant cannot happen (uq_dose_slot), but the id is
    in the comparison anyway so the answer is total.
    """
    return int(
        db.execute(
            select(func.count(MedicationDose.id)).where(
                MedicationDose.medication_id == dose.medication_id,
                or_(
                    MedicationDose.scheduled_at < dose.scheduled_at,
                    and_(
                        MedicationDose.scheduled_at == dose.scheduled_at,
                        MedicationDose.id <= dose.id,
                    ),
                ),
            )
        ).scalar_one()
    )


def _dose_payload(db: Session, dose: MedicationDose, medication: Medication) -> str:
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
            # Written once, when the reminder is queued: the e-mail subject says
            # "Dose #3" and must keep saying the same thing on every message of
            # that conversation.
            "dose_number": dose_number(db, dose),
            "comments": medication.comments,
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
        if dose.snoozed_until and dose.snoozed_until > now:
            # Explicitly asked to be left alone until then.
            continue
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
                payload=_dose_payload(db, dose, dose.medication),
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


def _queue_snooze_notifications(db: Session) -> int:
    """One reminder per elapsed snooze.

    The key carries the snooze instant, so pressing "remind me in 10 minutes"
    twice produces two distinct reminders rather than being swallowed by the
    dedupe index.
    """
    now = now_local()
    horizon = now - timedelta(minutes=CATCH_UP_WINDOW_MINUTES)

    due = (
        db.execute(
            select(MedicationDose)
            .options(selectinload(MedicationDose.medication))
            .join(Medication)
            .where(
                MedicationDose.status == DoseStatus.SCHEDULED.value,
                MedicationDose.snoozed_until.is_not(None),
                MedicationDose.snoozed_until <= now,
                Medication.status == MedicationStatus.ACTIVE.value,
            )
        )
        .scalars()
        .all()
    )
    if not due:
        return 0

    def key_for(dose):
        return f"dose:{dose.id}:snooze:{dose.snoozed_until:%Y%m%d%H%M}"

    known = _existing_keys(db, [key_for(dose) for dose in due])

    created = 0
    for dose in due:
        key = key_for(dose)
        fire_at = dose.snoozed_until
        # The snooze is spent either way; the dose goes back to the normal rules.
        dose.snoozed_until = None
        if key in known:
            continue
        known.add(key)
        stale = fire_at < horizon
        db.add(
            Notification(
                type=NotificationType.DOSE.value,
                kind=DoseNotificationKind.SNOOZE.value,
                dedupe_key=key,
                reference_id=dose.id,
                fire_at=fire_at,
                title_key="notification.medication_title",
                body_key="notification.dose_body",
                payload=_dose_payload(db, dose, dose.medication),
                windows_sent_at=now if stale else None,
                browser_delivered_at=now if stale else None,
                email_sent_at=now if stale else None,
            )
        )
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
                # A snooze the user asked for still has to be honoured. While it
                # is running the dose stays pending, so the reminder it promised
                # can still fire; it turns "missed" on the first tick after the
                # snooze has been spent.
                or_(
                    MedicationDose.snoozed_until.is_(None),
                    MedicationDose.snoozed_until <= now,
                ),
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
        # A spent snooze on a missed dose would otherwise keep showing
        # "snoozed until ..." on a row that is no longer pending.
        dose.snoozed_until = None

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
                payload=_dose_payload(db, dose, medication),
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
    if kind == DoseNotificationKind.SNOOZE.value:
        return t("notification.dose_lead_snooze", language)
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


# One row per moment of a dose: the subject, the heading, the opening line, the
# status word, how long ago it was due, and the sentence that closes it. Keeping
# them in a table rather than a chain of ifs means the seven e-mails of a dose
# are obviously variations of one message, and adding an eighth is one line.
_DOSE_EMAIL = {
    DoseNotificationKind.BEFORE_30.value: {
        "subject": "email.subject_before_30", "heading": "email.heading_reminder",
        "intro": "email.intro_before", "status": "email.status_upcoming",
        "closing": "email.closing_first",
    },
    DoseNotificationKind.BEFORE_15.value: {
        "subject": "email.subject_before_15", "heading": "email.heading_reminder",
        "intro": "email.intro_before", "status": "email.status_upcoming",
        "closing": "email.closing_second",
    },
    DoseNotificationKind.BEFORE_5.value: {
        "subject": "email.subject_before_5", "heading": "email.heading_reminder",
        "intro": "email.intro_before", "status": "email.status_upcoming",
    },
    DoseNotificationKind.AT_TIME.value: {
        "subject": "email.subject_at_time", "heading": "email.heading_time",
        "intro": "email.intro_at_time", "status": "email.status_pending",
        "closing": "email.closing_mark",
    },
    DoseNotificationKind.SNOOZE.value: {
        "subject": "email.subject_snooze", "heading": "email.heading_time",
        "intro": "email.intro_snooze", "status": "email.status_pending",
        "closing": "email.closing_mark",
    },
    DoseNotificationKind.AFTER_15.value: {
        "subject": "email.subject_after_15", "heading": "email.heading_pending",
        "intro": "email.intro_pending", "status": "email.status_pending",
        "elapsed": 15, "closing": "email.closing_update",
    },
    DoseNotificationKind.AFTER_30.value: {
        "subject": "email.subject_after_30", "heading": "email.heading_pending",
        "intro": "email.intro_pending", "status": "email.status_pending",
        "elapsed": 30, "closing": "email.closing_update",
    },
    DoseNotificationKind.OVERDUE.value: {
        "subject": "email.subject_overdue", "heading": "email.heading_overdue",
        "intro": "email.intro_overdue", "status": "email.status_overdue",
        "elapsed": "from_schedule", "closing": "email.closing_review",
    },
}


def render_email(notification: Notification, language: str) -> tuple[str, str]:
    """Long form, for e-mail: `(subject, body)`, fully translated.

    Every string comes from the catalogs and every date and time goes through
    the same formatter the screens use, so an e-mail reads the way the rest of
    the application does in whichever language it is set to.
    """
    payload = json.loads(notification.payload) if notification.payload else {}

    if notification.type == NotificationType.DOSE.value:
        return _render_dose_email(notification, payload, language)

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


def _elapsed_label(fired_at, scheduled, language: str) -> str:
    """"45 minutes" / "2 hours 30 minutes", from the two instants themselves."""
    if scheduled is None or fired_at is None:
        return t("email.elapsed_over_2h", language)
    minutes = max(int((fired_at - scheduled).total_seconds() // 60), 0)
    if minutes < 60:
        return t("email.elapsed_minutes", language, minutes=minutes)
    hours, rest = divmod(minutes, 60)
    if rest == 0:
        return t("email.elapsed_hours", language, hours=hours)
    return t("email.elapsed_hours_minutes", language, hours=hours, minutes=rest)


def _render_dose_email(
    notification: Notification, payload: dict, language: str
) -> tuple[str, str]:
    kind = notification.kind or DoseNotificationKind.AT_TIME.value
    spec = _DOSE_EMAIL.get(kind, _DOSE_EMAIL[DoseNotificationKind.AT_TIME.value])

    name = payload.get("name", "")
    number = payload.get("dose_number") or 1
    scheduled = parse_datetime(payload.get("scheduled_at"))
    clock = format_time(scheduled, language) if scheduled else ""

    subject = t(spec["subject"], language, name=name, number=number)

    # The opening line. "In 30 minutes you have a dose of:" is followed by the
    # name on its own line; the pending/overdue ones are a whole sentence and
    # stand alone.
    minutes = _OFFSET_MINUTES.get(kind)
    if spec["intro"] == "email.intro_before":
        intro = t(spec["intro"], language, minutes=abs(minutes or 0))
    elif spec["intro"] in ("email.intro_pending", "email.intro_overdue"):
        intro = t(spec["intro"], language, name=name, time=clock)
    else:
        intro = t(spec["intro"], language)

    lines = [t(spec["heading"], language), "", intro]
    if spec["intro"] in ("email.intro_before", "email.intro_at_time", "email.intro_snooze"):
        lines += ["", name]

    details = _dose_summary(payload, language)
    if details:
        lines += ["", f"{t('email.label_dose', language)}:", details]
    if scheduled:
        lines += [
            "", f"{t('email.label_scheduled_time', language)}:", clock,
            "", f"{t('email.label_date', language)}:", format_date(scheduled, language),
        ]

    elapsed = spec.get("elapsed")
    if elapsed == "from_schedule":
        # The overdue moment is the `missed_after_minutes` setting, which the
        # user can put anywhere from 5 minutes to a day. Read the real gap off
        # the row instead of asserting "more than 2 hours" at 5 minutes past.
        lines += [
            "",
            f"{t('email.label_elapsed', language)}:",
            _elapsed_label(notification.fire_at, scheduled, language),
        ]
    elif elapsed:
        lines += ["", f"{t('email.label_elapsed', language)}:",
                  t("email.elapsed_minutes", language, minutes=elapsed)]

    lines += ["", f"{t('email.label_status', language)}:", t(spec["status"], language)]

    # The user's own note about the medication, if there is one. Short by
    # design: this is a reminder, not a medical report.
    comments = (payload.get("comments") or "").strip()
    if comments:
        lines += ["", f"{t('email.label_notes', language)}:", comments[:200]]

    if spec.get("closing"):
        lines += ["", t(spec["closing"], language)]

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


def new_message_id_for(config, token: str) -> str:
    """Re-exported so callers do not have to reach into the e-mail module."""
    from app.notifications.email import new_message_id

    return new_message_id(config, token)


def dose_email_thread(db: Session, notification: Notification, config) -> "EmailThread":
    """Where this reminder sits in its dose's conversation.

    One thread per *dose*: every reminder for dose 412 quotes the ones sent
    before it, and dose 413 of the same medication starts a conversation of its
    own. The first reminder actually e-mailed for a dose opens the thread — not
    necessarily the -30 minute one, since that offset can be switched off, or
    the app can have been closed when it was due.
    """
    from app.notifications.email import EmailThread, new_message_id

    references: list[str] = []
    if notification.type == NotificationType.DOSE.value and notification.reference_id:
        # Belt and braces: the dose id identifies the conversation, and the
        # scheduled time confirms it. If an id were ever recycled, the times
        # would not match and the new dose would correctly start fresh.
        mine = json.loads(notification.payload) if notification.payload else {}
        slot = mine.get("scheduled_at")

        rows = db.execute(
            select(Notification.email_message_id, Notification.payload)
            .where(
                Notification.type == NotificationType.DOSE.value,
                Notification.reference_id == notification.reference_id,
                Notification.email_message_id.is_not(None),
                Notification.id != notification.id,
            )
            .order_by(Notification.fire_at, Notification.id)
        ).all()
        for value, payload in rows:
            if not value:
                continue
            other = json.loads(payload) if payload else {}
            if slot and other.get("scheduled_at") and other["scheduled_at"] != slot:
                continue
            references.append(value)

    token = f"dose{notification.reference_id}-n{notification.id}"
    return EmailThread(message_id=new_message_id(config, token), references=references)


# The states that mean "the user has dealt with this dose". Note that `missed`
# is not one of them: the overdue e-mail is *about* a dose that ran out of time,
# and the sweep that sets it runs earlier in the same tick.
_RESOLVED_BY_USER = frozenset(
    {
        DoseStatus.TAKEN.value,
        DoseStatus.SKIPPED.value,
        DoseStatus.BEFORE_REGISTRATION.value,
    }
)


def _dose_still_wants_email(db: Session, notification: Notification) -> bool:
    """Ask the dose, now, instead of trusting the queue.

    A reminder is queued minutes before it is sent, and in between the user may
    have marked the dose. Cancellation already withdraws the queued rows, but
    this is the check that closes the race for good: the state at the moment of
    sending is the one that decides.
    """
    if notification.type != NotificationType.DOSE.value or not notification.reference_id:
        return True
    dose = db.get(MedicationDose, notification.reference_id)
    if dose is None:
        return False
    return dose.status not in _RESOLVED_BY_USER


def _claim_for_email(db: Session, notification: Notification, thread) -> bool:
    """Take ownership of this row for sending. False if someone else already had it."""
    result = db.execute(
        update(Notification)
        .where(Notification.id == notification.id, Notification.email_sent_at.is_(None))
        .values(
            email_sent_at=now_local(),
            email_message_id=thread.message_id if thread is not None else None,
        )
    )
    db.commit()
    if not result.rowcount:
        return False
    db.refresh(notification)
    return True


def _backfill_dose_number(db: Session, notification: Notification) -> None:
    """Give an old queued reminder the dose number its payload predates.

    A row queued by a version before this feature has no `dose_number`, and
    defaulting it to 1 would put the wrong ordinal in the subject — in the same
    conversation as the correctly numbered messages around it.
    """
    if notification.type != NotificationType.DOSE.value or not notification.payload:
        return
    payload = json.loads(notification.payload)
    if payload.get("dose_number") or not notification.reference_id:
        return
    dose = db.get(MedicationDose, notification.reference_id)
    if dose is None:
        return
    payload["dose_number"] = dose_number(db, dose)
    notification.payload = json.dumps(payload, ensure_ascii=False)


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
        # Taken or skipped in the meantime? Then this message has nothing left
        # to say. It is recorded as handled so it never comes back.
        resolved = not stale and not _dose_still_wants_email(db, notification)
        skip = stale or resolved

        if not skip:
            _backfill_dose_number(db, notification)
        rendered = None if skip else render_email(notification, language)
        thread = None if skip else dose_email_thread(db, notification, config)

        # Claim it first and commit, so a crash mid-send can only ever lose a
        # message - never repeat one the user already received. The claim is a
        # conditional UPDATE rather than an attribute assignment: the scheduler
        # thread and a manual "run now" from the browser can be in this loop at
        # the same time, and only one of them may send.
        if not _claim_for_email(db, notification, thread):
            continue
        if skip:
            continue

        ok, error = send_email(config, rendered[0], rendered[1], thread)
        if ok:
            sent += 1
        else:
            # The Message-ID stays on the row even when the send failed: the
            # next reminder of this dose would otherwise open a second thread.
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


def purge_dose_notifications(db: Session, dose_ids: list[int]) -> int:
    """Delete the notifications of doses whose rows are about to disappear.

    Cancelling is not enough here. SQLite hands out `INTEGER PRIMARY KEY` values
    as `max(rowid) + 1`, so a deleted dose's id is handed to the next dose
    created — and a notification row still pointing at that number would then
    belong to a completely different treatment. Two things would follow: its
    `dedupe_key` (`dose:1:before_30`) would suppress the new dose's reminders on
    every channel, and its `Message-ID` would drag the new dose into the deleted
    one's e-mail conversation.

    So when the dose row itself goes, its notifications go with it. This is only
    ever called for doses being deleted; a dose that was taken, skipped or
    missed keeps its history and is merely cancelled.
    """
    if not dose_ids:
        return 0
    removed = 0
    for start in range(0, len(dose_ids), 400):
        chunk = dose_ids[start : start + 400]
        result = db.execute(
            delete(Notification).where(
                Notification.type == NotificationType.DOSE.value,
                Notification.reference_id.in_(chunk),
            )
        )
        removed += result.rowcount or 0
    if removed:
        db.flush()
        logger.info("Removed %s notification(s) of deleted doses", removed)
    return removed


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


# --------------------------------------------------------------------------- #
# Notification centre (v3)
# --------------------------------------------------------------------------- #
def notification_history(
    db: Session, language: str, unread_only: bool = False, limit: int = 50, offset: int = 0
) -> dict:
    """What the bell icon shows: the app's own record, independent of whether
    Windows, the browser or e-mail managed to deliver anything."""
    stmt = select(Notification)
    if unread_only:
        stmt = stmt.where(Notification.read_at.is_(None))
    rows = (
        db.execute(stmt.order_by(Notification.fire_at.desc()).offset(offset).limit(limit))
        .scalars()
        .all()
    )
    items = []
    for row in rows:
        rendered = render_notification(row, language)
        rendered["read"] = row.read_at is not None
        rendered["read_at"] = row.read_at.isoformat() if row.read_at else None
        # Delivery is reported per channel and only when it is actually known.
        rendered["delivery"] = {
            "windows": row.windows_sent_at.isoformat() if row.windows_sent_at else None,
            "browser": row.browser_delivered_at.isoformat() if row.browser_delivered_at else None,
            "email": row.email_sent_at.isoformat() if row.email_sent_at else None,
            "error": row.error,
        }
        items.append(rendered)

    return {
        "items": items,
        "unread": unread_count(db),
        "total": db.query(Notification).count(),
        "offset": offset,
        "limit": limit,
    }


def unread_count(db: Session) -> int:
    return int(
        db.execute(
            select(func.count(Notification.id)).where(Notification.read_at.is_(None))
        ).scalar_one()
    )


def mark_read(db: Session, ids: list[int] | None = None) -> int:
    """Mark the given notifications read, or every unread one when ids is None."""
    now = now_local()
    stmt = select(Notification).where(Notification.read_at.is_(None))
    if ids:
        stmt = stmt.where(Notification.id.in_(ids))
    rows = db.execute(stmt).scalars().all()
    for row in rows:
        row.read_at = now
    if rows:
        db.flush()
    return len(rows)


def _purge_old_notifications(db: Session, keep_days: int = 90) -> int:
    """Keep the notification history from growing forever.

    The window is a setting because the notification centre reads this table:
    shortening it shortens the history the user can scroll back through.
    """
    cutoff = now_local() - timedelta(days=max(int(keep_days or 90), 1))
    old = db.execute(select(Notification).where(Notification.fire_at < cutoff)).scalars().all()
    for row in old:
        db.delete(row)
    if old:
        db.flush()
    return len(old)
