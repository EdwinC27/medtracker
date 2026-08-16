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
    HOST,
    LOG_FILE,
    PORT,
    SCHEDULER_ENABLED,
    STATIC_DIR,
)
from app.database.db import init_db
from app.notifications import scheduler as background_scheduler
from app.routes.api import router as api_router
from app.routes.pages import router as pages_router
from app.services.errors import AppError

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    handlers=[logging.StreamHandler(), logging.FileHandler(LOG_FILE, encoding="utf-8")],
)
logger = logging.getLogger("medtracker")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    if SCHEDULER_ENABLED:
        background_scheduler.start()
    else:
        logger.info("Background scheduler disabled by MEDTRACKER_DISABLE_SCHEDULER")
    yield
    background_scheduler.shutdown()


app = FastAPI(title=APP_NAME, version=APP_VERSION, lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
app.include_router(api_router)
app.include_router(pages_router)


# --------------------------------------------------------------------------- #
# Error handling: the UI never sees a stack trace, only a translation key.
# --------------------------------------------------------------------------- #
@app.exception_handler(AppError)
async def app_error_handler(_request: Request, exc: AppError):
    return JSONResponse(status_code=exc.status_code, content=exc.to_dict())


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
