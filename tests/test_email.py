"""The e-mail channel: configuration, the secret store, and message content.

No message is ever sent to a real server here — `smtplib` is replaced by a
recording double, which is also what lets these tests assert the exact subject
and body a real inbox would receive.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from app.models.models import Notification, NotificationType
from app.notifications import dispatcher
from app.notifications.email import EmailConfig, config_from_settings, send_email
from app.services import medications as medication_service
from app.services.errors import ValidationError
from app.services.settings_service import get_settings, update_settings
from app.utils import secretstore
from app.utils.timeutil import now_local
from tests.test_appointments import make_appointment
from tests.test_medications import make_payload

SMTP_SETTINGS = {
    "email_recipient": "edwin@example.com",
    "email_sender": "medtracker@example.com",
    "smtp_host": "smtp.example.com",
    "smtp_port": 587,
    "smtp_username": "medtracker@example.com",
    "smtp_password": "hunter2",
    "smtp_security": "starttls",
    "email_notifications": True,
}


class FakeSMTP:
    """Records what would have been sent."""

    sent: list = []
    logins: list = []

    def __init__(self, host, port, timeout=None, context=None):
        self.host, self.port = host, port

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def ehlo(self):
        pass

    def starttls(self, context=None):
        pass

    def login(self, username, password):
        FakeSMTP.logins.append((username, password))

    def send_message(self, message):
        FakeSMTP.sent.append(message)


@pytest.fixture()
def smtp(monkeypatch):
    FakeSMTP.sent = []
    FakeSMTP.logins = []
    monkeypatch.setattr("smtplib.SMTP", FakeSMTP)
    monkeypatch.setattr("smtplib.SMTP_SSL", FakeSMTP)
    return FakeSMTP


# --------------------------------------------------------------------------- #
# Configuration and the stored password
# --------------------------------------------------------------------------- #
def test_the_password_is_never_stored_in_clear_text(db):
    update_settings(db, dict(SMTP_SETTINGS))
    settings = get_settings(db)

    stored = settings.smtp_password_protected
    assert stored
    assert "hunter2" not in stored
    # …but the application can still read it back on this machine.
    assert secretstore.unprotect(stored) == "hunter2"


def test_the_password_is_not_exposed_to_the_browser(db):
    from app.services.settings_service import settings_to_dict

    update_settings(db, dict(SMTP_SETTINGS))
    payload = settings_to_dict(get_settings(db))

    assert "smtp_password" not in payload
    assert "smtp_password_protected" not in payload
    assert payload["smtp_password_set"] is True
    assert payload["secret_backend"] in {"dpapi", "file"}


def test_an_untouched_password_field_keeps_the_stored_one(db):
    update_settings(db, dict(SMTP_SETTINGS))
    before = get_settings(db).smtp_password_protected

    update_settings(db, {"smtp_host": "smtp2.example.com"})  # no smtp_password key
    assert get_settings(db).smtp_password_protected == before


def test_clearing_the_password_forgets_it(db):
    update_settings(db, dict(SMTP_SETTINGS))
    update_settings(db, {"smtp_password": "", "email_notifications": False})
    assert get_settings(db).smtp_password_protected is None


def test_enabling_email_without_a_host_is_refused(db):
    with pytest.raises(ValidationError) as exc:
        update_settings(db, {"email_notifications": True})
    assert exc.value.fields["email_notifications"] == "validation.email_incomplete"


def test_a_malformed_address_is_refused(db):
    with pytest.raises(ValidationError) as exc:
        update_settings(db, {"email_recipient": "not-an-address"})
    assert exc.value.fields["email_recipient"] == "validation.email_invalid"


def test_a_bad_port_is_refused(db):
    with pytest.raises(ValidationError) as exc:
        update_settings(db, {"smtp_port": 99999})
    assert exc.value.fields["smtp_port"] == "validation.port_invalid"


def test_the_send_configuration_is_built_from_settings(db):
    update_settings(db, dict(SMTP_SETTINGS))
    config = config_from_settings(get_settings(db))

    assert config.host == "smtp.example.com"
    assert config.port == 587
    assert config.recipient == "edwin@example.com"
    assert config.password == "hunter2"
    assert config.is_complete


# --------------------------------------------------------------------------- #
# Sending
# --------------------------------------------------------------------------- #
def test_a_message_reaches_the_configured_recipient(db, smtp):
    update_settings(db, dict(SMTP_SETTINGS))
    config = config_from_settings(get_settings(db))

    sent, error = send_email(config, "Subject", "Body")

    assert sent and error is None
    assert len(smtp.sent) == 1
    assert smtp.sent[0]["To"] == "edwin@example.com"
    assert smtp.sent[0]["From"] == "medtracker@example.com"
    assert smtp.sent[0]["Subject"] == "Subject"
    assert smtp.logins == [("medtracker@example.com", "hunter2")]


def test_an_incomplete_configuration_sends_nothing(db, smtp):
    sent, error = send_email(EmailConfig("", 0, None, None, "", ""), "s", "b")
    assert not sent
    assert error == "incomplete configuration"
    assert smtp.sent == []


def test_a_refused_connection_is_reported_not_raised(db, monkeypatch):
    def explode(*args, **kwargs):
        raise OSError("connection refused")

    monkeypatch.setattr("smtplib.SMTP", explode)
    update_settings(db, dict(SMTP_SETTINGS))

    sent, error = send_email(config_from_settings(get_settings(db)), "s", "b")
    assert not sent
    assert "connection refused" in error


def test_nothing_is_sent_while_the_channel_is_off(db, smtp):
    """The channel switch is independent of the other two."""
    update_settings(db, dict(SMTP_SETTINGS))
    update_settings(db, {"email_notifications": False})

    today = now_local().date()
    medication_service.create_medication(
        db,
        make_payload(
            start_date=today.isoformat(),
            end_date=today.isoformat(),
            first_dose_time=(now_local() - timedelta(minutes=5)).strftime("%H:%M"),
        ),
    )
    db.commit()

    summary = dispatcher.run_tick(db, send_windows=False)
    assert summary["dose_notifications"] >= 1  # still queued for the other channels
    assert summary["emails_sent"] == 0
    assert smtp.sent == []


def test_a_dose_reminder_is_emailed_when_the_channel_is_on(db, smtp):
    update_settings(db, dict(SMTP_SETTINGS))
    today = now_local().date()
    medication_service.create_medication(
        db,
        make_payload(
            name="Amoxicillin",
            start_date=today.isoformat(),
            end_date=today.isoformat(),
            first_dose_time=(now_local() - timedelta(minutes=5)).strftime("%H:%M"),
        ),
    )
    db.commit()

    summary = dispatcher.run_tick(db, send_windows=False)
    assert summary["emails_sent"] >= 1
    assert any("Amoxicillin" in m["Subject"] for m in smtp.sent)


def test_an_appointment_reminder_is_emailed_too(db, smtp):
    update_settings(db, dict(SMTP_SETTINGS))
    make_appointment(db, when=now_local() + timedelta(hours=23, minutes=30))
    db.commit()

    summary = dispatcher.run_tick(db, send_windows=False)
    assert summary["emails_sent"] >= 1
    assert any("Dr." in m["Subject"] for m in smtp.sent)


# --------------------------------------------------------------------------- #
# Content and language
# --------------------------------------------------------------------------- #
def _dose_notification(kind="at_time"):
    """A notification exactly as the scheduler would have written it."""
    import json

    return Notification(
        type=NotificationType.DOSE.value,
        kind=kind,
        dedupe_key=f"test:{kind}",
        reference_id=1,
        fire_at=datetime(2026, 8, 20, 10, 0),
        title_key="notification.medication_title",
        body_key="notification.dose_body",
        payload=json.dumps(
            {
                "dose_id": 1,
                "medication_id": 1,
                "name": "Amoxicillin",
                "dose_amount": "500",
                "dose_unit": "mg",
                "quantity": 1,
                "form": "capsule",
                "scheduled_at": datetime(2026, 8, 20, 10, 0).isoformat(),
            }
        ),
    )


def test_the_english_email_follows_the_requested_shape(db):
    subject, body = dispatcher.render_email(_dose_notification(), "en")

    assert subject == "Medication reminder: Amoxicillin"
    assert "Medication reminder" in body
    assert "It's time to take:" in body
    assert "Amoxicillin" in body
    assert "Dose:" in body
    assert "500 mg — 1 capsule" in body
    assert "Scheduled time:" in body
    assert "10:00 AM" in body


def test_the_spanish_email_follows_the_requested_shape(db):
    subject, body = dispatcher.render_email(_dose_notification(), "es")

    assert subject == "Recordatorio de medicamento: Amoxicillin"
    assert "Recordatorio de medicamento" in body
    assert "Es hora de tomar:" in body
    assert "Dosis:" in body
    assert "500 mg — 1 cápsula" in body
    assert "Hora programada:" in body
    assert "10:00" in body


def test_the_email_language_follows_the_saved_preference(db, smtp):
    update_settings(db, dict(SMTP_SETTINGS))
    update_settings(db, {"language": "es"})

    today = now_local().date()
    medication_service.create_medication(
        db,
        make_payload(
            name="Amoxicillin",
            start_date=today.isoformat(),
            end_date=today.isoformat(),
            first_dose_time=(now_local() - timedelta(minutes=5)).strftime("%H:%M"),
        ),
    )
    db.commit()
    dispatcher.run_tick(db, send_windows=False)

    assert smtp.sent
    assert smtp.sent[0]["Subject"].startswith("Recordatorio de medicamento")


def test_every_offset_produces_its_own_wording(db):
    for kind, phrase_en in (
        ("before_30", "In 30 minutes"),
        ("before_15", "In 15 minutes"),
        ("before_5", "In 5 minutes"),
        ("at_time", "It's time to take"),
        ("after_15", "15 minutes ago"),
        ("after_30", "30 minutes ago"),
        ("overdue", "now overdue"),
    ):
        _subject, body = dispatcher.render_email(_dose_notification(kind), "en")
        assert phrase_en in body, kind


def test_an_email_is_sent_once_even_if_the_tick_repeats(db, smtp):
    update_settings(db, dict(SMTP_SETTINGS))
    today = now_local().date()
    medication_service.create_medication(
        db,
        make_payload(
            start_date=today.isoformat(),
            end_date=today.isoformat(),
            frequency_hours=24,
            first_dose_time=(now_local() - timedelta(minutes=1)).strftime("%H:%M"),
        ),
    )
    db.commit()

    dispatcher.run_tick(db, send_windows=False)
    first_count = len(smtp.sent)
    for _ in range(3):
        dispatcher.run_tick(db, send_windows=False)

    assert len(smtp.sent) == first_count


def test_a_broken_hostname_cannot_take_the_scheduler_down(db, monkeypatch):
    """Regression: getaddrinfo raises UnicodeError (a ValueError) on a malformed
    host. If that escaped, every later tick would abort before committing."""
    update_settings(db, dict(SMTP_SETTINGS))
    # A DNS label longer than 63 characters: stores fine, but makes IDNA
    # encoding inside getaddrinfo raise UnicodeError — a ValueError, not an
    # OSError, which is exactly what used to escape.
    update_settings(db, {"smtp_host": "a" * 64 + ".example.com"})

    sent, error = send_email(config_from_settings(get_settings(db)), "s", "b")
    assert not sent and error

    today = now_local().date()
    medication_service.create_medication(
        db,
        make_payload(
            start_date=today.isoformat(),
            end_date=today.isoformat(),
            first_dose_time=(now_local() - timedelta(minutes=5)).strftime("%H:%M"),
        ),
    )
    db.commit()

    summary = dispatcher.run_tick(db, send_windows=False)   # must not raise
    assert summary["emails_sent"] == 0
    assert summary["dose_notifications"] >= 1               # the tick completed


def test_a_rejected_save_leaves_no_secret_behind(db):
    """Regression: the password used to be written before validation ran."""
    with pytest.raises(ValidationError):
        update_settings(
            db,
            {
                "smtp_host": "smtp.example.com",
                "email_recipient": "not-an-address",
                "smtp_password": "should-never-be-stored",
            },
        )
    assert get_settings(db).smtp_password_protected is None
    # …and the rejected value never reached the secret store either.
    assert secretstore.unprotect("file:smtp_password") != "should-never-be-stored"
