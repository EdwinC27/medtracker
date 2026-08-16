"""Appointments, their reminders (3 days / 1 day / 3 hours) and their link to
medications."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from app.models.models import ReminderKind
from app.services import appointments as service
from app.services import medications as medication_service
from app.services.errors import ValidationError
from app.utils.timeutil import now_local
from tests.test_medications import make_payload


def make_appointment(db, when=None, **overrides):
    when = when or (now_local() + timedelta(days=5))
    payload = {
        "doctor_name": "Dr. Smith",
        "scheduled_at": when.replace(second=0, microsecond=0).isoformat(),
        "treatment": "Ear infection",
        "notes": "Bring the previous lab results",
    }
    payload.update(overrides)
    return service.create_appointment(db, payload)


def test_reminders_match_the_spec_example(db):
    """Appointment on Aug 21 at 10:00 AM -> Aug 18 10:00, Aug 20 10:00, Aug 21 07:00."""
    appointment = make_appointment(db, when=datetime(2026, 8, 21, 10, 0))
    by_kind = {reminder.kind: reminder.remind_at for reminder in appointment.reminders}

    assert by_kind[ReminderKind.DAYS_3.value] == datetime(2026, 8, 18, 10, 0)
    assert by_kind[ReminderKind.DAY_1.value] == datetime(2026, 8, 20, 10, 0)
    assert by_kind[ReminderKind.HOURS_3.value] == datetime(2026, 8, 21, 7, 0)


def test_reminders_can_be_switched_off(db):
    appointment = make_appointment(db, reminder_days_3=False, reminder_hours_3=False)
    kinds = {reminder.kind for reminder in appointment.reminders}
    assert kinds == {ReminderKind.DAY_1.value}


def test_moving_an_appointment_moves_its_reminders(db):
    appointment = make_appointment(db, when=datetime(2026, 8, 21, 10, 0))
    service.update_appointment(
        db,
        appointment.id,
        {
            "doctor_name": appointment.doctor_name,
            "scheduled_at": datetime(2026, 9, 1, 9, 0).isoformat(),
        },
    )
    by_kind = {reminder.kind: reminder.remind_at for reminder in appointment.reminders}
    assert by_kind[ReminderKind.DAY_1.value] == datetime(2026, 8, 31, 9, 0)
    assert by_kind[ReminderKind.HOURS_3.value] == datetime(2026, 9, 1, 6, 0)


def test_doctor_name_is_required(db):
    with pytest.raises(ValidationError) as exc:
        service.create_appointment(db, {"doctor_name": "", "scheduled_at": "2026-08-21T10:00"})
    assert exc.value.fields["doctor_name"] == "validation.doctor_required"


def test_datetime_is_required(db):
    with pytest.raises(ValidationError) as exc:
        service.create_appointment(db, {"doctor_name": "Dr. Smith", "scheduled_at": ""})
    assert exc.value.fields["scheduled_at"] == "validation.appointment_datetime_required"


def test_link_between_appointment_and_medications_works_both_ways(db):
    medication = medication_service.create_medication(db, make_payload())
    appointment = make_appointment(db, medication_ids=[medication.id])

    assert [m.id for m in appointment.medications] == [medication.id]
    assert [a.id for a in medication.appointments] == [appointment.id]


def test_deleting_an_appointment_keeps_its_medications(db):
    medication = medication_service.create_medication(db, make_payload())
    appointment = make_appointment(db, medication_ids=[medication.id])
    service.delete_appointment(db, appointment.id)
    db.commit()

    assert medication_service.get_medication(db, medication.id) is not None
    assert medication.appointments == []


def test_next_appointment_returns_the_closest_upcoming_one(db):
    make_appointment(db, when=now_local() + timedelta(days=20))
    soon = make_appointment(db, when=now_local() + timedelta(days=2))
    make_appointment(db, when=now_local() - timedelta(days=2))

    assert service.next_appointment(db).id == soon.id
