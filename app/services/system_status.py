"""System Status: what the application can say about its own health.

Read-only by construction. Nothing in this module sends a notification, sends
an e-mail, writes a backup or restarts anything — opening the page must not
have side effects, which is exactly why the "send a test" buttons live in
Settings and not here.

Every component reports one of four levels, and the page is meant to be
readable at a glance rather than complete:

    ok        working
    warning   working, but something needs attention
    error     not working
    disabled  switched off, or never configured
"""

from __future__ import annotations

import logging
import sqlite3
import sys
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.config import APP_VERSION, DB_PATH, SCHEDULER_INTERVAL_SECONDS
from app.database.migrations import CURRENT_VERSION
from app.utils.timeutil import iso, now_local

logger = logging.getLogger(__name__)

OK = "ok"
WARNING = "warning"
ERROR = "error"
DISABLED = "disabled"

# When the process started, set once at import of the application.
_started_at: datetime | None = None


def mark_started(moment: datetime | None = None) -> None:
    global _started_at
    _started_at = moment or now_local()


def started_at() -> datetime | None:
    return _started_at


def _component(key: str, level: str, detail_key: str | None = None, **facts) -> dict:
    """One row of the page. `detail_key` is a translation key, never a sentence."""
    return {"key": key, "level": level, "detail_key": detail_key, **facts}


# --------------------------------------------------------------------------- #
# The components
# --------------------------------------------------------------------------- #
def _application() -> dict:
    uptime = None
    if _started_at is not None:
        uptime = int((now_local() - _started_at).total_seconds())
    return _component(
        "application",
        OK,
        "status.application_running",
        version=APP_VERSION,
        started_at=iso(_started_at) if _started_at else None,
        uptime_seconds=uptime,
        frozen=bool(getattr(sys, "frozen", False)),
        platform=sys.platform,
    )


def _database(db: Session) -> dict:
    """A real round trip, not an assumption that the session object exists."""
    try:
        db.execute(__import__("sqlalchemy").text("SELECT 1")).scalar_one()
    except Exception as exc:  # noqa: BLE001 - the point is to report, not raise
        logger.warning("System Status: the database did not answer: %s", exc)
        return _component("database", ERROR, "status.database_error", engine="SQLite")

    size = None
    stored_version = None
    try:
        if DB_PATH.exists():
            size = DB_PATH.stat().st_size
            connection = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
            try:
                stored_version = connection.execute("PRAGMA user_version").fetchone()[0]
            finally:
                connection.close()
    except (OSError, sqlite3.Error) as exc:
        logger.warning("System Status: could not read the database file: %s", exc)

    level = OK
    detail = "status.database_connected"
    if stored_version is not None and stored_version != CURRENT_VERSION:
        # Worth flagging: it means a migration did not run, and the application
        # is talking to a schema it was not built for.
        level = WARNING
        detail = "status.database_version_mismatch"

    return _component(
        "database",
        level,
        detail,
        engine="SQLite",
        schema_version=stored_version,
        expected_schema_version=CURRENT_VERSION,
        path=str(DB_PATH),
        size_bytes=size,
    )


def _scheduler(db: Session) -> dict:
    from app.notifications import scheduler as background_scheduler

    info = background_scheduler.status()
    if not info["running"]:
        return _component(
            "scheduler", ERROR, "status.scheduler_stopped",
            interval_seconds=info["interval_seconds"],
            last_run=info["last_run"],
            last_error=info["last_error"],
        )

    level = WARNING if info["last_error"] else OK
    detail = "status.scheduler_last_failed" if info["last_error"] else "status.scheduler_running"

    next_run = None
    if info["last_run"]:
        try:
            next_run = iso(
                datetime.fromisoformat(info["last_run"])
                + timedelta(seconds=info["interval_seconds"])
            )
        except ValueError:  # pragma: no cover - defensive
            next_run = None

    return _component(
        "scheduler",
        level,
        detail,
        interval_seconds=info["interval_seconds"],
        last_run=info["last_run"],
        next_run=next_run,
        last_error=info["last_error"],
        next_dose=_next_dose(db),
    )


def _next_dose(db: Session) -> dict | None:
    """The next thing the scheduler will actually have to do."""
    from sqlalchemy import select

    from app.models.models import DoseStatus, MedicationDose

    dose = (
        db.execute(
            select(MedicationDose)
            .where(
                MedicationDose.status == DoseStatus.SCHEDULED.value,
                MedicationDose.scheduled_at >= now_local(),
            )
            .order_by(MedicationDose.scheduled_at)
            .limit(1)
        )
        .scalars()
        .first()
    )
    if dose is None:
        return None
    return {
        "scheduled_at": iso(dose.scheduled_at),
        "medication": dose.medication.name if dose.medication else None,
    }


def _windows_notifications(settings) -> dict:
    from app.notifications import windows

    if not settings.windows_notifications:
        return _component("windows_notifications", DISABLED, "status.channel_off")
    if not windows.is_available():
        return _component(
            "windows_notifications", WARNING, "status.windows_unavailable",
            reason=windows.unavailable_reason(),
        )
    return _component("windows_notifications", OK, "status.channel_available")


def _browser_notifications(settings) -> dict:
    if not settings.browser_notifications:
        return _component("browser_notifications", DISABLED, "status.channel_off")
    # The queue is served by the app; whether the browser has been granted
    # permission is something only the browser knows, and the page itself says
    # so. From here the honest answer is "available".
    return _component("browser_notifications", OK, "status.channel_available")


def _email_notifications(settings) -> dict:
    """Configuration only. Nothing is sent to find out."""
    from app.notifications.email import config_from_settings

    if not settings.email_notifications:
        return _component("email_notifications", DISABLED, "status.channel_off")

    config = config_from_settings(settings)
    missing = [
        name
        for name, value in (
            ("smtp_host", config.host),
            ("email_recipient", config.recipient),
            ("email_sender", config.sender),
        )
        if not value
    ]
    if missing:
        return _component(
            "email_notifications", ERROR, "status.email_incomplete", missing=missing
        )
    if config.username and not config.password:
        # A user name with no retrievable password: usually a secret store that
        # cannot be read on this machine, which would fail at send time.
        return _component("email_notifications", ERROR, "status.email_no_password")

    return _component(
        "email_notifications", OK, "status.email_configured",
        host=config.host, port=config.port, security=config.security,
        recipient=config.recipient,
    )


def _backup(settings) -> dict:
    from app.services import backup as backup_service

    try:
        info = backup_service.status(settings)
    except Exception as exc:  # noqa: BLE001
        logger.warning("System Status: could not read the backup folder: %s", exc)
        return _component("backup", ERROR, "status.backup_unreadable")

    facts = {
        "enabled": info["enabled"],
        "frequency": info["frequency"],
        "time": info["time"],
        "keep": info["keep"],
        "location": info["location"],
        "writable": info["writable"],
        "count": info["count"],
        "last_backup_at": info["last_backup_at"],
        "last_error": settings.last_backup_error,
        "next_backup_at": _next_backup_at(settings),
    }

    if settings.last_backup_error:
        return _component("backup", ERROR, "status.backup_failed", **facts)
    if not info["enabled"]:
        return _component("backup", DISABLED, "status.backup_off", **facts)
    if not info["writable"]:
        return _component("backup", ERROR, "status.backup_location_unwritable", **facts)
    if not info["last_backup_at"]:
        return _component("backup", WARNING, "status.backup_never", **facts)
    return _component("backup", OK, "status.backup_ok", **facts)


def _next_backup_at(settings) -> str | None:
    """When the next automatic backup is due, by the same rule the tick uses."""
    if not settings.backup_enabled:
        return None
    from datetime import datetime as _dt

    period = timedelta(days=7 if settings.backup_frequency == "weekly" else 1)
    if settings.last_backup_at is None:
        candidate = _dt.combine(now_local().date(), settings.backup_time)
        if candidate < now_local():
            candidate += timedelta(days=1)
        return iso(candidate)
    return iso(settings.last_backup_at + period)


def _desktop(settings) -> dict:
    from app.desktop import startup as desktop_startup

    state = desktop_startup.read_state()
    if not state.supported:
        return _component(
            "startup", DISABLED, "status.startup_unsupported",
            setting=bool(settings.start_with_windows), **state.to_dict()
        )
    if state.error:
        return _component(
            "startup", ERROR, "status.startup_error",
            setting=bool(settings.start_with_windows), **state.to_dict()
        )
    if state.stale:
        # Registered, but pointing at a program that is no longer there.
        # Windows fails at every logon and says nothing about it, so this card
        # is the only place the user could ever find out.
        return _component(
            "startup", WARNING, "status.startup_stale",
            setting=bool(settings.start_with_windows), **state.to_dict()
        )
    if bool(settings.start_with_windows) != state.enabled:
        # The registry and the setting disagree: something outside the app
        # changed one of them, and saying so is more useful than guessing.
        return _component(
            "startup", WARNING, "status.startup_mismatch",
            setting=bool(settings.start_with_windows), **state.to_dict()
        )
    return _component(
        "startup",
        OK if state.enabled else DISABLED,
        "status.startup_on" if state.enabled else "status.startup_off",
        setting=bool(settings.start_with_windows), **state.to_dict()
    )


def _app_lock(settings) -> dict:
    from app.services import applock

    info = applock.state(settings)
    if not info["enabled"]:
        return _component("app_lock", DISABLED, "status.app_lock_off", **info)
    return _component("app_lock", OK, "status.app_lock_on", **info)


def _network(settings) -> dict:
    """Who can reach the application, and at what address.

    The addresses are here because this is where somebody looks when they want
    to open the application on their phone. Reading them off the screen beats
    running `ipconfig` and guessing which of the four answers is the right one.
    """
    from app.config import PORT
    from app.desktop.network import EVERY_INTERFACE, local_addresses

    wanted = bool(settings.network_access)
    listening = _listening_on()
    open_now = listening == EVERY_INTERFACE

    from app.desktop.network import https_enabled

    facts = {
        "setting": wanted,
        "https": https_enabled(),
        "listening_on": listening,
        "port": PORT,
        "local_url": f"http://127.0.0.1:{PORT}",
        "addresses": [
            f"{'https' if https_enabled() else 'http'}://{address}:{PORT}"
            for address in local_addresses()
        ] if open_now else [],
        # A change of this setting only takes effect on the next start: the port
        # is bound before the application is in a position to read it.
        "restart_required": wanted != open_now,
    }

    if wanted != open_now:
        return _component("network", WARNING, "status.network_restart_required", **facts)
    if open_now:
        return _component("network", OK, "status.network_open", **facts)
    return _component("network", DISABLED, "status.network_local_only", **facts)


def _listening_on() -> str:
    """What the running server actually bound to, not what was asked for."""
    from app.config import HOST
    from app.desktop.network import EVERY_INTERFACE, host_to_bind

    global _bound_host
    if _bound_host is not None:
        return _bound_host
    return host_to_bind(HOST if HOST != "127.0.0.1" else None) or EVERY_INTERFACE


_bound_host: str | None = None


def mark_bound(host: str) -> None:
    """Told by the launcher, so the report states fact rather than intention."""
    global _bound_host
    _bound_host = host


# --------------------------------------------------------------------------- #
# The page
# --------------------------------------------------------------------------- #
def collect(db: Session, settings=None) -> dict:
    """Every component, each one isolated: a failure is reported, not raised."""
    from app.services.settings_service import get_settings

    settings = settings or get_settings(db)

    builders = (
        ("application", lambda: _application()),
        ("database", lambda: _database(db)),
        ("scheduler", lambda: _scheduler(db)),
        ("windows_notifications", lambda: _windows_notifications(settings)),
        ("browser_notifications", lambda: _browser_notifications(settings)),
        ("email_notifications", lambda: _email_notifications(settings)),
        ("backup", lambda: _backup(settings)),
        ("network", lambda: _network(settings)),
        ("startup", lambda: _desktop(settings)),
        ("app_lock", lambda: _app_lock(settings)),
    )

    components = []
    for key, build in builders:
        try:
            components.append(build())
        except Exception as exc:  # noqa: BLE001 - one broken probe, one bad row
            logger.exception("System Status: %s could not be read: %s", key, exc)
            components.append(_component(key, ERROR, "status.unavailable"))

    levels = {item["level"] for item in components}
    overall = ERROR if ERROR in levels else WARNING if WARNING in levels else OK

    return {
        "generated_at": iso(now_local()),
        "overall": overall,
        "app_version": APP_VERSION,
        "schema_version": CURRENT_VERSION,
        "scheduler_interval_seconds": SCHEDULER_INTERVAL_SECONDS,
        "components": components,
    }


def health(db: Session) -> dict:
    """The small, cheap version the desktop launcher polls while starting.

    Deliberately not the full page: it must answer before the UI exists, must
    never be blocked by the lock, and must not read the backup folder.
    """
    from app.notifications import scheduler as background_scheduler

    try:
        db.execute(__import__("sqlalchemy").text("SELECT 1")).scalar_one()
        database_ok = True
    except Exception:  # noqa: BLE001
        database_ok = False

    scheduler_info = background_scheduler.status()
    return {
        "ok": database_ok,
        "version": APP_VERSION,
        "database": database_ok,
        "scheduler": scheduler_info["running"],
        "scheduler_last_error": scheduler_info["last_error"],
        "started_at": iso(_started_at) if _started_at else None,
    }
