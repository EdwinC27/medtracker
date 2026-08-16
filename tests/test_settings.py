"""Settings, including the global first-dose-time behaviour."""

from __future__ import annotations

from datetime import time, timedelta

import pytest

from app.models.models import DoseStatus
from app.services import medications as medication_service
from app.services.errors import ValidationError
from app.services.settings_service import get_settings, update_settings
from app.utils.timeutil import now_local
from tests.test_medications import make_payload


def test_defaults(db):
    settings = get_settings(db)
    assert settings.default_first_dose_time == time(10, 0)
    assert settings.language is None  # follow the browser
    assert settings.appt_reminder_days_3 is True


def test_new_medications_use_the_global_first_dose_time(db):
    update_settings(db, {"default_first_dose_time": "08:30"})
    payload = make_payload()
    payload.pop("first_dose_time")
    medication = medication_service.create_medication(db, payload)
    assert medication.first_dose_time == time(8, 30)


def test_changing_the_global_time_realigns_active_medications(db):
    medication = medication_service.create_medication(db, make_payload())
    assert medication.first_dose_time == time(10, 0)

    _settings, recalculated = update_settings(db, {"default_first_dose_time": "07:00"})

    assert medication.first_dose_time == time(7, 0)
    assert recalculated > 0
    future = [d for d in medication.doses if d.scheduled_at > now_local()]
    assert future, "there should still be upcoming doses"
    assert {d.scheduled_at.hour % 24 for d in future} <= {7, 15, 23}


def test_marked_doses_are_never_touched_by_a_global_time_change(db):
    today = now_local().date()
    medication = medication_service.create_medication(
        db,
        make_payload(
            start_date=(today - timedelta(days=2)).isoformat(),
            end_date=(today + timedelta(days=5)).isoformat(),
        ),
    )
    past = [d for d in medication.doses if d.scheduled_at < now_local()]
    assert past
    medication_service.set_dose_status(db, past[0].id, DoseStatus.TAKEN.value)
    marked_time = past[0].scheduled_at

    update_settings(db, {"default_first_dose_time": "07:00"})

    still_there = [d for d in medication.doses if d.scheduled_at == marked_time]
    assert len(still_there) == 1
    assert still_there[0].status == DoseStatus.TAKEN.value


def test_suspended_medications_are_not_realigned(db):
    medication = medication_service.create_medication(db, make_payload())
    medication_service.suspend_medication(db, medication.id)
    update_settings(db, {"default_first_dose_time": "07:00"})
    assert medication.first_dose_time == time(10, 0)
    assert not [d for d in medication.doses if d.scheduled_at > now_local()]


def test_language_preference_is_persisted(db):
    settings, _ = update_settings(db, {"language": "es"})
    assert settings.language == "es"
    settings, _ = update_settings(db, {"language": None})
    assert settings.language is None


def test_invalid_ranges_are_rejected(db):
    with pytest.raises(ValidationError) as exc:
        update_settings(db, {"ending_soon_days": 900})
    assert exc.value.fields["ending_soon_days"] == "validation.ending_soon_range"

    with pytest.raises(ValidationError) as exc:
        update_settings(db, {"missed_after_minutes": 1})
    assert exc.value.fields["missed_after_minutes"] == "validation.missed_range"


def test_invalid_time_is_rejected(db):
    with pytest.raises(ValidationError) as exc:
        update_settings(db, {"default_first_dose_time": "not a time"})
    assert "default_first_dose_time" in exc.value.fields
