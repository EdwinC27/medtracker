"""v3 Medical timeline: a chronological reading of what already exists."""

from __future__ import annotations

from datetime import datetime, timedelta

from app.services import appointments as appointment_service
from app.services import medications as medication_service
from app.services.timeline import build_timeline
from app.utils.timeutil import now_local
from tests.test_appointments import make_doctor
from tests.test_medications import make_payload


def seed(db):
    doctor = make_doctor(db, "Dr. Alvarez")
    other = make_doctor(db, "Dr. Beltran")
    medication = medication_service.create_medication(db, make_payload(name="Amoxicillin"))

    past = appointment_service.create_appointment(
        db,
        {
            "doctor_id": doctor.id,
            "scheduled_at": (now_local() - timedelta(days=30)).replace(
                second=0, microsecond=0
            ).isoformat(),
            "treatment": "First consultation",
            "medication_ids": [medication.id],
        },
    )
    upcoming = appointment_service.create_appointment(
        db,
        {
            "doctor_id": doctor.id,
            "scheduled_at": (now_local() + timedelta(days=30)).replace(
                second=0, microsecond=0
            ).isoformat(),
            "treatment": "Review",
            "follow_up_of_id": past.id,
        },
    )
    elsewhere = appointment_service.create_appointment(
        db,
        {
            "doctor_id": other.id,
            "scheduled_at": (now_local() + timedelta(days=10)).replace(
                second=0, microsecond=0
            ).isoformat(),
            "treatment": "Second opinion",
        },
    )
    db.commit()
    return {
        "doctor": doctor, "other": other, "medication": medication,
        "past": past, "upcoming": upcoming, "elsewhere": elsewhere,
    }


def test_newest_first_is_the_default_and_oldest_first_reverses_it(db):
    seed(db)
    newest = [entry["datetime"] for entry in build_timeline(db)["entries"]]
    oldest = [entry["datetime"] for entry in build_timeline(db, order="oldest")["entries"]]

    assert newest == sorted(newest, reverse=True)
    assert oldest == list(reversed(newest))
    assert build_timeline(db)["order"] == "newest"


def test_the_scope_splits_past_from_upcoming(db):
    data = seed(db)
    past = build_timeline(db, scope="past", kind="appointments")["entries"]
    upcoming = build_timeline(db, scope="upcoming", kind="appointments")["entries"]

    assert [entry["id"] for entry in past] == [data["past"].id]
    assert set(entry["id"] for entry in upcoming) == {
        data["upcoming"].id, data["elsewhere"].id
    }
    assert all(entry["is_past"] for entry in past)
    assert not any(entry["is_past"] for entry in upcoming)


def test_filtering_by_doctor_keeps_only_their_appointments(db):
    data = seed(db)
    entries = build_timeline(db, doctor_id=data["doctor"].id)["entries"]
    assert all(entry["type"] == "appointment" for entry in entries)
    assert {entry["doctor"]["name"] for entry in entries} == {"Dr. Alvarez"}
    assert len(entries) == 2


def test_filtering_by_medication_keeps_only_where_it_was_prescribed(db):
    data = seed(db)
    entries = build_timeline(
        db, medication_id=data["medication"].id, kind="appointments"
    )["entries"]
    assert [entry["id"] for entry in entries] == [data["past"].id]


def test_an_entry_carries_its_doctor_medications_and_follow_up_links(db):
    data = seed(db)
    by_id = {entry["id"]: entry for entry in build_timeline(db)["entries"]}

    first = by_id[data["past"].id]
    assert first["doctor"]["name"] == "Dr. Alvarez"
    assert first["doctor"]["occupation"] == "Otolaryngologist"
    assert [m["name"] for m in first["medications"]] == ["Amoxicillin"]
    assert [item["id"] for item in first["follow_ups"]] == [data["upcoming"].id]
    assert first["follow_up_of"] is None

    review = by_id[data["upcoming"].id]
    assert review["follow_up_of"]["id"] == data["past"].id
    assert review["treatment"] == "Review"


def test_an_unknown_order_or_scope_falls_back_instead_of_failing(db):
    seed(db)
    payload = build_timeline(db, order="sideways", scope="whenever", kind="nonsense")
    assert payload["order"] == "newest" and payload["scope"] == "all"
    assert payload["kind"] == "all"
    # Three appointments plus the one treatment that seeding creates.
    assert payload["count"] == 4


def test_an_empty_history_is_an_empty_timeline(db):
    payload = build_timeline(db)
    assert payload["entries"] == []
    assert payload["count"] == 0 and payload["total"] == 0
    assert payload["has_more"] is False


def test_the_endpoint_answers(client):
    response = client.get("/api/timeline?order=oldest&scope=all")
    assert response.status_code == 200
    assert response.json()["order"] == "oldest"
