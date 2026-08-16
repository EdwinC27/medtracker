"""The v2 dose reminder schedule.

    -30 min   -15 min   -5 min   dose time   +15 min   +30 min   +2 h -> overdue

Time is controlled by monkeypatching `now_local`, which every module imports
from `app.utils.timeutil`, so these tests never depend on the real clock.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from app.models.models import DoseStatus, Notification, NotificationType
from app.notifications import dispatcher
from app.services import medications as medication_service
from app.services import scheduling
from app.services.settings_service import get_settings
from tests.test_medications import make_payload

# The dose under test is always at 10:00 on this day.
DAY = datetime(2026, 8, 20)
SCHEDULED = DAY.replace(hour=10, minute=0)


@pytest.fixture()
def clock(monkeypatch):
    """A movable clock shared by every module that asks the time."""

    holder = {"now": SCHEDULED - timedelta(hours=1)}

    def fake_now():
        return holder["now"]

    for module in (dispatcher, scheduling, medication_service):
        monkeypatch.setattr(module, "now_local", fake_now)
    monkeypatch.setattr("app.services.settings_service.now_local", fake_now)

    def at(hour, minute):
        holder["now"] = DAY.replace(hour=hour, minute=minute)
        return holder["now"]

    holder["at"] = at
    return holder


@pytest.fixture()
def dose(db, clock):
    """A single Amoxicillin dose scheduled for 10:00."""
    medication = medication_service.create_medication(
        db,
        make_payload(
            start_date=DAY.date().isoformat(),
            end_date=DAY.date().isoformat(),
            frequency_hours=24,
            first_dose_time="10:00",
        ),
    )
    db.commit()
    assert len(medication.doses) == 1
    return medication.doses[0]


def kinds_queued(db) -> list[str]:
    return [
        row.kind
        for row in db.query(Notification)
        .filter(Notification.type == NotificationType.DOSE.value)
        .order_by(Notification.fire_at)
        .all()
    ]


def test_the_six_reminders_fire_at_the_right_minutes(db, dose, clock):
    """Walk the clock through the day and check what exists at each step."""
    at = clock["at"]

    at(9, 20)
    dispatcher.run_tick(db, send_windows=False, send_email=False)
    assert kinds_queued(db) == []          # nothing is due yet

    at(9, 30)
    dispatcher.run_tick(db, send_windows=False, send_email=False)
    assert kinds_queued(db) == ["before_30"]

    at(9, 45)
    dispatcher.run_tick(db, send_windows=False, send_email=False)
    assert kinds_queued(db) == ["before_30", "before_15"]

    at(9, 55)
    dispatcher.run_tick(db, send_windows=False, send_email=False)
    assert kinds_queued(db) == ["before_30", "before_15", "before_5"]

    at(10, 0)
    dispatcher.run_tick(db, send_windows=False, send_email=False)
    assert kinds_queued(db)[-1] == "at_time"

    at(10, 15)
    dispatcher.run_tick(db, send_windows=False, send_email=False)
    assert kinds_queued(db)[-1] == "after_15"

    at(10, 30)
    dispatcher.run_tick(db, send_windows=False, send_email=False)
    assert kinds_queued(db) == [
        "before_30", "before_15", "before_5", "at_time", "after_15", "after_30",
    ]
    assert dose.status == DoseStatus.SCHEDULED.value  # still not overdue

    at(12, 0)
    dispatcher.run_tick(db, send_windows=False, send_email=False)
    assert dose.status == DoseStatus.MISSED.value
    assert kinds_queued(db)[-1] == "overdue"


def test_marking_the_dose_taken_cancels_every_later_reminder(db, dose, clock):
    """9:30 and 9:45 fire, the user takes it at 9:50, and nothing else fires."""
    at = clock["at"]

    at(9, 30)
    dispatcher.run_tick(db, send_windows=False, send_email=False)
    at(9, 45)
    dispatcher.run_tick(db, send_windows=False, send_email=False)
    assert kinds_queued(db) == ["before_30", "before_15"]

    at(9, 50)
    medication_service.set_dose_status(db, dose.id, DoseStatus.TAKEN.value)
    db.commit()

    for hour, minute in ((9, 55), (10, 0), (10, 15), (10, 30), (12, 0)):
        at(hour, minute)
        dispatcher.run_tick(db, send_windows=False, send_email=False)

    assert kinds_queued(db) == ["before_30", "before_15"]
    assert dose.status == DoseStatus.TAKEN.value  # never turned into "missed"


def test_marking_the_dose_skipped_also_cancels_the_rest(db, dose, clock):
    at = clock["at"]
    at(9, 30)
    dispatcher.run_tick(db, send_windows=False, send_email=False)

    at(9, 40)
    medication_service.set_dose_status(db, dose.id, DoseStatus.SKIPPED.value)
    db.commit()

    at(12, 0)
    dispatcher.run_tick(db, send_windows=False, send_email=False)
    assert kinds_queued(db) == ["before_30"]
    assert dose.status == DoseStatus.SKIPPED.value


def test_a_restart_never_duplicates_a_reminder(db, dose, clock):
    """Running the same tick repeatedly is what a restart looks like."""
    clock["at"](10, 0)
    for _ in range(5):
        dispatcher.run_tick(db, send_windows=False, send_email=False)

    kinds = kinds_queued(db)
    assert kinds == sorted(set(kinds), key=kinds.index)  # no repeats
    assert len(kinds) == 4  # -30, -15, -5, at_time

    keys = [row.dedupe_key for row in db.query(Notification).all()]
    assert len(keys) == len(set(keys))
    assert f"dose:{dose.id}:at_time" in keys


def test_each_offset_can_be_switched_off(db, dose, clock):
    settings = get_settings(db)
    settings.dose_before_30 = False
    settings.dose_before_5 = False
    settings.dose_after_15 = False
    db.commit()

    clock["at"](10, 30)
    dispatcher.run_tick(db, send_windows=False, send_email=False)
    assert kinds_queued(db) == ["before_15", "at_time", "after_30"]


def test_overdue_can_be_switched_off_but_the_status_still_changes(db, dose, clock):
    settings = get_settings(db)
    settings.dose_overdue = False
    db.commit()

    clock["at"](12, 0)
    dispatcher.run_tick(db, send_windows=False, send_email=False)
    assert "overdue" not in kinds_queued(db)
    assert dose.status == DoseStatus.MISSED.value


def test_the_overdue_delay_follows_the_setting(db, dose, clock):
    settings = get_settings(db)
    settings.missed_after_minutes = 45
    db.commit()

    clock["at"](10, 40)
    dispatcher.run_tick(db, send_windows=False, send_email=False)
    assert dose.status == DoseStatus.SCHEDULED.value

    clock["at"](10, 46)
    dispatcher.run_tick(db, send_windows=False, send_email=False)
    assert dose.status == DoseStatus.MISSED.value


def test_a_suspended_medication_produces_no_reminders(db, dose, clock):
    medication_service.suspend_medication(db, dose.medication_id)
    db.commit()

    clock["at"](10, 30)
    dispatcher.run_tick(db, send_windows=False, send_email=False)
    assert kinds_queued(db) == []


def test_reminder_text_names_the_offset_in_both_languages(db, dose, clock):
    clock["at"](10, 30)
    dispatcher.run_tick(db, send_windows=False, send_email=False)

    by_kind = {row.kind: row for row in db.query(Notification).all()}

    before = dispatcher.render_notification(by_kind["before_30"], "en")
    assert "In 30 minutes" in before["body"]
    assert "Amoxicillin" in before["body"]

    before_es = dispatcher.render_notification(by_kind["before_30"], "es")
    assert "En 30 minutos" in before_es["body"]

    at_time = dispatcher.render_notification(by_kind["at_time"], "es")
    assert "Es hora de tomar" in at_time["body"]
    assert "1 cápsula" in at_time["body"]

    after = dispatcher.render_notification(by_kind["after_15"], "en")
    assert "15 minutes ago" in after["body"]


def test_the_dose_records_when_it_became_overdue(db, dose, clock):
    """Requirement: the history keeps both when it was due and when it changed."""
    at = clock["at"](12, 0)
    dispatcher.run_tick(db, send_windows=False, send_email=False)

    assert dose.scheduled_at == SCHEDULED
    assert dose.status_changed_at == at
    assert dose.marked_at is None  # nobody marked it; it expired


def test_handling_a_dose_withdraws_a_reminder_already_queued(db, dose, clock):
    """Regression: a reminder queued a moment before Taken must not still be
    delivered to the browser afterwards."""
    from app.notifications.dispatcher import pending_for_browser

    clock["at"](9, 30)
    dispatcher.run_tick(db, send_windows=False, send_email=False)
    assert pending_for_browser(db, "en")          # waiting for the browser

    clock["at"](9, 50)
    medication_service.set_dose_status(db, dose.id, DoseStatus.TAKEN.value)
    db.commit()

    assert pending_for_browser(db, "en") == []    # withdrawn, not delivered


def test_deleting_a_medication_withdraws_its_pending_reminders(db, dose, clock):
    from app.notifications.dispatcher import pending_for_browser

    clock["at"](9, 30)
    dispatcher.run_tick(db, send_windows=False, send_email=False)
    assert pending_for_browser(db, "en")

    medication_service.delete_medication(db, dose.medication_id)
    db.commit()
    assert pending_for_browser(db, "en") == []


def test_suspending_a_medication_withdraws_its_pending_reminders(db, dose, clock):
    from app.notifications.dispatcher import pending_for_browser

    clock["at"](9, 30)
    dispatcher.run_tick(db, send_windows=False, send_email=False)
    medication_service.suspend_medication(db, dose.medication_id)
    db.commit()
    assert pending_for_browser(db, "en") == []
