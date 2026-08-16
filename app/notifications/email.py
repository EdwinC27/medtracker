"""E-mail channel.

Plain `smtplib` from the standard library — no service, no API key, no extra
dependency. The SMTP settings live in the `settings` row and are edited from
Settings → E-mail; the password itself is never stored in clear text (see
`app/utils/secretstore.py`).

Bodies are built from the same JSON catalogs as the rest of the app, so an
e-mail arrives in whichever language the app is set to.

Threading
---------
Every reminder for one dose belongs to one conversation. That is done with the
real RFC 5322 headers rather than by hoping a mail client groups by subject:
the first message of a dose carries its own `Message-ID`, and each later one
carries `In-Reply-To` (the message immediately before it) and `References`
(the whole chain, oldest first). Subjects change from message to message —
"in 30 minutes", then "time to take it" — and the thread survives it, which is
exactly what those headers are for.

The unit of threading is the *dose*, not the medication: dose #1 of Ryaltris
and dose #2 of Ryaltris are separate conversations.
"""

from __future__ import annotations

import logging
import secrets
import smtplib
import ssl
from dataclasses import dataclass, field
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


@dataclass
class EmailThread:
    """Where one message sits in its conversation.

    `message_id` is this message's own identity; `references` is every earlier
    message of the same dose, oldest first. An empty `references` means this
    message opens the thread.
    """

    message_id: str
    references: list[str] = field(default_factory=list)

    @property
    def in_reply_to(self) -> str | None:
        return self.references[-1] if self.references else None

    @property
    def is_root(self) -> bool:
        return not self.references


def new_message_id(config: EmailConfig, token: str) -> str:
    """A Message-ID for one reminder, unique and traceable back to it.

    Deliberately short. `email.utils.make_msgid` produces something like 70
    characters, which pushes `Message-ID:` and `In-Reply-To:` over the 78-column
    limit and makes them fold onto a continuation line. That is legal, and every
    modern client unfolds it, but a one-line id is what the rest of the world
    sends and it is free to match.

    Uniqueness comes from the token (the dose and the notification row, which
    are unique within this database) plus eight random hex characters, which
    keep two installations — or one reinstalled over the other — from ever
    minting the same id. The domain is the sender's, which is what receiving
    servers expect.
    """
    domain = config.sender.rpartition("@")[2].strip() or "medtracker.local"
    suffix = secrets.token_hex(4)
    return f"<mt.{token}.{suffix}@{domain}>"


def send_email(
    config: EmailConfig,
    subject: str,
    body: str,
    thread: EmailThread | None = None,
) -> tuple[bool, str | None]:
    """Send one plain-text message. Returns `(sent, error)`; never raises.

    The error string is technical (it goes to the log and to the "send test
    e-mail" response); the UI shows a translated message alongside it.
    """
    if not config.is_complete:
        return False, "incomplete configuration"

    try:
        message = EmailMessage()
        # A header cannot contain a line break. The name of a medication can —
        # nothing stops someone pasting one in — and an unheaded ValueError here
        # would abort the whole scheduler tick, not just this message. It is
        # also the classic header-injection vector, so it is collapsed rather
        # than merely caught.
        message["Subject"] = _one_line(subject)
        message["From"] = _one_line(config.sender)
        message["To"] = _one_line(config.recipient)
        if thread is not None:
            message["Message-ID"] = thread.message_id
            if thread.references:
                # In-Reply-To names the parent; References carries the whole
                # chain, oldest first, which is how a client rebuilds the
                # conversation even if one message never arrived.
                message["In-Reply-To"] = thread.in_reply_to
                message["References"] = " ".join(thread.references)
        message.set_content(body)
    except (ValueError, TypeError) as exc:
        return False, f"could not build the message: {exc}"

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


def _one_line(value: str) -> str:
    """Collapse anything that would break a header into single spaces."""
    return " ".join(str(value or "").split())


def _decode(value) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    return str(value)
