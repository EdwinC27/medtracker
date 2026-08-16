"""The optional app lock.

A privacy lock for one machine: one PIN, hashed, guarding one application. The
tests below are the promises it makes — the PIN is never stored, nothing
medical is served while locked, and turning it off needs the PIN.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from app.services import applock
from app.services.errors import ValidationError
from app.services.settings_service import get_settings


@pytest.fixture(autouse=True)
def clean_lock_state():
    """Every test starts with the application locked and nothing remembered."""
    applock.reset_for_tests()
    yield
    applock.reset_for_tests()


def enabled(db, pin="1234"):
    settings = get_settings(db)
    applock.enable(db, settings, pin, pin)
    db.commit()
    return settings


# --------------------------------------------------------------------------- #
# Enabling, and what is stored
# --------------------------------------------------------------------------- #
def test_enabling_stores_a_hash_and_never_the_pin(db):
    settings = enabled(db, "482913")

    assert settings.app_lock_enabled is True
    assert settings.pin_hash and settings.pin_salt
    # The obvious mistake, checked explicitly: nowhere in the row is the PIN.
    row = " ".join(
        str(getattr(settings, column.name)) for column in settings.__table__.columns
    )
    assert "482913" not in row
    assert settings.pin_hash != "482913"
    assert len(settings.pin_hash) == 64          # sha256, hex


def test_the_same_pin_hashes_differently_for_two_people(db):
    """A per-PIN salt, so an identical PIN does not give an identical hash."""
    first, salt_one = applock.hash_pin("1234")
    second, salt_two = applock.hash_pin("1234")
    assert salt_one != salt_two
    assert first != second


def test_a_pin_that_is_too_short_or_too_long_is_refused(db):
    settings = get_settings(db)
    for bad in ("", "1", "123", "1234567", "abcd", None):
        with pytest.raises(ValidationError) as exc:
            applock.enable(db, settings, bad, bad)
        assert exc.value.fields["pin"] == "validation.pin_length"
    assert settings.pin_hash is None


def test_the_two_pins_have_to_match(db):
    settings = get_settings(db)
    with pytest.raises(ValidationError) as exc:
        applock.enable(db, settings, "1234", "4321")
    assert exc.value.fields["confirm_pin"] == "validation.pin_mismatch"
    assert settings.app_lock_enabled is False


def test_setting_it_up_leaves_you_inside(db):
    """Whoever just chose the PIN does not then have to type it."""
    settings = enabled(db)
    assert applock.is_locked(settings) is False


# --------------------------------------------------------------------------- #
# Unlocking
# --------------------------------------------------------------------------- #
def test_the_right_pin_unlocks(db):
    settings = enabled(db)
    applock.lock()
    assert applock.is_locked(settings) is True

    applock.attempt_unlock(db, settings, "1234")
    assert applock.is_locked(settings) is False


def test_the_wrong_pin_does_not(db):
    settings = enabled(db)
    applock.lock()

    with pytest.raises(ValidationError) as exc:
        applock.attempt_unlock(db, settings, "9999")
    assert exc.value.fields["pin"] == "validation.pin_incorrect"
    assert applock.is_locked(settings) is True


def test_an_application_with_no_lock_is_never_locked(db):
    settings = get_settings(db)
    assert applock.is_locked(settings) is False
    settings.app_lock_enabled = True       # on, but no PIN was ever set
    assert applock.is_locked(settings) is False


def test_it_starts_locked(db):
    """Restarting the application is what `reset_for_tests` stands in for."""
    settings = enabled(db)
    applock.reset_for_tests()
    assert applock.is_locked(settings) is True


# --------------------------------------------------------------------------- #
# Wrong guesses
# --------------------------------------------------------------------------- #
def test_repeated_wrong_guesses_buy_a_wait(db):
    settings = enabled(db)
    applock.lock()

    for _ in range(applock.FREE_ATTEMPTS):
        with pytest.raises(ValidationError):
            applock.attempt_unlock(db, settings, "0000")
    assert applock.seconds_until_retry(settings) == 0

    with pytest.raises(ValidationError):
        applock.attempt_unlock(db, settings, "0000")
    assert applock.seconds_until_retry(settings) > 0

    # ...and while the wait runs, even the right PIN has to queue.
    with pytest.raises(ValidationError) as exc:
        applock.attempt_unlock(db, settings, "1234")
    assert exc.value.fields["pin"] == "validation.pin_locked_out"


def test_the_wait_grows_but_stops_growing(db):
    settings = enabled(db)
    applock.lock()
    for _ in range(40):
        with pytest.raises(ValidationError):
            applock.attempt_unlock(db, settings, "0000")
        settings.pin_locked_until = None      # let the next attempt through
    assert settings.pin_failed_attempts == 40

    settings.pin_failed_attempts = 39
    with pytest.raises(ValidationError):
        applock.attempt_unlock(db, settings, "0000")
    assert applock.seconds_until_retry(settings) <= applock.LOCKOUT_MAX_SECONDS


def test_the_right_pin_wipes_the_slate(db):
    settings = enabled(db)
    applock.lock()
    for _ in range(3):
        with pytest.raises(ValidationError):
            applock.attempt_unlock(db, settings, "0000")

    applock.attempt_unlock(db, settings, "1234")
    assert settings.pin_failed_attempts == 0
    assert settings.pin_locked_until is None


def test_the_wait_survives_a_restart(db):
    """Held in the row, not in memory, so closing the window is not a way past it."""
    settings = enabled(db)
    applock.lock()
    for _ in range(applock.FREE_ATTEMPTS + 1):
        with pytest.raises(ValidationError):
            applock.attempt_unlock(db, settings, "0000")

    applock.reset_for_tests()                 # the application restarts
    assert applock.seconds_until_retry(settings) > 0


# --------------------------------------------------------------------------- #
# Changing and disabling
# --------------------------------------------------------------------------- #
def test_changing_the_pin_needs_the_current_one(db):
    settings = enabled(db, "1234")

    with pytest.raises(ValidationError) as exc:
        applock.change(db, settings, "0000", "5678", "5678")
    assert exc.value.fields["current_pin"] == "validation.pin_incorrect"
    assert applock.verify_pin(settings, "1234") is True

    applock.change(db, settings, "1234", "5678", "5678")
    assert applock.verify_pin(settings, "5678") is True
    assert applock.verify_pin(settings, "1234") is False


def test_a_new_pin_still_has_to_be_confirmed(db):
    settings = enabled(db)
    with pytest.raises(ValidationError) as exc:
        applock.change(db, settings, "1234", "5678", "8765")
    assert exc.value.fields["confirm_pin"] == "validation.pin_mismatch"
    assert applock.verify_pin(settings, "1234") is True


def test_disabling_needs_the_current_pin(db):
    settings = enabled(db)

    with pytest.raises(ValidationError) as exc:
        applock.disable(db, settings, "0000")
    assert exc.value.fields["current_pin"] == "validation.pin_incorrect"
    assert settings.app_lock_enabled is True

    applock.disable(db, settings, "1234")
    assert settings.app_lock_enabled is False
    assert settings.pin_hash is None and settings.pin_salt is None


def test_disabling_forgets_the_hash_entirely(db):
    settings = enabled(db)
    applock.disable(db, settings, "1234")
    assert applock.verify_pin(settings, "1234") is False
    assert applock.is_locked(settings) is False


# --------------------------------------------------------------------------- #
# Auto-lock
# --------------------------------------------------------------------------- #
def test_it_locks_itself_after_the_chosen_idle_time(db):
    settings = enabled(db)
    applock.set_auto_lock(db, settings, 15)
    applock.unlock()

    later = datetime(2026, 8, 20, 12, 0)
    applock._session.last_seen = later - timedelta(minutes=14)
    assert applock.is_locked(settings, reference=later) is False

    applock._session.last_seen = later - timedelta(minutes=15)
    assert applock.is_locked(settings, reference=later) is True


def test_activity_keeps_it_open(db):
    settings = enabled(db)
    applock.set_auto_lock(db, settings, 5)
    applock.unlock()
    applock._session.last_seen = datetime(2026, 8, 20, 11, 0)

    applock.touch()                            # something happened just now
    assert applock.is_locked(settings) is False


def test_never_means_never(db):
    settings = enabled(db)
    applock.set_auto_lock(db, settings, 0)
    applock.unlock()
    applock._session.last_seen = datetime(2020, 1, 1, 0, 0)
    assert applock.is_locked(settings) is False


def test_only_the_offered_intervals_are_accepted(db):
    settings = enabled(db)
    with pytest.raises(ValidationError) as exc:
        applock.set_auto_lock(db, settings, 7)
    assert exc.value.fields["auto_lock_minutes"] == "validation.auto_lock_invalid"
