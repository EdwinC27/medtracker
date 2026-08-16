"""An optional local lock for the application.

This is a privacy lock for a single-user machine, not an account system. There
is no user name, no password, no session server, no token to steal — one PIN,
checked against a hash, guarding one desktop application.

What it does guarantee
----------------------
* The PIN is never stored. Only PBKDF2-HMAC-SHA256 of it, with a random salt
  per PIN and a deliberately slow iteration count.
* Nothing medical is served while the application is locked: the pages redirect
  to the lock screen and the API answers 423 instead of data.
* Turning the lock off, or changing the PIN, needs the current PIN.
* Repeated wrong guesses buy the guesser a wait, and the wait survives a
  restart because it is stored, not held in memory.

What it deliberately does not try to be
---------------------------------------
The database itself is not encrypted (out of scope for v4, and stated as such),
so anyone with the file and a SQLite viewer can read it. The lock stops someone
who picks up the running machine, which is the threat it is meant for.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta

from app.services.errors import ValidationError
from app.utils.timeutil import now_local

logger = logging.getLogger(__name__)

PIN_MIN_LENGTH = 4
PIN_MAX_LENGTH = 6
# 200k rounds is imperceptible once per unlock and expensive in bulk, which is
# the whole point of a slow hash for a four-digit secret.
PBKDF2_ROUNDS = 200_000

AUTO_LOCK_OPTIONS = (0, 5, 15, 30, 60)

# Friction, not a vault door: five tries, then a wait that grows with each
# further failure and stops at five minutes.
FREE_ATTEMPTS = 5
LOCKOUT_STEP_SECONDS = 30
LOCKOUT_MAX_SECONDS = 300


# --------------------------------------------------------------------------- #
# Hashing
# --------------------------------------------------------------------------- #
def normalise_pin(raw: str | None) -> str:
    """Digits only, and only as many as we allow."""
    pin = "".join(ch for ch in str(raw or "") if ch.isdigit())
    if not pin or not PIN_MIN_LENGTH <= len(pin) <= PIN_MAX_LENGTH:
        raise ValidationError({"pin": "validation.pin_length"})
    return pin


def hash_pin(pin: str, salt: str | None = None) -> tuple[str, str]:
    """Returns `(hash_hex, salt_hex)`."""
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", pin.encode("utf-8"), bytes.fromhex(salt), PBKDF2_ROUNDS
    )
    return digest.hex(), salt


def verify_pin(settings, raw: str | None) -> bool:
    """Constant-time comparison against the stored hash."""
    if not settings.pin_hash or not settings.pin_salt:
        return False
    pin = "".join(ch for ch in str(raw or "") if ch.isdigit())
    if not pin:
        return False
    candidate, _salt = hash_pin(pin, settings.pin_salt)
    return hmac.compare_digest(candidate, settings.pin_hash)


# --------------------------------------------------------------------------- #
# The lock, in memory
# --------------------------------------------------------------------------- #
@dataclass
class _Session:
    """Whether this running application is currently unlocked.

    In memory on purpose: the specification asks the application to lock when it
    starts, and a state that does not survive the process gives that for free.

    `token` is what makes the unlock belong to the browser that typed the PIN
    rather than to the process. It is a fresh random string on every unlock,
    handed back in an HttpOnly cookie and never written anywhere — so it dies
    with the process, exactly like the rest of this state.
    """

    unlocked: bool = False
    since: datetime | None = None
    last_seen: datetime | None = None
    token: str | None = None


_session = _Session()

COOKIE_NAME = "medtracker_unlock"


def reset_for_tests() -> None:
    """Forget the unlock state (used by the tests, and by a full shutdown)."""
    global _session
    _session = _Session()


def unlock() -> str:
    """Unlock, and return the token that proves it to one browser."""
    now = now_local()
    _session.unlocked = True
    _session.since = now
    _session.last_seen = now
    _session.token = secrets.token_urlsafe(32)
    return _session.token


def lock() -> None:
    _session.unlocked = False
    _session.since = None
    _session.last_seen = None
    _session.token = None


def current_token() -> str | None:
    return _session.token


def touch() -> None:
    """Record activity, which is what auto-lock measures idleness against.

    Called only for requests a person actually caused. The application polls
    itself — the notification bell every 30 seconds, Today every minute — and
    counting those would mean an open tab kept the application unlocked for
    ever, which is precisely the situation auto-lock exists for.
    """
    if _session.unlocked:
        _session.last_seen = now_local()


def is_locked(settings, reference: datetime | None = None, token: str | None = None) -> bool:
    """The one question the rest of the application asks.

    `token`, when given, is the caller's proof that *it* is the browser that
    unlocked. Passing None asks the process-wide question instead, which is what
    the tray, System Status and the tests want.
    """
    if not settings.app_lock_enabled or not settings.pin_hash:
        return False
    if not _session.unlocked:
        return True

    minutes = int(settings.auto_lock_minutes or 0)
    if minutes > 0 and _session.last_seen is not None:
        now = reference or now_local()
        if now - _session.last_seen >= timedelta(minutes=minutes):
            lock()
            logger.info("Locked after %s minutes of inactivity", minutes)
            return True

    if token is not None and not (
        _session.token and hmac.compare_digest(token, _session.token)
    ):
        return True
    return False


def state(settings, token: str | None = None) -> dict:
    return {
        "enabled": bool(settings.app_lock_enabled and settings.pin_hash),
        "configured": bool(settings.pin_hash),
        "locked": is_locked(settings, token=token),
        "auto_lock_minutes": int(settings.auto_lock_minutes or 0),
        "auto_lock_options": list(AUTO_LOCK_OPTIONS),
        "pin_min_length": PIN_MIN_LENGTH,
        "pin_max_length": PIN_MAX_LENGTH,
    }


# --------------------------------------------------------------------------- #
# Attempt throttling
# --------------------------------------------------------------------------- #
def seconds_until_retry(settings, reference: datetime | None = None) -> int:
    if not settings.pin_locked_until:
        return 0
    now = reference or now_local()
    remaining = (settings.pin_locked_until - now).total_seconds()
    return max(int(remaining + 0.999), 0)


def _register_failure(db, settings) -> None:
    settings.pin_failed_attempts = int(settings.pin_failed_attempts or 0) + 1
    over = settings.pin_failed_attempts - FREE_ATTEMPTS
    if over > 0:
        wait = min(LOCKOUT_STEP_SECONDS * over, LOCKOUT_MAX_SECONDS)
        settings.pin_locked_until = now_local() + timedelta(seconds=wait)
        logger.warning(
            "Wrong PIN %s times; further attempts blocked for %ss",
            settings.pin_failed_attempts,
            wait,
        )
    db.flush()


def _clear_failures(db, settings) -> None:
    settings.pin_failed_attempts = 0
    settings.pin_locked_until = None
    db.flush()


# --------------------------------------------------------------------------- #
# Commands
# --------------------------------------------------------------------------- #
def attempt_unlock(db, settings, raw_pin: str | None) -> dict:
    """Check a PIN and unlock on success. Raises on a wrong one."""
    if not settings.app_lock_enabled or not settings.pin_hash:
        unlock()
        return state(settings)

    wait = seconds_until_retry(settings)
    if wait:
        raise ValidationError({"pin": "validation.pin_locked_out"})

    if not verify_pin(settings, raw_pin):
        _register_failure(db, settings)
        db.commit()
        raise ValidationError({"pin": "validation.pin_incorrect"})

    _clear_failures(db, settings)
    unlock()
    db.commit()
    logger.info("Application unlocked")
    return state(settings)


def _require_current_pin(db, settings, raw_pin: str | None) -> None:
    """Used by every change to the lock itself. Never skippable."""
    if not settings.pin_hash:
        return
    if seconds_until_retry(settings):
        raise ValidationError({"current_pin": "validation.pin_locked_out"})
    if not verify_pin(settings, raw_pin):
        _register_failure(db, settings)
        db.commit()
        raise ValidationError({"current_pin": "validation.pin_incorrect"})
    _clear_failures(db, settings)


def enable(db, settings, pin: str | None, confirm: str | None) -> dict:
    """Turn the lock on with a new PIN."""
    if settings.app_lock_enabled and settings.pin_hash:
        raise ValidationError({"pin": "validation.pin_already_enabled"})

    clean = normalise_pin(pin)
    if clean != "".join(ch for ch in str(confirm or "") if ch.isdigit()):
        raise ValidationError({"confirm_pin": "validation.pin_mismatch"})

    settings.pin_hash, settings.pin_salt = hash_pin(clean)
    settings.app_lock_enabled = True
    _clear_failures(db, settings)
    unlock()  # the person who just set it is obviously allowed in
    db.flush()
    logger.info("App lock enabled")
    return state(settings)


def change(db, settings, current: str | None, pin: str | None, confirm: str | None) -> dict:
    """Replace the PIN, after proving the current one."""
    if not settings.pin_hash:
        raise ValidationError({"current_pin": "validation.pin_not_enabled"})
    _require_current_pin(db, settings, current)

    clean = normalise_pin(pin)
    if clean != "".join(ch for ch in str(confirm or "") if ch.isdigit()):
        raise ValidationError({"confirm_pin": "validation.pin_mismatch"})

    settings.pin_hash, settings.pin_salt = hash_pin(clean)
    db.flush()
    logger.info("PIN changed")
    return state(settings)


def disable(db, settings, current: str | None) -> dict:
    """Turn the lock off, which also needs the current PIN."""
    if not settings.pin_hash:
        return state(settings)
    _require_current_pin(db, settings, current)

    settings.app_lock_enabled = False
    settings.pin_hash = None
    settings.pin_salt = None
    settings.auto_lock_minutes = 0
    _clear_failures(db, settings)
    unlock()
    db.flush()
    logger.info("App lock disabled")
    return state(settings)


def set_auto_lock(db, settings, minutes) -> dict:
    try:
        value = int(minutes)
    except (TypeError, ValueError):
        raise ValidationError({"auto_lock_minutes": "validation.auto_lock_invalid"}) from None
    if value not in AUTO_LOCK_OPTIONS:
        raise ValidationError({"auto_lock_minutes": "validation.auto_lock_invalid"})
    settings.auto_lock_minutes = value
    db.flush()
    return state(settings)
