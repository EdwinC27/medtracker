"""v3 Global search — and the promise that it only ever reads."""

from __future__ import annotations

from datetime import datetime

from app.models.models import Appointment, Doctor, Medication, MedicationDose
from app.services import appointments as appointment_service
from app.services import medications as medication_service
from app.services.search import search
from tests.test_appointments import make_doctor
from tests.test_medications import make_payload


def seed(db):
    medication = medication_service.create_medication(
        db,
        make_payload(
            name="Amoxicillin",
            comments="Take with food, prescribed for the ear infection",
            start_date="2026-09-01",
            end_date="2026-09-03",
        ),
    )
    doctor = make_doctor(db, "Dr. Rosa Martínez", occupation="Otolaryngologist")
    appointment = appointment_service.create_appointment(
        db,
        {
            "doctor_id": doctor.id,
            "scheduled_at": datetime(2026, 9, 21, 10, 0).isoformat(),
            "treatment": "Sinus review",
            "location": "Clinic Norte",
            "notes": "Bring the audiometry",
            "medication_ids": [medication.id],
        },
    )
    db.commit()
    return medication, doctor, appointment


def test_a_one_letter_query_returns_nothing(db):
    seed(db)
    result = search(db, "a")
    assert result["total"] == 0
    assert result["medications"] == [] and result["doctors"] == []


def test_an_empty_query_returns_nothing(db):
    seed(db)
    assert search(db, "   ")["total"] == 0


def test_a_medication_is_found_by_name_case_insensitively(db):
    seed(db)
    hits = search(db, "AMOXI")["medications"]
    assert [hit["name"] for hit in hits] == ["Amoxicillin"]
    assert hits[0]["href"] == f"/medications/{hits[0]['id']}"


def test_a_medication_is_found_by_its_comments(db):
    seed(db)
    assert [hit["name"] for hit in search(db, "with food")["medications"]] == ["Amoxicillin"]


def test_a_doctor_is_found_by_name_and_by_occupation(db):
    seed(db)
    assert len(search(db, "martínez")["doctors"]) == 1
    assert len(search(db, "otolaryng")["doctors"]) == 1


def test_a_doctor_hit_carries_how_many_appointments_they_have(db):
    seed(db)
    assert search(db, "martínez")["doctors"][0]["appointment_count"] == 1


def test_an_appointment_is_found_by_treatment_notes_or_location(db):
    seed(db)
    assert len(search(db, "sinus")["appointments"]) == 1
    assert len(search(db, "audiometry")["appointments"]) == 1
    assert len(search(db, "clinic norte")["appointments"]) == 1


def test_an_appointment_is_found_through_its_doctor(db):
    seed(db)
    hits = search(db, "rosa")["appointments"]
    assert len(hits) == 1
    assert hits[0]["doctor_name"] == "Dr. Rosa Martínez"
    assert [m["name"] for m in hits[0]["medications"]] == ["Amoxicillin"]


def test_an_appointment_is_found_by_an_exact_date(db):
    seed(db)
    assert len(search(db, "2026-09-21")["appointments"]) == 1
    assert len(search(db, "21/09/2026")["appointments"]) == 1
    assert search(db, "2026-09-22")["appointments"] == []


def test_a_query_that_matches_nothing_says_so(db):
    seed(db)
    result = search(db, "zzzz")
    assert result["total"] == 0
    assert result["query"] == "zzzz"


def test_the_total_adds_the_three_groups_up(db):
    seed(db)
    result = search(db, "o")          # too short on purpose
    assert result["total"] == 0
    result = search(db, "ro")
    assert result["total"] == (
        len(result["medications"]) + len(result["doctors"]) + len(result["appointments"])
    )


def test_searching_changes_nothing(db):
    """The whole module is SELECTs; this is the test that keeps it that way."""
    seed(db)
    before = (
        db.query(Medication).count(),
        db.query(MedicationDose).count(),
        db.query(Doctor).count(),
        db.query(Appointment).count(),
    )
    for query in ("amoxi", "rosa", "sinus", "2026-09-21", "zzz"):
        search(db, query)
    after = (
        db.query(Medication).count(),
        db.query(MedicationDose).count(),
        db.query(Doctor).count(),
        db.query(Appointment).count(),
    )
    assert before == after


def test_the_endpoint_answers_and_stays_quiet_on_a_short_query(client):
    client.post("/api/medications", json=make_payload(name="Ryaltris"))
    assert client.get("/api/search?q=ryal").json()["total"] == 1
    assert client.get("/api/search?q=r").json()["total"] == 0
    assert client.get("/api/search").json()["total"] == 0
