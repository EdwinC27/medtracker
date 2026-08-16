"""The two confirmation rules added in v2.

Both live in `app/services/scheduling.py` so they are testable here rather than
only existing as JavaScript.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest

from app.models.models import MedicationStatus
from app.services import medications as medication_service
from app.services.scheduling import (
    requires_complete_confirmation,
    requires_taken_confirmation,
    taken_confirmation_threshold,
)
from app.utils.timeutil import now_local
from tests.test_medications import make_payload

SCHEDULED = datetime(2026, 8, 20, 10, 0)


# --------------------------------------------------------------------------- #
# Marking a dose as taken
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "hour,minute,expected",
    [
        (9, 0, True),    # an hour early -> ask
        (9, 29, True),   # 31 minutes early -> ask
        (9, 30, False),  # exactly 30 minutes early -> just do it
        (9, 45, False),
        (10, 0, False),  # on time
        (11, 30, False),  # late; still no question
    ],
)
def test_the_thirty_minute_rule(hour, minute, expected):
    now = SCHEDULED.replace(hour=hour, minute=minute)
    assert requires_taken_confirmation(SCHEDULED, now) is expected


def test_the_threshold_is_exactly_thirty_minutes_before():
    assert taken_confirmation_threshold(SCHEDULED) == datetime(2026, 8, 20, 9, 30)


def test_the_threshold_is_exposed_to_the_frontend(db):
    """The UI compares against this instead of re-deriving the rule."""
    medication = medication_service.create_medication(db, make_payload())
    dose = medication.doses[0]
    payload = medication_service.serialize_dose(dose)

    assert payload["confirm_taken_before"] == (
        dose.scheduled_at - timedelta(minutes=30)
    ).isoformat()


def test_the_confirmation_never_blocks_the_action(db):
    """It is a question, not a rule: confirming still marks the dose."""
    medication = medication_service.create_medication(db, make_payload())
    future = [d for d in medication.doses if d.scheduled_at > now_local()][-1]

    dose = medication_service.set_dose_status(db, future.id, "taken")
    assert dose.status == "taken"


# --------------------------------------------------------------------------- #
# Completing a medication
# --------------------------------------------------------------------------- #
def test_completing_before_the_end_date_asks(db):
    today = now_local().date()
    medication = medication_service.create_medication(
        db, make_payload(end_date=(today + timedelta(days=5)).isoformat())
    )
    assert requires_complete_confirmation(medication, today) is True


def test_completing_on_the_end_date_does_not_ask(db):
    today = now_local().date()
    medication = medication_service.create_medication(
        db, make_payload(end_date=today.isoformat())
    )
    assert requires_complete_confirmation(medication, today) is False


def test_completing_after_the_end_date_does_not_ask(db):
    medication = medication_service.create_medication(
        db, make_payload(end_date=date(2026, 8, 20).isoformat())
    )
    medication.status = MedicationStatus.ACTIVE.value
    assert requires_complete_confirmation(medication, date(2026, 8, 25)) is False


def test_an_open_ended_treatment_always_asks(db):
    medication = medication_service.create_medication(db, make_payload(end_date=None))
    assert medication.end_date is None
    assert requires_complete_confirmation(medication, now_local().date()) is True


def test_a_treatment_that_is_not_active_never_asks(db):
    today = now_local().date()
    medication = medication_service.create_medication(
        db, make_payload(end_date=(today + timedelta(days=5)).isoformat())
    )
    medication_service.suspend_medication(db, medication.id)
    assert requires_complete_confirmation(medication, today) is False


def test_the_flag_travels_with_the_serialized_medication(db):
    today = now_local().date()
    medication = medication_service.create_medication(
        db, make_payload(end_date=(today + timedelta(days=5)).isoformat())
    )
    payload = medication_service.serialize_medication(medication)
    assert payload["needs_complete_confirmation"] is True
    assert payload["open_ended"] is False
