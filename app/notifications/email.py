"""E-mail channel.

Plain `smtplib` from the standard library — no service, no API key, no extra
dependency. The SMTP settings live in the `settings` row and are edited from
Settings → E-mail; the password itself is never stored in clear text (see
`app/utils/secretstore.py`).

Bodies are built from the same JSON catalogs as the rest of the app, so an
e-mail arrives in whichever language the app is set to.
"""

from __future__ import annotations

import logging
import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage

from app.utils.secretstore import unprotect

logger = logging.getLogger(__name__)

SMTP_TIMEOUT_SECONDS = 20


@dataclass
class EmailConfig:
    host: str
    port: int
    username: str | None
    password: str | None
    sender: str
    recipient: str
    security: str = "starttls"  # "starttls" | "ssl" | "none"

    @property
    def is_complete(self) -> bool:
        return bool(self.host and self.port and self.recipient and self.sender)


def config_from_settings(settings) -> EmailConfig:
    """Build the send configuration, decrypting the password on the way."""
    sender = settings.email_sender or settings.smtp_username or settings.email_recipient
    return EmailConfig(
        host=(settings.smtp_host or "").strip(),
        port=int(settings.smtp_port or 587),
        username=(settings.smtp_username or "").strip() or None,
        password=unprotect(settings.smtp_password_protected),
        sender=(sender or "").strip(),
        recipient=(settings.email_recipient or "").strip(),
        security=(settings.smtp_security or "starttls").strip().lower(),
    )


def send_email(config: EmailConfig, subject: str, body: str) -> tuple[bool, str | None]:
    """Send one plain-text message. Returns `(sent, error)`; never raises.

    The error string is technical (it goes to the log and to the "send test
    e-mail" response); the UI shows a translated message alongside it.
    """
    if not config.is_complete:
        return False, "incomplete configuration"

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = config.sender
    message["To"] = config.recipient
    message.set_content(body)

    try:
        if config.security == "ssl":
            context = ssl.create_default_context()
            with smtplib.SMTP_SSL(
                config.host, config.port, timeout=SMTP_TIMEOUT_SECONDS, context=context
            ) as server:
                _login_and_send(server, config, message)
        else:
            with smtplib.SMTP(
                config.host, config.port, timeout=SMTP_TIMEOUT_SECONDS
            ) as server:
                server.ehlo()
                if config.security == "starttls":
                    server.starttls(context=ssl.create_default_context())
                    server.ehlo()
                _login_and_send(server, config, message)
        return True, None
    except smtplib.SMTPAuthenticationError as exc:
        return False, f"authentication rejected: {exc.smtp_code} {_decode(exc.smtp_error)}"
    except smtplib.SMTPException as exc:
        return False, f"SMTP error: {exc}"
    except (OSError, ssl.SSLError) as exc:
        return False, f"connection failed: {exc}"
    except Exception as exc:  # noqa: BLE001
        # Deliberately broad. A malformed host makes getaddrinfo raise
        # UnicodeError, which is a ValueError - and an exception escaping here
        # would abort the whole scheduler tick, not just this one message.
        logger.warning("Unexpected e-mail failure: %s", exc)
        return False, f"{type(exc).__name__}: {exc}"


def _login_and_send(server, config: EmailConfig, message: EmailMessage) -> None:
    if config.username and config.password:
        server.login(config.username, config.password)
    server.send_message(message)


def _decode(value) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    return str(value)
