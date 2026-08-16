"""Backups of the SQLite database.

Safety
------
Backups are taken with **SQLite's own online backup API** (`Connection.backup`),
never by copying the file. A plain file copy of a live database can capture a
half-written page or miss the contents of the WAL and produce a backup that
looks fine and restores corrupt. The backup API takes a consistent snapshot
while the application keeps using the database.

Restoring works the same way in reverse, and always writes a safety copy of the
current database first — so "restore" is never a one-way door.
"""

from __future__ import annotations

import logging
import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy.orm import Session

from app.config import BACKUP_DIR, BACKUP_PREFIX, DB_PATH
from app.services.errors import AppError, ValidationError
from app.utils.timeutil import now_local

logger = logging.getLogger(__name__)

AUTOMATIC = "auto"
MANUAL = "manual"
SAFETY = "safety"
PRE_IMPORT = "preimport"
# Written by the migration itself, before the schema is touched. It is never
# created through this module, but it lives in the same folder and the user must
# be able to restore it, so it is a kind the rest of the code understands.
PRE_MIGRATION = "premigration"
KINDS = (AUTOMATIC, MANUAL, SAFETY, PRE_IMPORT)
ALL_KINDS = KINDS + (PRE_MIGRATION,)


@dataclass
class BackupFile:
    path: Path
    kind: str
    created_at: datetime
    size: int

    def to_dict(self) -> dict:
        return {
            "name": self.path.name,
            "path": str(self.path),
            "kind": self.kind,
            "created_at": self.created_at.isoformat(),
            "size": self.size,
        }


# --------------------------------------------------------------------------- #
# Location
# --------------------------------------------------------------------------- #
def backup_directory(settings) -> Path:
    """Where backups go: the configured folder, or data/backups by default."""
    configured = (settings.backup_location or "").strip()
    return Path(configured) if configured else BACKUP_DIR


def validate_location(raw: str | None) -> str | None:
    """Check a user-supplied folder is usable before it is saved."""
    value = (raw or "").strip()
    if not value:
        return None
    path = Path(value)
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".medtracker-write-test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError:
        raise ValidationError({"backup_location": "validation.backup_location_unusable"}) from None
    return str(path)


# --------------------------------------------------------------------------- #
# Creating
# --------------------------------------------------------------------------- #
def create_backup(settings, kind: str = MANUAL, source: Path | None = None) -> BackupFile:
    """Take a consistent snapshot of the database. Returns the file written."""
    if kind not in KINDS:
        kind = MANUAL
    source_path = Path(source) if source else DB_PATH
    if not source_path.exists():
        raise AppError("error.backup_no_database")

    directory = backup_directory(settings)
    try:
        directory.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        logger.error("Backup folder unusable: %s", exc)
        raise ValidationError({"backup_location": "validation.backup_location_unusable"}) from None

    stamp = now_local().strftime("%Y%m%d-%H%M%S")
    target = directory / f"{BACKUP_PREFIX}-{kind}-{stamp}.db"

    connection = sqlite3.connect(str(source_path))
    try:
        destination = sqlite3.connect(str(target))
        try:
            with destination:
                connection.backup(destination)
        finally:
            destination.close()
    except sqlite3.Error as exc:
        logger.error("Backup failed: %s", exc)
        target.unlink(missing_ok=True)
        raise AppError("error.backup_failed") from None
    finally:
        connection.close()

    logger.info("Backup written: %s", target)
    return BackupFile(target, kind, now_local(), target.stat().st_size)


# --------------------------------------------------------------------------- #
# Listing and retention
# --------------------------------------------------------------------------- #
def _parse_name(path: Path) -> tuple[str, datetime] | None:
    stem = path.stem
    if not stem.startswith(f"{BACKUP_PREFIX}-"):
        return None
    parts = stem.split("-")
    if len(parts) < 4:
        return None
    # The migration names its copy "medtracker-pre-v3-<date>-<time>", so the
    # kind is everything between the prefix and the timestamp.
    kind = "-".join(parts[1:-2])
    if kind.startswith("pre-v"):
        kind = PRE_MIGRATION
    if kind not in ALL_KINDS:
        return None
    try:
        created = datetime.strptime(f"{parts[-2]}-{parts[-1]}", "%Y%m%d-%H%M%S")
    except ValueError:
        return None
    return kind, created


def list_backups(settings) -> list[BackupFile]:
    directory = backup_directory(settings)
    if not directory.exists():
        return []
    found: list[BackupFile] = []
    for path in directory.glob(f"{BACKUP_PREFIX}-*.db"):
        parsed = _parse_name(path)
        if parsed is None:
            continue
        kind, created = parsed
        try:
            size = path.stat().st_size
        except OSError:
            continue
        found.append(BackupFile(path, kind, created, size))
    return sorted(found, key=lambda item: item.created_at, reverse=True)


def prune_backups(settings) -> list[str]:
    """Delete the oldest automatic backups beyond the retention setting.

    Only automatic ones are pruned: a manual backup, a safety copy taken before
    a restore, and the pre-import copy are things the user asked for, so they
    are never deleted behind their back. The live database is never touched.
    """
    keep = max(int(settings.backup_keep or 7), 1)
    automatic = [item for item in list_backups(settings) if item.kind == AUTOMATIC]
    removed: list[str] = []
    for item in automatic[keep:]:
        if item.path.resolve() == DB_PATH.resolve():
            continue  # paranoia: never the active database
        try:
            item.path.unlink()
            removed.append(item.path.name)
        except OSError as exc:  # pragma: no cover
            logger.warning("Could not remove old backup %s: %s", item.path, exc)
    if removed:
        logger.info("Pruned %s old backup(s)", len(removed))
    return removed


def find_backup(settings, name: str) -> BackupFile:
    """Resolve a backup by file name, refusing anything outside the folder."""
    directory = backup_directory(settings).resolve()
    candidate = (directory / Path(name).name).resolve()
    if candidate.parent != directory or not candidate.is_file():
        raise ValidationError({"backup": "validation.backup_not_found"})
    parsed = _parse_name(candidate)
    kind = parsed[0] if parsed else MANUAL
    created = parsed[1] if parsed else now_local()
    return BackupFile(candidate, kind, created, candidate.stat().st_size)


# --------------------------------------------------------------------------- #
# Scheduling
# --------------------------------------------------------------------------- #
def is_backup_due(settings, reference: datetime | None = None) -> bool:
    """True when the configured moment has passed and none was taken since.

    Deliberately forgiving: if the machine was off at 01:00 the backup is taken
    at the next tick after it comes back, rather than being skipped for the day.
    """
    if not settings.backup_enabled:
        return False
    now = reference or now_local()
    period = timedelta(days=7 if settings.backup_frequency == "weekly" else 1)

    last = settings.last_backup_at
    if last is None:
        # Only start once today's configured time has actually passed.
        return now.time() >= settings.backup_time

    due_at = last + period
    return now >= due_at and now.time() >= settings.backup_time


def run_scheduled_backup(db: Session, reference: datetime | None = None) -> dict | None:
    """Called from the scheduler tick. Returns the backup taken, or None."""
    from app.services.settings_service import get_settings

    settings = get_settings(db)
    if not is_backup_due(settings, reference):
        return None
    try:
        backup = create_backup(settings, AUTOMATIC)
    except AppError as exc:
        logger.warning("Scheduled backup failed: %s", exc.message_key)
        return None
    settings.last_backup_at = reference or now_local()
    removed = prune_backups(settings)
    db.flush()
    return {"backup": backup.to_dict(), "pruned": removed}


# --------------------------------------------------------------------------- #
# Restoring
# --------------------------------------------------------------------------- #
def restore_backup(db: Session, name: str) -> dict:
    """Replace the live database with a backup, after copying the current one.

    The safety copy is taken first and its name is returned, so the user can
    always get back to the state they were in a moment ago.
    """
    from app.services.settings_service import get_settings

    settings = get_settings(db)
    chosen = find_backup(settings, name)

    # Refuse a file that is not a readable SQLite database rather than
    # overwriting good data with rubbish.
    _assert_valid_database(chosen.path)

    safety = create_backup(settings, SAFETY)

    # Detach the ORM before the file underneath it changes.
    db.commit()
    from app.database.db import engine

    engine.dispose()

    source = sqlite3.connect(str(chosen.path))
    try:
        destination = sqlite3.connect(str(DB_PATH))
        try:
            with destination:
                source.backup(destination)
        finally:
            destination.close()
    except sqlite3.Error as exc:
        logger.error("Restore failed: %s", exc)
        raise AppError("error.restore_failed") from None
    finally:
        source.close()

    # A backup can be older than the running version - the copy the migration
    # itself takes before upgrading is the obvious case. Bringing the restored
    # file up to the current schema here is what stops "restore" from leaving
    # the application talking to a database it no longer understands.
    from app.database.migrations import run_migrations

    migrated = run_migrations(DB_PATH)

    logger.info("Restored %s (safety copy: %s)", chosen.path.name, safety.path.name)
    return {
        "restored": chosen.to_dict(),
        "safety_backup": safety.to_dict(),
        "migrated": migrated["applied"],
    }


def _assert_valid_database(path: Path) -> None:
    from app.database.migrations import CURRENT_VERSION

    try:
        connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            result = connection.execute("PRAGMA integrity_check").fetchone()[0]
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            tables = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
        finally:
            connection.close()
    except sqlite3.Error:
        raise ValidationError({"backup": "validation.backup_invalid"}) from None

    if result != "ok" or "medications" not in tables:
        raise ValidationError({"backup": "validation.backup_invalid"})
    # An older file is fine - the migrations bring it forward after the restore.
    # A newer one is not: this version has no way to read a schema from the
    # future, and pretending otherwise would break the app on the next request.
    if version > CURRENT_VERSION:
        raise ValidationError({"backup": "validation.backup_newer_schema"})


def status(settings) -> dict:
    backups = list_backups(settings)
    directory = backup_directory(settings)
    return {
        "enabled": bool(settings.backup_enabled),
        "frequency": settings.backup_frequency,
        "time": settings.backup_time.strftime("%H:%M"),
        "keep": settings.backup_keep,
        "location": str(directory),
        "is_default_location": not (settings.backup_location or "").strip(),
        "writable": os.access(directory, os.W_OK) if directory.exists() else False,
        "last_backup_at": settings.last_backup_at.isoformat() if settings.last_backup_at else None,
        "backups": [item.to_dict() for item in backups],
        "count": len(backups),
    }
