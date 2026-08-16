"""The background scheduler: does it find what is due, and only once?"""

from __future__ import annotations

from datetime import timedelta

from app.models.models import DoseStatus, Notification, NotificationType
from app.notifications.dispatcher import (
    mark_browser_delivered,
    pending_for_browser,
    render_notification,
    run_tick,
)
from app.services import appointments as appointment_service
from app.services import medications as medication_service
from app.services.settings_service import get_settings
from app.utils.timeutil import now_local
from tests.test_appointments import make_appointment
from tests.test_medications import make_payload


def test_tick_queues_a_notification_for_a_due_dose(db):
    today = now_local().date()
    medication = medication_service.create_medication(
        db,
        make_payload(
            start_date=today.isoformat(),
            end_date=(today + timedelta(days=1)).isoformat(),
            first_dose_time=(now_local() - timedelta(minutes=5)).strftime("%H:%M"),
        ),
    )
    db.commit()

    summary = run_tick(db, send_windows=False)
    assert summary["dose_notifications"] >= 1

    queued = db.query(Notification).filter_by(type=NotificationType.DOSE.value).all()
    assert queued
    assert queued[0].reference_id in {dose.id for dose in medication.doses}


def test_a_dose_is_never_notified_twice(db):
    today = now_local().date()
    medication_service.create_medication(
        db,
        make_payload(
            start_date=today.isoformat(),
            end_date=today.isoformat(),
            first_dose_time=(now_local() - timedelta(minutes=5)).strftime("%H:%M"),
        ),
    )
    db.commit()

    first = run_tick(db, send_windows=False)["dose_notifications"]
    second = run_tick(db, send_windows=False)["dose_notifications"]

    assert first >= 1
    assert second == 0


def test_suspended_medications_do_not_notify(db):
    today = now_local().date()
    medication = medication_service.create_medication(
        db,
        make_payload(
            start_date=today.isoformat(),
            end_date=(today + timedelta(days=2)).isoformat(),
            first_dose_time=(now_local() - timedelta(minutes=5)).strftime("%H:%M"),
        ),
    )
    medication_service.suspend_medication(db, medication.id)
    for dose in medication.doses:
        dose.notified_at = None
    db.commit()

    assert run_tick(db, send_windows=False)["dose_notifications"] == 0


def test_reminders_can_be_disabled_globally(db):
    today = now_local().date()
    medication_service.create_medication(
        db,
        make_payload(
            start_date=today.isoformat(),
            end_date=today.isoformat(),
            first_dose_time=(now_local() - timedelta(minutes=5)).strftime("%H:%M"),
        ),
    )
    settings = get_settings(db)
    settings.medication_reminders = False
    db.commit()

    assert run_tick(db, send_windows=False)["dose_notifications"] == 0


def test_appointment_reminder_fires_when_due(db):
    # Appointment in 23.5 h -> the "1 day before" reminder was due 30 min ago.
    appointment = make_appointment(db, when=now_local() + timedelta(hours=23, minutes=30))
    db.commit()
    summary = run_tick(db, send_windows=False)
    assert summary["appointment_notifications"] >= 1

    queued = db.query(Notification).filter_by(type=NotificationType.APPOINTMENT.value).all()
    assert queued
    assert queued[0].reference_id == appointment.id


def test_appointment_reminder_is_not_repeated(db):
    make_appointment(db, when=now_local() + timedelta(hours=23, minutes=30))
    db.commit()
    run_tick(db, send_windows=False)
    assert run_tick(db, send_windows=False)["appointment_notifications"] == 0


def test_notification_text_is_rendered_in_both_languages(db):
    today = now_local().date()
    medication_service.create_medication(
        db,
        make_payload(
            name="Amoxicillin",
            dose_amount="500",
            dose_unit="mg",
            quantity=1,
            form="capsule",
            start_date=today.isoformat(),
            end_date=today.isoformat(),
            first_dose_time=(now_local() - timedelta(minutes=5)).strftime("%H:%M"),
        ),
    )
    db.commit()
    run_tick(db, send_windows=False)

    notification = db.query(Notification).first()

    english = render_notification(notification, "en")
    assert english["title"] == "Medication reminder"
    assert "Amoxicillin" in english["body"]
    assert "500 mg" in english["body"]
    assert "1 capsule" in english["body"]

    spanish = render_notification(notification, "es")
    assert spanish["title"] == "Recordatorio de medicamento"
    assert "1 cápsula" in spanish["body"]


def test_browser_queue_is_emptied_once_delivered(db):
    today = now_local().date()
    medication_service.create_medication(
        db,
        make_payload(
            start_date=today.isoformat(),
            end_date=today.isoformat(),
            first_dose_time=(now_local() - timedelta(minutes=5)).strftime("%H:%M"),
        ),
    )
    db.commit()
    run_tick(db, send_windows=False)

    pending = pending_for_browser(db, "en")
    assert pending

    mark_browser_delivered(db, [item["id"] for item in pending])
    db.commit()

    assert pending_for_browser(db, "en") == []


def test_stale_reminders_are_not_shown_after_a_long_shutdown(db):
    """A dose from days ago is marked handled but produces no notification."""
    today = now_local().date()
    medication_service.create_medication(
        db,
        make_payload(
            start_date=(today - timedelta(days=5)).isoformat(),
            end_date=(today - timedelta(days=4)).isoformat(),
        ),
    )
    db.commit()
    summary = run_tick(db, send_windows=False)
    assert summary["dose_notifications"] == 0
    assert pending_for_browser(db, "en") == []


def test_reminders_of_an_appointment_that_already_happened_do_not_stay_queued(db):
    """The app was closed over the appointment: nothing is shown, but the
    reminder is marked as handled instead of being rescanned forever."""
    appointment = make_appointment(db, when=now_local() - timedelta(hours=1))
    db.commit()

    summary = run_tick(db, send_windows=False)
    assert summary["appointment_notifications"] == 0
    assert all(reminder.sent_at is not None for reminder in appointment.reminders)
    assert run_tick(db, send_windows=False)["appointment_notifications"] == 0


def test_stale_notifications_are_not_toasted_when_windows_is_re_enabled(db):
    """Turning the Windows channel back on must not fire a days-old backlog."""

    old = Notification(
        type=NotificationType.DOSE.value,
        reference_id=1,
        fire_at=now_local() - timedelta(hours=10),
        title_key="notification.medication_title",
        body_key="notification.medication_body",
        payload="{}",
    )
    db.add(old)
    db.commit()

    sent = []
    import app.notifications.windows as windows

    original_available, original_send = windows.is_available, windows.send_toast
    windows.is_available = lambda: True
    windows.send_toast = lambda title, body, icon=None: (sent.append(title), (True, None))[1]
    try:
        run_tick(db)
    finally:
        windows.is_available, windows.send_toast = original_available, original_send

    assert sent == []
    assert old.windows_sent_at is not None  # handled, just not shown


def test_tick_also_marks_missed_doses_and_completes_treatments(db):
    today = now_local().date()
    medication = medication_service.create_medication(
        db,
        make_payload(
            start_date=(today - timedelta(days=4)).isoformat(),
            end_date=(today - timedelta(days=1)).isoformat(),
        ),
    )
    medication.status = "active"
    db.commit()

    summary = run_tick(db, send_windows=False)
    assert summary["missed_doses"] > 0
    assert medication.status == "completed"
    assert all(dose.status != DoseStatus.SCHEDULED.value for dose in medication.doses)


def test_moving_an_appointment_still_sends_its_reminder(db):
    """Regression: the dedupe key used to ignore the date, so a rescheduled
    appointment was silently blocked by its own old reminder."""
    from app.services import appointments as appointment_service

    appointment = make_appointment(db, when=now_local() + timedelta(hours=23, minutes=30))
    db.commit()
    assert run_tick(db, send_windows=False)["appointment_notifications"] >= 1

    # Push it a week out and back into the 1-day window.
    appointment_service.update_appointment(
        db,
        appointment.id,
        {
            "doctor_id": appointment.doctor_id,
            "scheduled_at": (now_local() + timedelta(days=7, hours=23, minutes=30)).isoformat(),
        },
    )
    db.commit()
    # Nothing is due yet at the new date...
    assert run_tick(db, send_windows=False)["appointment_notifications"] == 0

    # ...but once it is, the new reminder is not blocked by the old row.
    appointment_service.update_appointment(
        db,
        appointment.id,
        {
            "doctor_id": appointment.doctor_id,
            "scheduled_at": (now_local() + timedelta(hours=22, minutes=30)).isoformat(),
        },
    )
    db.commit()
    assert run_tick(db, send_windows=False)["appointment_notifications"] >= 1
