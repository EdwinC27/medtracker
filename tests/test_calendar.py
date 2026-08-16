"""v3 Calendar: what lands on which day, the filters, and the range guard."""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest

from app.services import appointments as appointment_service
from app.services import medications as medication_service
from app.services.calendar_service import (
    build_calendar,
    day_range,
    month_range,
    parse_anchor,
    range_for,
    week_range,
)
from app.services.errors import ValidationError
from tests.test_appointments import make_doctor
from tests.test_medications import make_payload

# A month in the future, so nothing here depends on the real clock.
START = date(2026, 9, 10)
END = date(2026, 9, 12)


def make_medication(db, **overrides):
    payload = make_payload(
        name="Amoxicillin",
        start_date=START.isoformat(),
        end_date=END.isoformat(),
        frequency_hours=24,
        first_dose_time="10:00",
    )
    payload.update(overrides)
    return medication_service.create_medication(db, payload)


def events_of(payload, kind):
    return [event for event in payload["events"] if event["type"] == kind]


# --------------------------------------------------------------------------- #
# Events
# --------------------------------------------------------------------------- #
def test_doses_appear_on_their_own_day_with_their_own_time(db):
    make_medication(db)
    db.commit()

    payload = build_calendar(db, date(2026, 9, 1), date(2026, 9, 30))
    doses = events_of(payload, "dose")

    assert [event["date"] for event in doses] == ["2026-09-10", "2026-09-11", "2026-09-12"]
    assert {event["time"] for event in doses} == {"10:00"}
    assert doses[0]["title"] == "Amoxicillin"
    assert doses[0]["href"] == f"/medications/{doses[0]['medication_id']}"
    assert doses[0]["status"] == "scheduled"


def test_an_appointment_appears_with_its_doctor_as_the_title(db):
    doctor = make_doctor(db, "Dr. Calendar")
    appointment_service.create_appointment(
        db,
        {
            "doctor_id": doctor.id,
            "scheduled_at": datetime(2026, 9, 21, 16, 30).isoformat(),
            "treatment": "Follow-up",
        },
    )
    db.commit()

    payload = build_calendar(db, date(2026, 9, 1), date(2026, 9, 30))
    appointments = events_of(payload, "appointment")

    assert len(appointments) == 1
    assert appointments[0]["date"] == "2026-09-21"
    assert appointments[0]["time"] == "16:30"
    assert appointments[0]["title"] == "Dr. Calendar"
    assert appointments[0]["subtitle"] == "Follow-up"


def test_a_treatment_marks_its_first_and_its_last_day(db):
    make_medication(db)
    db.commit()

    treatments = events_of(build_calendar(db, date(2026, 9, 1), date(2026, 9, 30)), "treatment")
    by_boundary = {event["boundary"]: event["date"] for event in treatments}

    assert by_boundary == {"start": "2026-09-10", "end": "2026-09-12"}
    assert all(event["time"] is None for event in treatments)


def test_an_open_ended_treatment_has_a_start_but_no_end_marker(db):
    make_medication(db, end_date=None)
    db.commit()

    treatments = events_of(build_calendar(db, date(2026, 9, 1), date(2026, 9, 30)), "treatment")
    assert [event["boundary"] for event in treatments] == ["start"]


def test_a_treatment_boundary_outside_the_window_is_not_returned(db):
    make_medication(db)
    db.commit()

    # Only the 11th and 12th are visible, so the start marker must not show up.
    treatments = events_of(build_calendar(db, date(2026, 9, 11), date(2026, 9, 12)), "treatment")
    assert [event["boundary"] for event in treatments] == ["end"]


# --------------------------------------------------------------------------- #
# Filters
# --------------------------------------------------------------------------- #
def test_the_scope_filter_selects_one_kind_of_event(db):
    make_medication(db)
    doctor = make_doctor(db, "Dr. Scope")
    appointment_service.create_appointment(
        db,
        {"doctor_id": doctor.id, "scheduled_at": datetime(2026, 9, 21, 9, 0).isoformat()},
    )
    db.commit()

    window = (date(2026, 9, 1), date(2026, 9, 30))
    assert build_calendar(db, *window, scope="medications")["counts"] == {
        "dose": 3, "appointment": 0, "treatment": 0
    }
    assert build_calendar(db, *window, scope="appointments")["counts"] == {
        "dose": 0, "appointment": 1, "treatment": 0
    }
    assert build_calendar(db, *window, scope="treatments")["counts"] == {
        "dose": 0, "appointment": 0, "treatment": 2
    }
    assert build_calendar(db, *window, scope="all")["counts"]["dose"] == 3


def test_an_unknown_scope_falls_back_to_showing_everything(db):
    make_medication(db)
    db.commit()
    payload = build_calendar(db, date(2026, 9, 1), date(2026, 9, 30), scope="nonsense")
    assert payload["scope"] == "all"
    assert payload["counts"]["dose"] == 3


def test_filtering_by_medication_hides_the_other_one(db):
    kept = make_medication(db, name="Kept")
    make_medication(db, name="Other")
    db.commit()

    payload = build_calendar(db, date(2026, 9, 1), date(2026, 9, 30), medication_id=kept.id)
    assert {event["title"] for event in payload["events"]} == {"Kept"}


def test_filtering_by_doctor_hides_the_other_doctors_appointments(db):
    mine = make_doctor(db, "Dr. Mine")
    theirs = make_doctor(db, "Dr. Theirs")
    for doctor in (mine, theirs):
        appointment_service.create_appointment(
            db,
            {"doctor_id": doctor.id, "scheduled_at": datetime(2026, 9, 21, 9, 0).isoformat()},
        )
    db.commit()

    payload = build_calendar(
        db, date(2026, 9, 1), date(2026, 9, 30), scope="appointments", doctor_id=mine.id
    )
    assert [event["title"] for event in payload["events"]] == ["Dr. Mine"]


def test_events_come_back_in_chronological_order(db):
    doctor = make_doctor(db, "Dr. Order")
    appointment_service.create_appointment(
        db,
        {"doctor_id": doctor.id, "scheduled_at": datetime(2026, 9, 11, 8, 0).isoformat()},
    )
    make_medication(db)
    db.commit()

    payload = build_calendar(db, date(2026, 9, 1), date(2026, 9, 30))
    stamps = [(event["date"], event["time"] or "") for event in payload["events"]]
    assert stamps == sorted(stamps)


# --------------------------------------------------------------------------- #
# Ranges
# --------------------------------------------------------------------------- #
def test_the_month_grid_runs_from_a_monday_to_a_sunday(db):
    start, end = month_range(date(2026, 9, 15))
    assert start == date(2026, 8, 31)      # the Monday before September 1st
    assert end == date(2026, 10, 4)        # the Sunday after September 30th
    assert start.weekday() == 0 and end.weekday() == 6
    assert start <= date(2026, 9, 1) and date(2026, 9, 30) <= end


def test_the_month_grid_handles_december(db):
    start, end = month_range(date(2026, 12, 5))
    assert start <= date(2026, 12, 1) and date(2026, 12, 31) <= end


def test_a_week_starts_on_monday_and_a_day_is_just_itself(db):
    assert week_range(date(2026, 9, 17)) == (date(2026, 9, 14), date(2026, 9, 20))
    assert day_range(date(2026, 9, 17)) == (date(2026, 9, 17), date(2026, 9, 17))
    assert range_for("week", date(2026, 9, 17)) == week_range(date(2026, 9, 17))
    assert range_for("day", date(2026, 9, 17)) == day_range(date(2026, 9, 17))
    assert range_for("anything-else", date(2026, 9, 17)) == month_range(date(2026, 9, 17))


def test_a_window_wider_than_the_cap_is_refused(db):
    with pytest.raises(ValidationError) as exc:
        build_calendar(db, date(2026, 1, 1), date(2026, 12, 31))
    assert exc.value.fields["range"] == "validation.range_too_wide"


def test_a_backwards_window_is_refused(db):
    with pytest.raises(ValidationError) as exc:
        build_calendar(db, date(2026, 9, 10), date(2026, 9, 1))
    assert exc.value.fields["range"] == "validation.date_invalid"


def test_the_widest_allowed_window_is_accepted(db):
    start = date(2026, 9, 1)
    payload = build_calendar(db, start, start + timedelta(days=62))
    assert payload["events"] == []


def test_parse_anchor_reads_a_date_and_rejects_rubbish(db):
    fallback = datetime(2026, 8, 16, 12, 0)
    assert parse_anchor(None, fallback) == date(2026, 8, 16)
    assert parse_anchor("2026-09-15", fallback) == date(2026, 9, 15)
    assert parse_anchor("2026-09-15T10:00:00", fallback) == date(2026, 9, 15)
    with pytest.raises(ValidationError) as exc:
        parse_anchor("not-a-date", fallback)
    assert exc.value.fields["anchor"] == "validation.date_invalid"


# --------------------------------------------------------------------------- #
# Through the API
# --------------------------------------------------------------------------- #
def test_the_endpoint_echoes_the_view_and_the_anchor(client):
    response = client.get("/api/calendar?view=week&anchor=2026-09-17")
    assert response.status_code == 200
    body = response.json()
    assert body["view"] == "week"
    assert body["anchor"] == "2026-09-17"
    assert body["start"] == "2026-09-14" and body["end"] == "2026-09-20"


def test_the_endpoint_falls_back_to_the_month_view(client):
    body = client.get("/api/calendar?view=decade&anchor=2026-09-17").json()
    assert body["view"] == "month"


def test_the_endpoint_rejects_a_broken_anchor(client):
    response = client.get("/api/calendar?anchor=15-99-2026")
    assert response.status_code == 422
    assert response.json()["fields"]["anchor"] == "validation.date_invalid"
