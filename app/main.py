"""Application entry point.

Run it with:

    python -m app.main            (or scripts\\start.bat on Windows)

Starting this single process gives you the web UI *and* the background
notification worker.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.exc import SQLAlchemyError

from app.config import (
    APP_NAME,
    APP_VERSION,
    FROZEN,
    HOST,
    LOG_FILE,
    PORT,
    SCHEDULER_ENABLED,
    STATIC_DIR,
)
from app.database.db import init_db
from app.notifications import scheduler as background_scheduler
from app.routes import lock_cache
from app.routes.api import router as api_router
from app.routes.lock import lock_middleware
from app.routes.origin import origin_middleware
from app.routes.pages import router as pages_router
from app.services.errors import AppError


def _log_handlers() -> list[logging.Handler]:
    """The console if there is one, and a file if one can be opened.

    Neither is guaranteed. Installing into a folder the user cannot write to
    used to raise here, during an `import`, before any code existed to turn it
    into a message — the application would rather run without a log file than
    not run. And the windowed Windows build has no console at all: `sys.stderr`
    is None there, and a StreamHandler wrapped around that silently discards
    everything it is handed.
    """
    import sys

    handlers: list[logging.Handler] = []
    if getattr(sys, "stderr", None) is not None:
        handlers.append(logging.StreamHandler())
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(LOG_FILE, encoding="utf-8"))
    except OSError as exc:  # pragma: no cover - depends on the filesystem
        if handlers:
            handlers[0].handle(
                logging.LogRecord(
                    "medtracker", logging.WARNING, __file__, 0,
                    "No log file (%s): %s", (LOG_FILE, exc), None,
                )
            )
    # `basicConfig` with an empty list installs nothing and then lets the root
    # logger fall back to a "last resort" handler on the very stderr that does
    # not exist. A handler that goes nowhere is the honest answer.
    return handlers or [logging.NullHandler()]


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    handlers=_log_handlers(),
)
logger = logging.getLogger("medtracker")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    from app.services import applock, system_status

    system_status.mark_started()
    # Every start begins locked; if the lock is off this is a no-op, and if it
    # is on the PIN is asked for before anything medical is served.
    applock.lock()
    lock_cache.invalidate()
    init_db()
    if SCHEDULER_ENABLED:
        background_scheduler.start()
    else:
        logger.info("Background scheduler disabled by MEDTRACKER_DISABLE_SCHEDULER")
    yield
    background_scheduler.shutdown()


# The interactive API browser is a development convenience. In the packaged
# application it is one more way to reach every write endpoint, so it is not
# shipped.
_DOCS = {} if not FROZEN else {"docs_url": None, "redoc_url": None, "openapi_url": None}

app = FastAPI(title=APP_NAME, version=APP_VERSION, lifespan=lifespan, **_DOCS)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# Middleware runs in reverse registration order, so the lock is registered
# first and therefore runs *inside* the origin check: a cross-site request is
# refused before the application even asks whether it is locked.
#
# The optional app lock, enforced in one place rather than remembered by every
# route. It does nothing at all unless the user has switched it on.
app.middleware("http")(lock_middleware)
# And the guard that keeps another website from driving this one.
app.middleware("http")(origin_middleware)

app.include_router(api_router)
app.include_router(pages_router)


# --------------------------------------------------------------------------- #
# Error handling: the UI never sees a stack trace, only a translation key.
# --------------------------------------------------------------------------- #
@app.exception_handler(AppError)
async def app_error_handler(_request: Request, exc: AppError):
    return JSONResponse(status_code=exc.status_code, content=exc.to_dict())


# Note on rollbacks: the request's own session is rolled back and closed by the
# `get_db` dependency, so by the time a handler below runs, nothing partial is
# pending. These handlers only decide what the user is told.
@app.exception_handler(SQLAlchemyError)
async def database_error_handler(_request: Request, exc: SQLAlchemyError):
    logger.exception("Database error: %s", exc)
    return JSONResponse(
        status_code=500,
        content={"error": "error.database", "params": {}, "fields": {}},
    )


@app.exception_handler(Exception)
async def unhandled_error_handler(_request: Request, exc: Exception):
    logger.exception("Unhandled error: %s", exc)
    return JSONResponse(
        status_code=500,
        content={"error": "error.generic", "params": {}, "fields": {}},
    )


def run() -> None:
    import uvicorn

    uvicorn.run(app, host=HOST, port=PORT, log_level="info")


if __name__ == "__main__":
    run()
