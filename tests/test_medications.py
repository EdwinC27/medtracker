"""Medication lifecycle: creation, validation, status transitions, doses."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

import pytest

from app.models.models import DoseStatus, MedicationStatus
from app.services import medications as service
from app.services.errors import ValidationError
from app.services.scheduling import (
    complete_finished_medications,
    mark_overdue_doses_as_missed,
)
from app.utils.timeutil import now_local


def last_medication(db):
    """The most recently created medication, for tests that do not keep it."""
    from app.models.models import Medication

    return db.query(Medication).order_by(Medication.id.desc()).first()


def register_before_start(db, medication):
    """Pretend the medication was added to the application before it began.

    Most tests predate the "before registration" rule and assume that every
    generated dose is pending, which is only true when the medication was
    registered before its first dose was due. Backdating the registration says
    that explicitly instead of leaving it to what time the suite runs at.
    """
    from app.utils.timeutil import combine

    medication.created_at = (
        combine(medication.start_date, medication.first_dose_time) - timedelta(days=1)
    )
    for dose in medication.doses:
        if dose.status == DoseStatus.BEFORE_REGISTRATION.value:
            dose.status = DoseStatus.SCHEDULED.value
    db.flush()
    return medication


def make_payload(**overrides):
    today = now_local().date()
    payload = {
        "name": "Amoxicillin",
        "dose_amount": "500",
        "dose_unit": "mg",
        "quantity": 1,
        "form": "capsule",
        "comments": "Take with food",
        "start_date": today.isoformat(),
        "end_date": (today + timedelta(days=9)).isoformat(),
        "frequency_hours": 8,
        "first_dose_time": "10:00",
    }
    payload.update(overrides)
    return payload


def test_create_generates_the_whole_schedule(db):
    medication = service.create_medication(db, make_payload())
    assert medication.status == MedicationStatus.ACTIVE.value
    assert len(medication.doses) == 29  # 10 days, every 8h from 10:00
    assert medication.doses[0].scheduled_at.time() == time(10, 0)
    # Nothing is marked at creation. A dose that was already due when the
    # medication was entered is recorded as history (see
    # tests/test_before_registration.py); everything else is pending.
    assert all(
        dose.status in (DoseStatus.SCHEDULED.value, DoseStatus.BEFORE_REGISTRATION.value)
        for dose in medication.doses
    )
    register_before_start(db, medication)
    assert all(dose.status == DoseStatus.SCHEDULED.value for dose in medication.doses)


def test_end_date_before_start_date_is_rejected(db):
    today = now_local().date()
    with pytest.raises(ValidationError) as exc:
        service.create_medication(
            db,
            make_payload(
                start_date=today.isoformat(),
                end_date=(today - timedelta(days=1)).isoformat(),
            ),
        )
    assert exc.value.fields["end_date"] == "validation.end_before_start"


def test_only_name_frequency_and_start_date_are_required(db):
    """v2: dose, unit, quantity, form, comments, image and end date are all
    optional, and the backend is what enforces the three that are not."""
    with pytest.raises(ValidationError) as exc:
        service.create_medication(db, {"name": "", "frequency_hours": None, "start_date": ""})
    assert exc.value.fields["name"] == "validation.name_required"
    assert exc.value.fields["frequency_hours"] == "validation.frequency_required"
    assert exc.value.fields["start_date"] == "validation.start_date_required"

    minimal = service.create_medication(
        db,
        {
            "name": "Vitamin D",
            "frequency_hours": 24,
            "start_date": now_local().date().isoformat(),
            "end_date": (now_local().date() + timedelta(days=3)).isoformat(),
            "first_dose_time": "09:00",
        },
    )
    assert minimal.dose_amount is None
    assert minimal.quantity is None
    assert minimal.doses


def test_invalid_frequency_is_rejected(db):
    with pytest.raises(ValidationError) as exc:
        service.create_medication(db, make_payload(frequency_hours=7))
    assert exc.value.fields["frequency_hours"] == "validation.frequency_invalid"


def test_zero_quantity_is_rejected(db):
    with pytest.raises(ValidationError) as exc:
        service.create_medication(db, make_payload(quantity=0))
    assert exc.value.fields["quantity"] == "validation.quantity_positive"


def test_suspend_removes_future_doses_but_keeps_history(db):
    medication = service.create_medication(db, make_payload())
    # Mark the first dose as taken so it becomes history.
    first = medication.doses[0]
    service.set_dose_status(db, first.id, DoseStatus.TAKEN.value)
    before = len(medication.doses)

    service.suspend_medication(db, medication.id)

    assert medication.status == MedicationStatus.SUSPENDED.value
    assert len(medication.doses) < before
    assert any(dose.status == DoseStatus.TAKEN.value for dose in medication.doses)
    assert all(
        dose.scheduled_at <= now_local() or dose.status != DoseStatus.SCHEDULED.value
        for dose in medication.doses
    )


def test_resume_regenerates_future_doses(db):
    medication = service.create_medication(db, make_payload())
    service.suspend_medication(db, medication.id)
    suspended_count = len(medication.doses)

    service.resume_medication(db, medication.id)

    assert medication.status == MedicationStatus.ACTIVE.value
    assert len(medication.doses) > suspended_count


def test_complete_stops_the_treatment(db):
    medication = service.create_medication(db, make_payload())
    service.complete_medication(db, medication.id)
    assert medication.status == MedicationStatus.COMPLETED.value
    assert medication.completed_at is not None
    assert not [d for d in medication.doses if d.scheduled_at > now_local()]


def test_medication_whose_end_date_passed_becomes_completed(db):
    today = now_local().date()
    medication = service.create_medication(
        db,
        make_payload(
            start_date=(today - timedelta(days=10)).isoformat(),
            end_date=(today - timedelta(days=1)).isoformat(),
        ),
    )
    # created_medication already detects this, but the scheduler must too
    medication.status = MedicationStatus.ACTIVE.value
    db.flush()
    assert complete_finished_medications(db) == 1
    assert medication.status == MedicationStatus.COMPLETED.value


def test_delete_removes_the_medication_and_its_doses(db):
    from app.models.models import MedicationDose

    medication = service.create_medication(db, make_payload())
    medication_id = medication.id
    service.delete_medication(db, medication_id)
    db.commit()

    assert db.query(MedicationDose).filter_by(medication_id=medication_id).count() == 0


def test_unmarked_doses_become_missed_after_the_grace_period(db):
    today = now_local().date()
    medication = service.create_medication(
        db,
        make_payload(
            start_date=(today - timedelta(days=2)).isoformat(),
            end_date=(today + timedelta(days=2)).isoformat(),
        ),
    )
    register_before_start(db, medication)
    changed = mark_overdue_doses_as_missed(db, grace_minutes=120)
    assert changed > 0
    assert all(
        dose.status != DoseStatus.SCHEDULED.value
        for dose in medication.doses
        if dose.scheduled_at < now_local() - timedelta(minutes=120)
    )


def test_nothing_is_ever_marked_as_taken_automatically(db):
    today = now_local().date()
    medication = service.create_medication(
        db,
        make_payload(
            start_date=(today - timedelta(days=3)).isoformat(),
            end_date=(today + timedelta(days=3)).isoformat(),
        ),
    )
    mark_overdue_doses_as_missed(db, grace_minutes=0)
    complete_finished_medications(db)
    assert not [d for d in medication.doses if d.status == DoseStatus.TAKEN.value]


def test_marking_a_dose_records_when_it_was_marked(db):
    medication = service.create_medication(db, make_payload())
    dose = medication.doses[0]
    service.set_dose_status(db, dose.id, DoseStatus.TAKEN.value)
    assert dose.status == DoseStatus.TAKEN.value
    assert dose.marked_at is not None

    service.set_dose_status(db, dose.id, DoseStatus.SCHEDULED.value)
    assert dose.marked_at is None


def test_editing_the_schedule_keeps_marked_doses(db):
    medication = service.create_medication(db, make_payload())
    past = [d for d in medication.doses if d.scheduled_at < now_local()]
    if past:
        service.set_dose_status(db, past[0].id, DoseStatus.TAKEN.value)

    today = now_local().date()
    service.update_medication(
        db,
        medication.id,
        make_payload(end_date=(today + timedelta(days=20)).isoformat()),
    )

    assert medication.end_date == today + timedelta(days=20)
    if past:
        assert any(d.status == DoseStatus.TAKEN.value for d in medication.doses)


def test_extending_the_end_date_adds_doses(db):
    medication = service.create_medication(db, make_payload())
    before = len(medication.doses)
    today = now_local().date()
    service.update_medication(
        db, medication.id, make_payload(end_date=(today + timedelta(days=19)).isoformat())
    )
    assert len(medication.doses) > before


def test_editing_without_touching_the_dates_keeps_a_finished_treatment_finished(db):
    medication = service.create_medication(db, make_payload())
    service.complete_medication(db, medication.id)

    service.update_medication(db, medication.id, make_payload(name="Amoxicillin 500"))

    assert medication.status == MedicationStatus.COMPLETED.value
    assert medication.name == "Amoxicillin 500"
    assert not [d for d in medication.doses if d.scheduled_at > now_local()]


def test_moving_the_end_date_forward_revives_a_finished_treatment(db):
    medication = service.create_medication(db, make_payload())
    service.complete_medication(db, medication.id)

    today = now_local().date()
    service.update_medication(
        db, medication.id, make_payload(end_date=(today + timedelta(days=15)).isoformat())
    )

    assert medication.status == MedicationStatus.ACTIVE.value
    assert [d for d in medication.doses if d.scheduled_at > now_local()]


def test_editing_a_medication_keeps_all_of_its_appointment_links(db):
    from tests.test_appointments import make_appointment

    medication = service.create_medication(db, make_payload())
    first = make_appointment(db, when=now_local() + timedelta(days=2))
    second = make_appointment(db, when=now_local() + timedelta(days=9))
    medication.appointments = [first, second]
    db.flush()

    # An edit that does not mention the links at all must not drop them.
    service.update_medication(db, medication.id, make_payload(comments="Updated"))
    assert {a.id for a in medication.appointments} == {first.id, second.id}

    # And an edit that does mention them replaces the whole set, as sent.
    payload = make_payload()
    payload["appointment_ids"] = [second.id]
    service.update_medication(db, medication.id, payload)
    assert [a.id for a in medication.appointments] == [second.id]


def test_invalid_appointment_ids_raise_a_validation_error_not_a_crash(db):
    payload = make_payload()
    payload["appointment_ids"] = ["not-a-number"]
    with pytest.raises(ValidationError):
        service.create_medication(db, payload)


def test_shortening_the_end_date_removes_future_doses(db):
    medication = service.create_medication(db, make_payload())
    before = len(medication.doses)
    today = now_local().date()
    service.update_medication(
        db, medication.id, make_payload(end_date=(today + timedelta(days=2)).isoformat())
    )
    assert len(medication.doses) < before
    assert all(dose.scheduled_at.date() <= today + timedelta(days=2) for dose in medication.doses)
