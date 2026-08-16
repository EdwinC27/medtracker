"""Background scheduler.

A single `BackgroundScheduler` (APScheduler) thread lives inside the FastAPI
process. Every `SCHEDULER_INTERVAL_SECONDS` it runs one `run_tick()`:

* completes treatments whose end date has passed,
* turns overdue unmarked doses into "missed",
* queues notifications for doses and appointment reminders that are now due,
* fires the Windows toasts.

Why in-process instead of Windows Task Scheduler
------------------------------------------------
The web app has to be running for the UI to work anyway, so keeping the worker
in the same process means one thing to start, one thing to stop, one database
connection pool and no risk of two schedulers double-notifying. Windows Task
Scheduler is still used, but only to *start this app at logon* — see
`scripts/install_autostart.ps1`. That combination is what makes reminders work
with the browser closed.

The schedule is never held in memory: everything is recomputed from SQLite on
each tick, so restarting the app (or the machine) loses nothing.
"""

from __future__ import annotations

import logging
from datetime import datetime

from apscheduler.schedulers.background import BackgroundScheduler

from app.config import SCHEDULER_INTERVAL_SECONDS
from app.database.db import session_scope
from app.utils.timeutil import now_local

logger = logging.getLogger(__name__)

_scheduler: BackgroundScheduler | None = None
_last_run: datetime | None = None
_last_error: str | None = None


def _job() -> None:
    global _last_run, _last_error
    try:
        from app.notifications.dispatcher import run_tick

        with session_scope() as db:
            summary = run_tick(db)
        _last_run = now_local()
        _last_error = None
        if any(value for key, value in summary.items()):
            logger.info("scheduler tick: %s", summary)
    except Exception as exc:  # never let the thread die
        _last_error = str(exc)
        logger.exception("scheduler tick failed: %s", exc)


def start() -> None:
    global _scheduler
    if _scheduler is not None:
        return
    _scheduler = BackgroundScheduler(daemon=True)
    _scheduler.add_job(
        _job,
        trigger="interval",
        seconds=SCHEDULER_INTERVAL_SECONDS,
        id="medtracker_tick",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=300,
    )
    _scheduler.start()
    logger.info("Background scheduler started (every %ss)", SCHEDULER_INTERVAL_SECONDS)
    _job()  # catch up immediately on startup


def shutdown() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
        logger.info("Background scheduler stopped")


def status() -> dict:
    return {
        "running": _scheduler is not None and _scheduler.running,
        "interval_seconds": SCHEDULER_INTERVAL_SECONDS,
        "last_run": _last_run.isoformat() if _last_run else None,
        "last_error": _last_error,
    }
