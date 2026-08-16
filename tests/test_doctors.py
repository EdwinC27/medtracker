"""Doctors, and the Doctor -> Appointment -> Medication chain."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from app.services import appointments as appointment_service
from app.services import doctors as service
from app.services import medications as medication_service
from app.services.errors import NotFoundError, ValidationError
from app.utils.timeutil import now_local
from tests.test_medications import make_payload


def make_doctor(db, **overrides):
    payload = {
        "name": "Dr. John Smith",
        "occupation": "Otolaryngologist",
        "phone": "555-555-5555",
    }
    payload.update(overrides)
    return service.create_doctor(db, payload)


def test_create_with_name_occupation_and_phone(db):
    doctor = make_doctor(db)
    assert doctor.name == "Dr. John Smith"
    assert doctor.occupation == "Otolaryngologist"
    assert doctor.phone == "555-555-5555"


def test_name_is_required(db):
    with pytest.raises(ValidationError) as exc:
        service.create_doctor(db, {"name": "  "})
    assert exc.value.fields["name"] == "validation.doctor_name_required"


def test_occupation_and_phone_are_optional(db):
    doctor = service.create_doctor(db, {"name": "Dra. Rosas"})
    assert doctor.occupation is None
    assert doctor.phone is None


def test_edit(db):
    doctor = make_doctor(db)
    service.update_doctor(
        db, doctor.id, {"name": "Dr. J. Smith", "occupation": "ENT", "phone": "555-111"}
    )
    assert doctor.name == "Dr. J. Smith"
    assert doctor.occupation == "ENT"
    assert doctor.phone == "555-111"


def test_delete(db):
    doctor = make_doctor(db)
    service.delete_doctor(db, doctor.id)
    db.commit()
    with pytest.raises(NotFoundError):
        service.get_doctor(db, doctor.id)


def test_a_doctor_with_appointments_cannot_be_deleted_by_accident(db):
    """Deleting would take visit history with it, so it is refused instead."""
    doctor = make_doctor(db)
    appointment_service.create_appointment(
        db,
        {"doctor_id": doctor.id, "scheduled_at": (now_local() + timedelta(days=3)).isoformat()},
    )
    with pytest.raises(ValidationError) as exc:
        service.delete_doctor(db, doctor.id)
    assert exc.value.fields["doctor"] == "validation.doctor_has_appointments"

    assert service.get_doctor(db, doctor.id) is doctor  # still there


def test_search_by_name_or_specialty(db):
    make_doctor(db, name="Dr. Smith", occupation="Cardiologist")
    make_doctor(db, name="Dra. Rosas", occupation="Otolaryngologist")

    assert [d.name for d in service.list_doctors(db, "rosas")] == ["Dra. Rosas"]
    assert [d.name for d in service.list_doctors(db, "cardio")] == ["Dr. Smith"]
    assert len(service.list_doctors(db)) == 2


def test_an_appointment_belongs_to_a_doctor_and_the_doctor_lists_it(db):
    doctor = make_doctor(db)
    first = appointment_service.create_appointment(
        db, {"doctor_id": doctor.id, "scheduled_at": datetime(2026, 8, 15, 10, 0).isoformat()}
    )
    second = appointment_service.create_appointment(
        db, {"doctor_id": doctor.id, "scheduled_at": datetime(2026, 8, 25, 10, 0).isoformat()}
    )
    db.refresh(doctor)

    assert {a.id for a in doctor.appointments} == {first.id, second.id}
    assert first.doctor is doctor
    # The name is stored once, on the doctor, and only referenced by the visit.
    assert appointment_service.serialize_appointment(first)["doctor_name"] == doctor.name


def test_the_chain_is_doctor_then_appointment_then_medication(db):
    """A medication hangs off appointments, never off a doctor directly."""
    doctor = make_doctor(db)
    medication_a = medication_service.create_medication(db, make_payload(name="Amoxicillin"))
    medication_b = medication_service.create_medication(db, make_payload(name="Ibuprofen"))

    first = appointment_service.create_appointment(
        db,
        {
            "doctor_id": doctor.id,
            "scheduled_at": datetime(2026, 8, 15, 10, 0).isoformat(),
            "medication_ids": [medication_a.id],
        },
    )
    second = appointment_service.create_appointment(
        db,
        {
            "doctor_id": doctor.id,
            "scheduled_at": datetime(2026, 8, 25, 10, 0).isoformat(),
            "medication_ids": [medication_b.id],
        },
    )

    assert [m.id for m in first.medications] == [medication_a.id]
    assert [m.id for m in second.medications] == [medication_b.id]
    assert not hasattr(medication_a, "doctor_id")


def test_the_same_medication_can_belong_to_several_appointments(db):
    doctor = make_doctor(db)
    medication = medication_service.create_medication(db, make_payload())

    first = appointment_service.create_appointment(
        db,
        {
            "doctor_id": doctor.id,
            "scheduled_at": datetime(2026, 8, 15, 10, 0).isoformat(),
            "medication_ids": [medication.id],
        },
    )
    second = appointment_service.create_appointment(
        db,
        {
            "doctor_id": doctor.id,
            "scheduled_at": datetime(2026, 8, 25, 10, 0).isoformat(),
            "medication_ids": [medication.id],
        },
    )

    assert {a.id for a in medication.appointments} == {first.id, second.id}


def test_an_appointment_can_hold_several_medications(db):
    doctor = make_doctor(db)
    first = medication_service.create_medication(db, make_payload(name="Amoxicillin"))
    second = medication_service.create_medication(db, make_payload(name="Ibuprofen"))

    appointment = appointment_service.create_appointment(
        db,
        {
            "doctor_id": doctor.id,
            "scheduled_at": datetime(2026, 8, 15, 10, 0).isoformat(),
            "medication_ids": [first.id, second.id],
        },
    )
    assert {m.id for m in appointment.medications} == {first.id, second.id}
