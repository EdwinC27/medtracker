"""v3 "Today": the one screen that answers "what do I have to do now?"."""

from __future__ import annotations

from datetime import datetime, timedelta

from app.models.models import DoseStatus
from app.services import appointments as appointment_service
from app.services import medications as medication_service
from app.services.today import build_today
from tests.test_appointments import make_doctor
from tests.test_medications import make_payload

# Everything is read at a fixed instant instead of "now", so the assertions are
# about the code and not about what time the suite happens to run at.
NOW = datetime(2026, 9, 10, 12, 0)
TODAY = NOW.date()


def make_medication(db, **overrides):
    payload = make_payload(
        start_date=TODAY.isoformat(),
        end_date=(TODAY + timedelta(days=2)).isoformat(),
        frequency_hours=8,
        first_dose_time="08:00",
    )
    payload.update(overrides)
    return medication_service.create_medication(db, payload)


def test_todays_doses_are_only_todays_and_are_in_order(db):
    make_medication(db)
    db.commit()

    payload = build_today(db, NOW)
    times = [dose["scheduled_at"][11:16] for dose in payload["todays_doses"]]

    assert times == ["08:00", "16:00"]      # 00:00 the next day belongs to tomorrow
    assert payload["date"] == TODAY.isoformat()


def test_the_summary_counts_taken_pending_and_total(db):
    medication = make_medication(db)
    db.commit()
    medication_service.set_dose_status(db, medication.doses[0].id, DoseStatus.TAKEN.value)
    db.commit()

    summary = build_today(db, NOW)["todays_summary"]
    assert summary == {"taken": 1, "pending": 1, "total": 2}


def test_the_next_dose_is_the_first_still_pending_one_ahead_of_now(db):
    make_medication(db)
    db.commit()

    next_dose = build_today(db, NOW)["next_dose"]
    assert next_dose["scheduled_at"][11:16] == "16:00"


def test_a_dose_already_past_and_unmarked_is_reported_as_overdue(db):
    make_medication(db)
    db.commit()

    overdue = build_today(db, NOW)["overdue_doses"]
    assert [dose["scheduled_at"][11:16] for dose in overdue] == ["08:00"]


def test_marking_it_takes_it_out_of_the_overdue_list(db):
    medication = make_medication(db)
    db.commit()
    medication_service.set_dose_status(db, medication.doses[0].id, DoseStatus.TAKEN.value)
    db.commit()

    assert build_today(db, NOW)["overdue_doses"] == []


def test_todays_appointments_and_the_next_one_are_both_reported(db):
    doctor = make_doctor(db, "Dr. Today")
    appointment_service.create_appointment(
        db,
        {"doctor_id": doctor.id, "scheduled_at": datetime(2026, 9, 10, 17, 0).isoformat()},
    )
    appointment_service.create_appointment(
        db,
        {"doctor_id": doctor.id, "scheduled_at": datetime(2026, 9, 25, 9, 0).isoformat()},
    )
    db.commit()

    payload = build_today(db, NOW)
    assert len(payload["todays_appointments"]) == 1
    assert payload["todays_appointments"][0]["scheduled_at"][:10] == "2026-09-10"
    assert payload["next_appointment"] is not None


def test_only_active_medications_are_listed(db):
    active = make_medication(db, name="Active")
    suspended = make_medication(db, name="Suspended")
    medication_service.suspend_medication(db, suspended.id)
    db.commit()

    names = [item["name"] for item in build_today(db, NOW)["active_medications"]]
    assert names == ["Active"]
    assert active.name in names


def test_a_treatment_finishing_within_the_window_is_flagged(db):
    make_medication(db, name="Ending", end_date=(TODAY + timedelta(days=1)).isoformat())
    db.commit()

    payload = build_today(db, NOW)
    assert [item["name"] for item in payload["ending_soon"]] == ["Ending"]
    assert payload["ending_soon_days"] >= 1


def test_an_open_ended_treatment_never_counts_as_ending_soon(db):
    """The v2 regression: end_date is None, so the subtraction must not happen."""
    make_medication(db, name="Forever", end_date=None)
    db.commit()

    payload = build_today(db, NOW)
    assert payload["ending_soon"] == []
    assert payload["active_medications"][0]["days_remaining"] is None
    assert payload["active_medications"][0]["open_ended"] is True


def test_an_empty_database_still_answers(db):
    payload = build_today(db, NOW)
    assert payload["todays_summary"] == {"taken": 0, "pending": 0, "total": 0}
    assert payload["next_dose"] is None
    assert payload["next_appointment"] is None


def test_the_endpoint_and_its_v2_alias_agree(client):
    today = client.get("/api/today")
    dashboard = client.get("/api/dashboard")
    assert today.status_code == 200 and dashboard.status_code == 200
    assert set(today.json()) == set(dashboard.json())
    assert "todays_summary" in today.json()
