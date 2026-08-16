"""v3 Medication detail: treatment progress and the dose timeline it feeds.

Progress here is *calendar time only* — how much of the period the user typed in
has elapsed. It says nothing about whether a treatment is working, and the tests
below are written to keep it that way.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from app.models.models import DoseStatus
from app.services import medications as medication_service
from app.services.medications import serialize_medication, treatment_progress
from tests.test_medications import make_payload

START = date(2026, 9, 1)
END = date(2026, 9, 10)          # ten days, inclusive


def make_medication(db, **overrides):
    payload = make_payload(
        name="Amoxicillin",
        start_date=START.isoformat(),
        end_date=END.isoformat(),
        frequency_hours=8,
        first_dose_time="10:00",
    )
    payload.update(overrides)
    return medication_service.create_medication(db, payload)


def test_the_first_day_is_day_one_of_ten(db):
    medication = make_medication(db)
    progress = treatment_progress(medication, START)

    assert progress["current_day"] == 1
    assert progress["total_days"] == 10
    assert progress["days_remaining"] == 9
    assert progress["percent"] == 10
    assert progress["not_started"] is False and progress["finished"] is False


def test_the_middle_of_the_treatment(db):
    progress = treatment_progress(make_medication(db), date(2026, 9, 5))
    assert progress["current_day"] == 5
    assert progress["percent"] == 50
    assert progress["days_remaining"] == 5


def test_the_last_day_is_a_hundred_percent(db):
    progress = treatment_progress(make_medication(db), END)
    assert progress["current_day"] == 10
    assert progress["percent"] == 100
    assert progress["days_remaining"] == 0
    assert progress["finished"] is False


def test_before_it_starts_nothing_has_elapsed(db):
    progress = treatment_progress(make_medication(db), START - timedelta(days=3))
    assert progress["not_started"] is True
    assert progress["current_day"] == 0
    assert progress["percent"] == 0


def test_after_it_ends_it_stays_at_a_hundred_percent(db):
    progress = treatment_progress(make_medication(db), END + timedelta(days=5))
    assert progress["finished"] is True
    assert progress["current_day"] == 10          # never overshoots
    assert progress["percent"] == 100
    assert progress["days_remaining"] == 0        # never goes negative


def test_a_one_day_treatment_is_a_whole_treatment(db):
    medication = make_medication(db, end_date=START.isoformat())
    progress = treatment_progress(medication, START)
    assert progress == {
        "current_day": 1, "total_days": 1, "days_remaining": 0, "percent": 100,
        "started": START.isoformat(), "ends": START.isoformat(),
        "not_started": False, "finished": False,
    }


def test_an_open_ended_treatment_has_no_progress_at_all(db):
    """No end date means no percentage - the app does not invent one."""
    medication = make_medication(db, end_date=None)
    assert treatment_progress(medication, date(2026, 9, 5)) is None


def test_the_dates_reported_are_the_ones_the_user_typed(db):
    progress = treatment_progress(make_medication(db), date(2026, 9, 5))
    assert progress["started"] == "2026-09-01"
    assert progress["ends"] == "2026-09-10"


# --------------------------------------------------------------------------- #
# What the detail screen actually receives
# --------------------------------------------------------------------------- #
def test_the_serialized_medication_carries_progress_and_counts(db):
    medication = make_medication(db)
    db.commit()
    medication_service.set_dose_status(db, medication.doses[0].id, DoseStatus.TAKEN.value)
    medication_service.set_dose_status(db, medication.doses[1].id, DoseStatus.SKIPPED.value)
    db.commit()

    data = serialize_medication(
        medication, include_doses=True, reference=datetime(2026, 9, 5, 12, 0)
    )

    assert data["progress"]["current_day"] == 5
    assert data["counts"]["taken"] == 1
    assert data["counts"]["skipped"] == 1
    assert data["counts"]["total"] == len(medication.doses)
    assert data["days_remaining"] == 5
    assert data["open_ended"] is False
    assert len(data["doses"]) == len(medication.doses)


def test_an_open_ended_medication_serializes_without_blowing_up(db):
    """The v2 regression: `None - date` used to crash the dashboard."""
    medication = make_medication(db, end_date=None)
    db.commit()

    data = serialize_medication(medication, reference=datetime(2026, 9, 5, 12, 0))
    assert data["progress"] is None
    assert data["days_remaining"] is None
    assert data["open_ended"] is True


def test_every_dose_in_the_timeline_carries_what_the_row_needs(db):
    medication = make_medication(db)
    db.commit()

    dose = serialize_medication(medication, include_doses=True)["doses"][0]
    for key in ("id", "scheduled_at", "status", "snoozed_until", "medication_name"):
        assert key in dose
    assert dose["scheduled_at"][:10] == "2026-09-01"
    assert dose["snoozed_until"] is None


def test_the_detail_endpoint_returns_the_doses(client):
    created = client.post(
        "/api/medications",
        json=make_payload(start_date="2026-09-01", end_date="2026-09-02",
                          frequency_hours=24, first_dose_time="10:00"),
    ).json()

    detail = client.get(f"/api/medications/{created['id']}").json()
    assert len(detail["doses"]) == 2
    assert detail["progress"]["total_days"] == 2
    assert detail["counts"]["scheduled"] == 2
