"""SQLite engine, session factory and schema bootstrap."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from app.config import DATABASE_URL

# check_same_thread=False: the APScheduler background thread uses its own
# sessions on the same SQLite file.
engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False, "timeout": 15},
    future=True,
)


@event.listens_for(engine, "connect")
def _set_sqlite_pragmas(dbapi_connection, _connection_record):
    """Enable foreign keys (off by default in SQLite) and WAL journaling.

    WAL lets the web request thread read while the scheduler thread writes,
    which is what makes the single-file database comfortable for a local app.
    """
    if isinstance(dbapi_connection, sqlite3.Connection):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.close()


SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


def get_db() -> Iterator[Session]:
    """FastAPI dependency: one session per request."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@contextmanager
def session_scope() -> Iterator[Session]:
    """Standalone session for background jobs and scripts."""
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def init_db() -> None:
    """Migrate, create any missing tables, and seed settings.

    Order matters: migrations run against the raw file first, so an existing v1
    database is brought up to date before SQLAlchemy looks at it.
    """
    from app.database.migrations import run_migrations
    from app.models import models  # noqa: F401  (registers the mappers)
    from app.models.models import Base

    report = run_migrations()
    if report["applied"]:
        import logging

        logging.getLogger(__name__).info(
            "Database migrated %s -> %s (backup: %s)",
            report["from"],
            report["to"],
            report["backup"] or "not needed",
        )

    Base.metadata.create_all(bind=engine)
    _stamp_schema_version()

    from app.services.settings_service import ensure_settings

    with session_scope() as db:
        ensure_settings(db)


def _stamp_schema_version() -> None:
    """Record the schema version on a database SQLAlchemy has just created.

    `create_all` writes the current tables but leaves `PRAGMA user_version` at
    0, which used to make a brand new file indistinguishable from an old one on
    the next start. Stamping it here means a fresh database is never a
    migration candidate, and the version detection has one less thing to guess.
    """
    import sqlite3

    from app.config import DB_PATH
    from app.database.migrations import CURRENT_VERSION

    if not DB_PATH.exists():
        return
    connection = sqlite3.connect(str(DB_PATH))
    try:
        if connection.execute("PRAGMA user_version").fetchone()[0] == 0:
            connection.execute(f"PRAGMA user_version = {CURRENT_VERSION}")
            connection.commit()
    finally:
        connection.close()
