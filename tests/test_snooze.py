"""v3 Snooze.

The rule the whole feature hangs on: snoozing moves the *reminder*, never the
dose. `scheduled_at` is the historical record of when the dose was due and must
come out of a snooze exactly as it went in, and the dose must stay pending.

Spec example: a dose scheduled at 10:00 AM, snoozed for 30 minutes, reminds at
10:30 AM while still reading "scheduled at 10:00 AM" and still not taken.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from app.models.models import DoseStatus, Notification, NotificationType
from app.notifications import dispatcher
from app.services import medications as medication_service
from app.services import scheduling
from app.services.errors import ValidationError
from app.utils.timeutil import now_local
from tests.test_medications import make_payload

DAY = datetime(2026, 8, 20)
SCHEDULED = DAY.replace(hour=10, minute=0)


@pytest.fixture()
def clock(monkeypatch):
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
    return medication.doses[0]


def dose_kinds(db):
    return [
        row.kind
        for row in db.query(Notification)
        .filter(Notification.type == NotificationType.DOSE.value)
        .order_by(Notification.fire_at)
        .all()
    ]


def undelivered_kinds(db):
    """Reminders still waiting to reach the user on some channel."""
    return [
        row.kind
        for row in db.query(Notification)
        .filter(Notification.type == NotificationType.DOSE.value)
        .order_by(Notification.fire_at)
        .all()
        if not (row.windows_sent_at and row.browser_delivered_at and row.email_sent_at)
    ]


# --------------------------------------------------------------------------- #
# The rule
# --------------------------------------------------------------------------- #
def test_the_spec_example(db, dose, clock):
    """10:00 + snooze 30 min -> reminded at 10:30, still due at 10:00, not taken."""
    clock["at"](10, 0)
    medication_service.snooze_dose(db, dose.id, 30)
    db.commit()

    assert dose.snoozed_until == DAY.replace(hour=10, minute=30)
    assert dose.scheduled_at == SCHEDULED                 # untouched
    assert dose.status == DoseStatus.SCHEDULED.value      # not taken


def test_the_three_offered_delays_all_work(db, clock):
    """10, 30 and 60 minutes are the options the UI offers."""
    clock["at"](10, 0)
    for minutes in (10, 30, 60):
        medication = medication_service.create_medication(
            db,
            make_payload(
                name=f"Med {minutes}",
                start_date=DAY.date().isoformat(),
                end_date=DAY.date().isoformat(),
                frequency_hours=24,
                first_dose_time="10:00",
            ),
        )
        db.commit()
        target = medication.doses[0]
        medication_service.snooze_dose(db, target.id, minutes)
        assert target.snoozed_until == DAY.replace(hour=10) + timedelta(minutes=minutes)


def test_an_arbitrary_delay_is_refused(db, dose, clock):
    with pytest.raises(ValidationError) as exc:
        medication_service.snooze_dose(db, dose.id, 45)
    assert exc.value.fields["minutes"] == "validation.snooze_invalid"
    assert dose.snoozed_until is None


def test_a_dose_that_is_already_handled_cannot_be_snoozed(db, dose, clock):
    clock["at"](10, 0)
    medication_service.set_dose_status(db, dose.id, DoseStatus.TAKEN.value)
    db.commit()

    with pytest.raises(ValidationError) as exc:
        medication_service.snooze_dose(db, dose.id, 10)
    assert exc.value.fields["status"] == "validation.snooze_not_pending"


# --------------------------------------------------------------------------- #
# What the scheduler does with it
# --------------------------------------------------------------------------- #
def test_the_reminder_arrives_when_the_snooze_runs_out(db, dose, clock):
    at = clock["at"]

    at(10, 0)
    dispatcher.run_tick(db, send_windows=False, send_email=False)
    assert "at_time" in dose_kinds(db)

    medication_service.snooze_dose(db, dose.id, 30)
    db.commit()
    # The queued reminders are withdrawn: the rows stay as history, but none of
    # them is still waiting to be delivered.
    assert undelivered_kinds(db) == []

    at(10, 15)
    dispatcher.run_tick(db, send_windows=False, send_email=False)
    assert "snooze" not in dose_kinds(db)   # not yet

    at(10, 30)
    dispatcher.run_tick(db, send_windows=False, send_email=False)
    assert "snooze" in dose_kinds(db)


def test_the_snooze_is_spent_once_it_has_fired(db, dose, clock):
    at = clock["at"]
    at(10, 0)
    medication_service.snooze_dose(db, dose.id, 10)
    db.commit()

    at(10, 10)
    dispatcher.run_tick(db, send_windows=False, send_email=False)
    db.refresh(dose)
    assert dose.snoozed_until is None
    assert dose_kinds(db).count("snooze") == 1

    # A second tick must not produce a second copy of the same reminder.
    at(10, 11)
    dispatcher.run_tick(db, send_windows=False, send_email=False)
    assert dose_kinds(db).count("snooze") == 1


def test_snoozing_twice_gives_two_distinct_reminders(db, dose, clock):
    at = clock["at"]

    at(10, 0)
    medication_service.snooze_dose(db, dose.id, 10)
    db.commit()
    at(10, 10)
    dispatcher.run_tick(db, send_windows=False, send_email=False)

    medication_service.snooze_dose(db, dose.id, 10)
    db.commit()
    at(10, 20)
    dispatcher.run_tick(db, send_windows=False, send_email=False)

    assert dose_kinds(db).count("snooze") == 2


def test_taking_the_dose_during_a_snooze_cancels_it(db, dose, clock):
    at = clock["at"]
    at(10, 0)
    medication_service.snooze_dose(db, dose.id, 30)
    db.commit()

    at(10, 5)
    medication_service.set_dose_status(db, dose.id, DoseStatus.TAKEN.value)
    db.commit()

    at(10, 30)
    dispatcher.run_tick(db, send_windows=False, send_email=False)
    assert "snooze" not in dose_kinds(db)


def test_a_snooze_does_not_stop_the_dose_going_overdue(db, dose, clock):
    """Snoozing is not a way of postponing the record - only the reminder."""
    at = clock["at"]
    at(10, 0)
    medication_service.snooze_dose(db, dose.id, 60)
    db.commit()

    at(12, 0)
    dispatcher.run_tick(db, send_windows=False, send_email=False)
    db.refresh(dose)
    assert dose.status == DoseStatus.MISSED.value
    assert dose.scheduled_at == SCHEDULED


# --------------------------------------------------------------------------- #
# Through the API
# --------------------------------------------------------------------------- #
def due_dose_payload():
    """A dose a few minutes from now.

    Inside its reminder window (which opens 30 minutes before) and still after
    the medication is registered, so it is a dose the application can manage
    rather than one that predates its own registration.
    """
    soon = now_local() + timedelta(minutes=10)
    return make_payload(
        start_date=soon.date().isoformat(),
        end_date=soon.date().isoformat(),
        frequency_hours=24,
        first_dose_time=soon.strftime("%H:%M"),
    )


def test_the_endpoint_snoozes_and_reports_the_new_reminder_time(client):
    created = client.post("/api/medications", json=due_dose_payload())
    assert created.status_code == 201
    dose_id = created.json()["doses"][0]["id"]

    response = client.post(f"/api/doses/{dose_id}/snooze", json={"minutes": 30})
    assert response.status_code == 200
    body = response.json()
    # The dose never moved: it still reads the time it was scheduled for.
    assert body["scheduled_at"][11:16] == due_dose_payload()["first_dose_time"]
    assert body["status"] == "scheduled"
    assert body["snoozed_until"] is not None


def test_the_endpoint_refuses_a_dose_that_is_not_due_yet(client):
    created = client.post(
        "/api/medications",
        json=make_payload(start_date="2026-09-10", end_date="2026-09-10",
                          frequency_hours=24, first_dose_time="10:00"),
    )
    dose_id = created.json()["doses"][0]["id"]
    response = client.post(f"/api/doses/{dose_id}/snooze", json={"minutes": 30})
    assert response.status_code == 422
    assert response.json()["fields"]["status"] == "validation.snooze_not_due_yet"


def test_the_endpoint_refuses_a_delay_it_does_not_offer(client):
    created = client.post("/api/medications", json=due_dose_payload())
    dose_id = created.json()["doses"][0]["id"]
    response = client.post(f"/api/doses/{dose_id}/snooze", json={"minutes": 45})
    assert response.status_code == 422
    assert response.json()["fields"]["minutes"] == "validation.snooze_invalid"
