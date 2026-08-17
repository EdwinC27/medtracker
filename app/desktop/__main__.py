"""`Medication Organizer.exe` — the desktop entry point.

    python -m app.desktop              start and open the interface
    python -m app.desktop --background start hidden, for the Windows logon
    python -m app.desktop --no-tray    no tray icon (useful when developing)

What happens, in order:

    paths → web server → database → scheduler → interface → tray

If a required step fails the user gets a message box naming the step, not a
blank window. The tray icon then keeps the process — and therefore the
reminders — alive until Exit is chosen.
"""

from __future__ import annotations

import argparse
import logging
import signal
import sys
import threading

from app.config import APP_NAME, APP_VERSION, HOST, PORT
from app.desktop import launcher

logger = logging.getLogger("medtracker.desktop")

_stopping = threading.Event()


def _labels() -> dict[str, str]:
    """Tray labels in the user's language, read from the same catalogs as the UI."""
    from app.config import DEFAULT_LANGUAGE
    from app.i18n import t

    language = DEFAULT_LANGUAGE
    try:
        from app.database.db import session_scope
        from app.services.settings_service import get_settings

        with session_scope() as db:
            language = get_settings(db).language or DEFAULT_LANGUAGE
    except Exception as exc:  # noqa: BLE001 - a label is never worth failing over
        logger.debug("Falling back to the default language for the tray: %s", exc)

    return {
        "title": t("app.name", language),
        "open": t("tray.open", language),
        "status": t("tray.status", language),
        "lock": t("tray.lock", language),
        "exit": t("tray.exit", language),
    }


def _lock_is_enabled() -> bool:
    try:
        from app.database.db import session_scope
        from app.services.settings_service import get_settings

        with session_scope() as db:
            settings = get_settings(db)
            return bool(settings.app_lock_enabled and settings.pin_hash)
    except Exception:  # noqa: BLE001
        return False


def _lock_now() -> None:
    from app.routes import lock_cache
    from app.services import applock

    applock.lock()
    lock_cache.invalidate()
    logger.info("Locked from the tray")


def shutdown(handle: launcher.ServerHandle | None) -> None:
    """Stop everything, in the order that leaves nothing behind.

    The server is asked to exit first; that runs FastAPI's lifespan shutdown,
    which stops the scheduler. Only then is the database connection pool
    disposed. Everything lives in this one process, so when it returns there is
    nothing left running.
    """
    if _stopping.is_set():
        return
    _stopping.set()
    logger.info("Shutting down")

    try:
        if handle is not None:
            handle.stop()
    except Exception as exc:  # noqa: BLE001
        logger.warning("The web server did not stop cleanly: %s", exc)

    try:
        from app.notifications import scheduler as background_scheduler

        background_scheduler.shutdown()
    except Exception as exc:  # noqa: BLE001
        logger.warning("The scheduler did not stop cleanly: %s", exc)

    try:
        from app.database.db import engine

        engine.dispose()
    except Exception as exc:  # noqa: BLE001
        logger.warning("The database did not close cleanly: %s", exc)

    logger.info("Stopped")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog=APP_NAME, description=f"{APP_NAME} {APP_VERSION}")
    parser.add_argument("--background", action="store_true",
                        help="start without opening the interface (used at Windows logon)")
    parser.add_argument("--no-tray", action="store_true", help="do not show a tray icon")
    parser.add_argument("--port", type=int, default=PORT)
    # No default: "not given" has to be distinguishable from "given, and it
    # happens to be the same address", because only then can the stored
    # Settings switch decide. An explicit --host still wins over the setting.
    parser.add_argument("--host", default=None)
    args = parser.parse_args(argv)

    from app.desktop.network import host_to_bind, scheme

    requested = args.host
    if requested is None and HOST != "127.0.0.1":
        # MEDTRACKER_HOST was set in the environment; that is explicit too.
        requested = HOST
    host = host_to_bind(requested)

    # Already running? Hand over to that instance instead of starting a second
    # scheduler that would double every reminder.
    existing = launcher.already_running(args.port)
    if existing is not None:
        logger.info("Already running (v%s); opening the existing instance",
                    existing.get("version"))
        if not args.background:
            launcher.open_browser(f"{scheme()}://127.0.0.1:{args.port}")
        return 0

    report, handle = launcher.start_application(
        host, args.port, open_ui=not args.background
    )

    if not report.ok:
        from app.desktop.messages import startup_failure_text

        launcher.show_error_dialog(APP_NAME, startup_failure_text(report))
        shutdown(handle)
        return 1

    logger.info("%s %s is running at %s", APP_NAME, APP_VERSION, report.url)

    # Reconcile the Windows startup entry with the stored setting on every
    # launch, so the switch is not just a remembered preference.
    try:
        from app.database.db import session_scope
        from app.desktop import startup as desktop_startup
        from app.services.settings_service import get_settings

        with session_scope() as db:
            desktop_startup.reconcile(bool(get_settings(db).start_with_windows))
    except Exception as exc:  # noqa: BLE001 - never fatal
        logger.warning("Could not reconcile the Windows startup entry: %s", exc)

    # Ctrl+C and a Windows logoff both mean the same thing here.
    def _signal(_sig, _frame):
        shutdown(handle)
        sys.exit(0)

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, _signal)
        except (ValueError, OSError):  # pragma: no cover - not on every platform
            pass

    if args.no_tray or not _tray_wanted():
        # No tray: block until interrupted, so the scheduler keeps running.
        try:
            while not _stopping.is_set():
                _stopping.wait(1.0)
        except KeyboardInterrupt:
            pass
        shutdown(handle)
        return 0

    from app.desktop.tray import Tray

    tray = Tray(
        on_open=lambda: launcher.open_browser(report.url or f"{scheme()}://127.0.0.1:{args.port}"),
        on_status=lambda: launcher.open_browser(
            f"{report.url or f'{scheme()}://127.0.0.1:{args.port}'}/settings?tab=status"
        ),
        on_lock=_lock_now,
        on_exit=lambda: shutdown(handle),
        labels=_labels(),
        lock_enabled=_lock_is_enabled,
    )
    if not tray.start(blocking=True):
        # The tray refused to appear; fall back to staying alive quietly rather
        # than exiting and silently taking the reminders with us.
        logger.warning("Running without a tray icon")
        try:
            while not _stopping.is_set():
                _stopping.wait(1.0)
        except KeyboardInterrupt:
            pass

    shutdown(handle)
    return 0


def _tray_wanted() -> bool:
    from app.desktop.tray import is_available

    return is_available()


if __name__ == "__main__":
    raise SystemExit(main())
