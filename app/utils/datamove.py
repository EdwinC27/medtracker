"""Moving the user's data to where v4 expects to find it.

Two one-time jobs that run on every start and do nothing at all once they have
been done. Neither ever overwrites anything: where the destination already
exists, that is the answer.

1. *Uploads.* Until v4 the medication photographs lived in `app/static/uploads`,
   which is code, not data — it is wiped by a reinstall and, worse, it is served
   without passing the app lock. They are moved into the data folder, and the
   old copy is removed once the new one is in place. Leaving it would leave a
   photograph of somebody's medication reachable at a URL their own browser
   history already holds, with the PIN screen up.

2. *An existing database, for the packaged application.* Running the `.exe` for
   the first time on a machine that has been using the source install must not
   look like a brand new, empty application. If there is no database in the new
   location and there is one in a plausible old one, it is copied across
   (together with the backups and the photographs) and the original is left
   exactly where it is, untouched, as its own fallback. The database is copied
   through SQLite, never as a file — see `_copy_database`.
"""

from __future__ import annotations

import logging
import shutil
import sqlite3
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

# How far up from the executable to look for a source installation's `data`
# folder. Three levels covers `…\ProyectoPersonal\dist\Medication Organizer\`.
LOOKUP_DEPTH = 3


def legacy_upload_dir() -> Path:
    from app.config import BASE_DIR

    return BASE_DIR / "static" / "uploads"


def migrate_legacy_uploads() -> int:
    """Move pre-v4 medication photographs into the data folder. Returns how many.

    Only into this installation's *own* data folder. `MEDTRACKER_DATA_DIR`
    points somewhere else for the test-suite and for anyone running a second
    copy against a scratch database — and this function deletes the source once
    it has copied. Running the tests inside a real installation would otherwise
    move the user's actual photographs into a temporary directory and then
    delete that directory, which is a way to lose data by *checking* the
    software works.
    """
    from app.config import DATA_DIR, UPLOAD_DIR, _default_data_dir

    if DATA_DIR != _default_data_dir():
        logger.debug("Not this installation's data folder; leaving the images alone")
        return 0

    source = legacy_upload_dir()
    if not source.is_dir():
        return 0

    moved = 0
    try:
        UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        for item in source.iterdir():
            if not item.is_file() or item.name.startswith("."):
                continue
            target = UPLOAD_DIR / item.name
            if not target.exists():
                shutil.copy2(item, target)
                moved += 1
            # Removed once it is safely in the new place — and this is not
            # tidiness. `app/static/` is served without passing the app lock;
            # a copy left behind would be a photograph of somebody's medication
            # still reachable at a URL their browser already knows, with the
            # PIN screen up.
            try:
                item.unlink()
            except OSError as exc:
                logger.warning("Could not remove the old copy of %s: %s", item.name, exc)
    except OSError as exc:
        # A photograph is not worth failing a start over: the medication still
        # opens, it just shows no picture until this succeeds on a later run.
        logger.warning("Could not move the uploaded images: %s", exc)

    if moved:
        logger.info("Moved %s uploaded image(s) into the data folder", moved)
    return moved


def _candidate_data_dirs() -> list[Path]:
    """Plausible places a previous installation kept its data folder."""
    if not getattr(sys, "frozen", False):
        return []
    here = Path(sys.executable).resolve().parent
    candidates = []
    for level in range(LOOKUP_DEPTH + 1):
        try:
            folder = here.parents[level - 1] if level else here
        except IndexError:
            break
        candidates.append(folder / "data")
    return candidates


def adopt_existing_database() -> Path | None:
    """Bring a source installation's data across on the packaged app's first run.

    Returns the folder it copied from, or None if there was nothing to do —
    which is the case on every run after the first.
    """
    from app.config import DATA_DIR, DB_PATH

    if DB_PATH.exists():
        return None

    for candidate in _candidate_data_dirs():
        source_db = candidate / "medtracker.db"
        try:
            if not source_db.is_file() or candidate.resolve() == DATA_DIR.resolve():
                continue
        except OSError:
            continue

        try:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            _copy_database(source_db, DB_PATH)
            for folder in ("uploads", "backups"):
                if (candidate / folder).is_dir():
                    shutil.copytree(
                        candidate / folder, DATA_DIR / folder, dirs_exist_ok=True
                    )
        except (OSError, sqlite3.Error) as exc:
            logger.error("Could not bring the existing data across: %s", exc)
            # Leave nothing half-copied behind: a partial database here would be
            # adopted as real on the next start, because the only test is
            # whether the file exists.
            for leftover in (DB_PATH, DB_PATH.with_name(DB_PATH.name + "-wal"),
                             DB_PATH.with_name(DB_PATH.name + "-shm")):
                try:
                    leftover.unlink(missing_ok=True)
                except OSError:  # pragma: no cover
                    pass
            return None

        logger.info("Adopted the existing data from %s", candidate)
        return candidate

    return None


def _copy_database(source: Path, destination: Path) -> None:
    """Copy a SQLite database the only way that is safe while it may be open.

    Not `shutil.copy2`. A database in WAL mode is three files, and copying them
    one after another gives three different instants: if the source checkpoints
    between the copy of the `.db` and the copy of the `-wal`, the result is a
    file that passes `integrity_check` and has lost the user's data. (The same
    reasoning, and the same words, are in `app/services/backup.py`, which is
    where this application already refuses to copy the file.)

    `sqlite3.Connection.backup` takes a consistent snapshot through SQLite
    itself, and the destination it writes is a plain, checkpointed database with
    no side files to carry across.
    """
    reader = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
    try:
        writer = sqlite3.connect(str(destination))
        try:
            with writer:
                reader.backup(writer)
            broken = writer.execute("PRAGMA integrity_check").fetchone()[0]
            tables = {
                row[0]
                for row in writer.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
        finally:
            writer.close()
    finally:
        reader.close()

    if broken != "ok" or "medications" not in tables:
        raise sqlite3.DatabaseError(f"the copied database is not usable ({broken})")


def prepare_data_folder() -> None:
    """Everything above, in the order a start needs it.

    The desktop launcher runs the adoption itself, as its own reported step and
    before the web server exists, because copying a large database and a folder
    of backups can take longer than the launcher is willing to wait for the
    server to answer. By the time this runs inside the lifespan it is therefore
    normally a no-op — but it stays here so that starting the application any
    other way (`python -m app.main`, a script, the tests) is not a way to skip
    it.
    """
    adopt_existing_database()
    migrate_legacy_uploads()
