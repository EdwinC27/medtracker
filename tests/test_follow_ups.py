"""Follow-up appointments.

The relationship is declared when the *new* appointment is created — it is never
created automatically from the earlier one — and only a visit that happened
before the new one can be chosen.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from app.services import appointments as service
from app.services.errors import ValidationError
from tests.test_doctors import make_doctor

AUG_15 = datetime(2026, 8, 15, 10, 0)
AUG_20 = datetime(2026, 8, 20, 10, 0)
AUG_25 = datetime(2026, 8, 25, 10, 0)
AUG_30 = datetime(2026, 8, 30, 10, 0)


def make(db, when, doctor=None, **overrides):
    payload = {
        "doctor_id": (doctor or make_doctor(db, name=f"Dr. {when:%d%H%M}")).id,
        "scheduled_at": when.isoformat(),
    }
    payload.update(overrides)
    return service.create_appointment(db, payload)


def test_an_appointment_can_have_no_follow_up(db):
    appointment = make(db, AUG_15)
    assert appointment.follow_up_of_id is None
    assert appointment.follow_ups == []


def test_an_appointment_can_be_a_follow_up_of_an_earlier_one(db):
    doctor = make_doctor(db)
    first = make(db, AUG_15, doctor)
    second = make(db, AUG_25, doctor, follow_up_of_id=first.id)

    assert second.follow_up_of_id == first.id
    assert second.follow_up_of is first
    # …and it is navigable from the original visit too.
    assert [a.id for a in first.follow_ups] == [second.id]


def test_a_future_appointment_cannot_be_the_previous_one(db):
    doctor = make_doctor(db)
    later = make(db, AUG_30, doctor)

    with pytest.raises(ValidationError) as exc:
        make(db, AUG_25, doctor, follow_up_of_id=later.id)
    assert exc.value.fields["follow_up_of_id"] == "validation.follow_up_not_earlier"


def test_an_appointment_at_the_very_same_time_is_not_earlier(db):
    doctor = make_doctor(db)
    first = make(db, AUG_25, doctor)
    with pytest.raises(ValidationError) as exc:
        make(db, AUG_25, doctor, follow_up_of_id=first.id)
    assert exc.value.fields["follow_up_of_id"] == "validation.follow_up_not_earlier"


def test_an_appointment_cannot_follow_up_on_itself(db):
    doctor = make_doctor(db)
    appointment = make(db, AUG_25, doctor)
    with pytest.raises(ValidationError) as exc:
        service.update_appointment(
            db,
            appointment.id,
            {
                "doctor_id": doctor.id,
                "scheduled_at": AUG_25.isoformat(),
                "follow_up_of_id": appointment.id,
            },
        )
    assert exc.value.fields["follow_up_of_id"] == "validation.follow_up_self"


def test_an_unknown_appointment_is_rejected(db):
    doctor = make_doctor(db)
    with pytest.raises(ValidationError) as exc:
        make(db, AUG_25, doctor, follow_up_of_id=99999)
    assert exc.value.fields["follow_up_of_id"] == "validation.follow_up_invalid"


def test_the_options_offered_only_contain_earlier_appointments(db):
    doctor = make_doctor(db)
    aug15 = make(db, AUG_15, doctor)
    aug20 = make(db, AUG_20, doctor)
    make(db, AUG_30, doctor)  # later: must not be offered

    options = service.eligible_follow_up_targets(db, AUG_25)
    assert {a.id for a in options} == {aug15.id, aug20.id}


def test_the_options_exclude_the_appointment_being_edited(db):
    doctor = make_doctor(db)
    aug15 = make(db, AUG_15, doctor)
    aug20 = make(db, AUG_20, doctor)

    options = service.eligible_follow_up_targets(db, AUG_25, exclude_id=aug20.id)
    assert {a.id for a in options} == {aug15.id}


def test_a_chain_of_follow_ups_is_navigable(db):
    doctor = make_doctor(db)
    first = make(db, AUG_15, doctor)
    second = make(db, AUG_20, doctor, follow_up_of_id=first.id)
    third = make(db, AUG_25, doctor, follow_up_of_id=second.id)

    assert third.follow_up_of.follow_up_of.id == first.id

    payload = service.serialize_appointment(second)
    assert payload["follow_up_of"]["id"] == first.id
    assert [f["id"] for f in payload["follow_ups"]] == [third.id]


def test_deleting_the_earlier_appointment_keeps_the_later_one(db):
    doctor = make_doctor(db)
    first = make(db, AUG_15, doctor)
    second = make(db, AUG_25, doctor, follow_up_of_id=first.id)

    service.delete_appointment(db, first.id)
    db.commit()

    assert service.get_appointment(db, second.id) is second
    assert second.follow_up_of_id is None


def test_moving_an_appointment_cannot_invert_its_follow_up_chain(db):
    """Regression: A -> B could be turned into a cycle by moving A after B."""
    doctor = make_doctor(db)
    first = make(db, AUG_15, doctor)
    make(db, AUG_20, doctor, follow_up_of_id=first.id)

    with pytest.raises(ValidationError) as exc:
        service.update_appointment(
            db,
            first.id,
            {"doctor_id": doctor.id, "scheduled_at": AUG_25.isoformat()},
        )
    assert exc.value.fields["scheduled_at"] == "validation.follow_up_would_invert"


def test_moving_an_appointment_that_stays_earlier_is_fine(db):
    doctor = make_doctor(db)
    first = make(db, AUG_15, doctor)
    make(db, AUG_25, doctor, follow_up_of_id=first.id)

    service.update_appointment(
        db, first.id, {"doctor_id": doctor.id, "scheduled_at": AUG_20.isoformat()}
    )
    assert first.scheduled_at == AUG_20
