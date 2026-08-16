"""Schema migrations.

The database is versioned with SQLite's own `PRAGMA user_version`, so no extra
bookkeeping table is needed. `run_migrations()` is called at startup, before
the ORM touches anything, and is idempotent: running it twice does nothing the
second time.

Rules this module follows
-------------------------
* A copy of the database file is taken before the first schema change, into
  `data/backups/`. Nothing is destructive until that copy exists.
* Existing rows are migrated, never dropped. `appointments.doctor_name` becomes
  a real `doctors` row and the appointment keeps pointing at it.
* Tables that need a column to become nullable (or need a new foreign key) are
  rebuilt with the standard SQLite create-copy-drop-rename dance inside one
  transaction, with foreign keys off for the duration, as the SQLite manual
  prescribes.

Versions
--------
0 -> 1  the v1 schema (an empty database is created directly at the current
        version by SQLAlchemy, so this only ever runs on a real v1 file)
1 -> 2  doctors, appointment.doctor_id + follow_up_of_id, optional medication
        fields, dose.status_changed_at, notification dedupe/kind/email columns,
        settings for e-mail and the six dose reminders
"""

from __future__ import annotations

import logging
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path

from app.config import DATA_DIR, DB_PATH

logger = logging.getLogger(__name__)

CURRENT_VERSION = 2
BACKUP_DIR = DATA_DIR / "backups"


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _table_exists(con: sqlite3.Connection, name: str) -> bool:
    row = con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    return row is not None


def _columns(con: sqlite3.Connection, table: str) -> list[str]:
    return [row[1] for row in con.execute(f"PRAGMA table_info('{table}')")]


def _add_column(con: sqlite3.Connection, table: str, column: str, ddl: str) -> None:
    if column not in _columns(con, table):
        con.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
        logger.info("migration: added %s.%s", table, column)


def backup_database(tag: str) -> Path | None:
    """Copy the database file next to itself before it is modified."""
    if not DB_PATH.exists():
        return None
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    target = BACKUP_DIR / f"medtracker-{tag}-{stamp}.db"
    # Use SQLite's own backup API so a live WAL is included correctly.
    source = sqlite3.connect(str(DB_PATH))
    try:
        destination = sqlite3.connect(str(target))
        with destination:
            source.backup(destination)
        destination.close()
    finally:
        source.close()
    logger.info("migration: backup written to %s", target)
    return target


def detect_version(con: sqlite3.Connection) -> int:
    """Figure out where this file stands.

    A fresh file created by SQLAlchemy already has the current schema but a
    user_version of 0, so the presence of the v2 tables is what distinguishes
    "new" from "old".
    """
    version = con.execute("PRAGMA user_version").fetchone()[0]
    if version:
        return int(version)
    if not _table_exists(con, "medications"):
        return CURRENT_VERSION  # brand new database
    if _table_exists(con, "doctors") and "doctor_id" in _columns(con, "appointments"):
        return CURRENT_VERSION  # already v2, just never stamped
    return 1


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #
def run_migrations(db_path: Path | None = None) -> dict:
    """Bring the database up to CURRENT_VERSION. Returns a small report."""
    path = Path(db_path) if db_path else DB_PATH
    report = {"from": None, "to": CURRENT_VERSION, "applied": [], "backup": None}

    if not path.exists():
        report["from"] = CURRENT_VERSION
        return report  # nothing to migrate; SQLAlchemy will create it

    con = sqlite3.connect(str(path))
    con.row_factory = sqlite3.Row
    try:
        version = detect_version(con)
        report["from"] = version
        if version >= CURRENT_VERSION:
            con.execute(f"PRAGMA user_version = {CURRENT_VERSION}")
            con.commit()
            return report

        if db_path is None:
            report["backup"] = str(backup_database(f"pre-v{CURRENT_VERSION}") or "")

        if version < 2:
            _migrate_1_to_2(con)
            report["applied"].append("1->2")

        con.execute(f"PRAGMA user_version = {CURRENT_VERSION}")
        con.commit()
        logger.info("migration: database is now at version %s", CURRENT_VERSION)
    finally:
        con.close()
    return report


# --------------------------------------------------------------------------- #
# 1 -> 2
# --------------------------------------------------------------------------- #
def _migrate_1_to_2(con: sqlite3.Connection) -> None:
    con.execute("PRAGMA foreign_keys = OFF")
    con.execute("BEGIN")

    # ---- doctors -----------------------------------------------------------
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS doctors (
            id INTEGER NOT NULL PRIMARY KEY,
            name VARCHAR(160) NOT NULL,
            occupation VARCHAR(160),
            phone VARCHAR(60),
            notes TEXT,
            created_at DATETIME NOT NULL,
            updated_at DATETIME NOT NULL
        )
        """
    )

    # ---- appointments: doctor_name -> doctors.id, plus follow_up_of_id -----
    appointment_columns = _columns(con, "appointments")
    if "doctor_id" not in appointment_columns:
        now = datetime.now().replace(microsecond=0).isoformat(sep=" ")

        # One doctor row per distinct name already in the appointments table.
        names = [
            row[0]
            for row in con.execute(
                "SELECT DISTINCT doctor_name FROM appointments "
                "WHERE doctor_name IS NOT NULL AND TRIM(doctor_name) <> ''"
            )
        ]
        for name in names:
            existing = con.execute(
                "SELECT id FROM doctors WHERE name = ?", (name.strip(),)
            ).fetchone()
            if existing is None:
                con.execute(
                    "INSERT INTO doctors (name, created_at, updated_at) VALUES (?,?,?)",
                    (name.strip(), now, now),
                )
        if names:
            logger.info("migration: created %s doctor(s) from appointments", len(names))

        # Any appointment with a blank doctor name gets one placeholder record,
        # so the new NOT NULL foreign key can be satisfied without inventing
        # data per row.
        blank = con.execute(
            "SELECT COUNT(*) FROM appointments "
            "WHERE doctor_name IS NULL OR TRIM(doctor_name) = ''"
        ).fetchone()[0]
        if blank:
            con.execute(
                "INSERT INTO doctors (name, created_at, updated_at) VALUES (?,?,?)",
                ("—", now, now),
            )

        con.execute(
            """
            CREATE TABLE appointments_v2 (
                id INTEGER NOT NULL PRIMARY KEY,
                doctor_id INTEGER NOT NULL,
                scheduled_at DATETIME NOT NULL,
                location VARCHAR(200),
                treatment VARCHAR(300),
                notes TEXT,
                next_appointment_at DATETIME,
                follow_up_of_id INTEGER,
                reminder_days_3 BOOLEAN NOT NULL,
                reminder_day_1 BOOLEAN NOT NULL,
                reminder_hours_3 BOOLEAN NOT NULL,
                created_at DATETIME NOT NULL,
                updated_at DATETIME NOT NULL,
                FOREIGN KEY(doctor_id) REFERENCES doctors (id) ON DELETE RESTRICT,
                FOREIGN KEY(follow_up_of_id) REFERENCES appointments (id) ON DELETE SET NULL
            )
            """
        )
        con.execute(
            """
            INSERT INTO appointments_v2 (
                id, doctor_id, scheduled_at, location, treatment, notes,
                next_appointment_at, follow_up_of_id,
                reminder_days_3, reminder_day_1, reminder_hours_3,
                created_at, updated_at
            )
            SELECT
                a.id,
                COALESCE(
                    (SELECT d.id FROM doctors d WHERE d.name = TRIM(a.doctor_name)),
                    (SELECT d.id FROM doctors d WHERE d.name = '—')
                ),
                a.scheduled_at, a.location, a.treatment, a.notes,
                a.next_appointment_at, NULL,
                a.reminder_days_3, a.reminder_day_1, a.reminder_hours_3,
                a.created_at, a.updated_at
            FROM appointments a
            """
        )
        moved = con.execute("SELECT COUNT(*) FROM appointments_v2").fetchone()[0]
        kept = con.execute("SELECT COUNT(*) FROM appointments").fetchone()[0]
        if moved != kept:
            raise RuntimeError(
                f"appointment migration would lose rows ({kept} -> {moved}), aborting"
            )
        con.execute("DROP TABLE appointments")
        con.execute("ALTER TABLE appointments_v2 RENAME TO appointments")
        con.execute(
            "CREATE INDEX IF NOT EXISTS ix_appointments_scheduled_at "
            "ON appointments (scheduled_at)"
        )
        con.execute(
            "CREATE INDEX IF NOT EXISTS ix_appointments_doctor_id "
            "ON appointments (doctor_id)"
        )
        con.execute(
            "CREATE INDEX IF NOT EXISTS ix_appointments_follow_up_of_id "
            "ON appointments (follow_up_of_id)"
        )
        logger.info("migration: appointments now reference doctors (%s rows)", moved)

    # ---- medications: dose fields and end_date become optional -------------
    medication_ddl = con.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='medications'"
    ).fetchone()[0]
    if "end_date DATE NOT NULL" in medication_ddl.replace("\n", " ").replace("\t", " "):
        con.execute(
            """
            CREATE TABLE medications_v2 (
                id INTEGER NOT NULL PRIMARY KEY,
                name VARCHAR(160) NOT NULL,
                image_path VARCHAR(300),
                dose_amount VARCHAR(40),
                dose_unit VARCHAR(20),
                quantity FLOAT,
                form VARCHAR(30),
                comments TEXT,
                start_date DATE NOT NULL,
                end_date DATE,
                frequency_hours INTEGER NOT NULL,
                first_dose_time TIME NOT NULL,
                status VARCHAR(20) NOT NULL,
                suspended_at DATETIME,
                completed_at DATETIME,
                created_at DATETIME NOT NULL,
                updated_at DATETIME NOT NULL
            )
            """
        )
        con.execute(
            """
            INSERT INTO medications_v2
            SELECT id, name, image_path, dose_amount, dose_unit, quantity, form,
                   comments, start_date, end_date, frequency_hours,
                   first_dose_time, status, suspended_at, completed_at,
                   created_at, updated_at
            FROM medications
            """
        )
        moved = con.execute("SELECT COUNT(*) FROM medications_v2").fetchone()[0]
        kept = con.execute("SELECT COUNT(*) FROM medications").fetchone()[0]
        if moved != kept:
            raise RuntimeError(
                f"medication migration would lose rows ({kept} -> {moved}), aborting"
            )
        con.execute("DROP TABLE medications")
        con.execute("ALTER TABLE medications_v2 RENAME TO medications")
        logger.info("migration: medication dose fields and end_date are now optional")

    # ---- simple column additions ------------------------------------------
    _add_column(con, "medication_doses", "status_changed_at", "DATETIME")
    # Backfill: the best evidence we have of when a dose changed status is when
    # the user marked it. Automatic "missed" rows from v1 stay NULL, which the
    # UI renders as "unknown" rather than inventing a timestamp.
    con.execute(
        "UPDATE medication_doses SET status_changed_at = marked_at "
        "WHERE status_changed_at IS NULL AND marked_at IS NOT NULL"
    )

    _add_column(con, "notifications", "kind", "VARCHAR(20)")
    _add_column(con, "notifications", "dedupe_key", "VARCHAR(80)")
    _add_column(con, "notifications", "email_sent_at", "DATETIME")
    # Give the v1 rows a dedupe key so the unique index can be created.
    con.execute(
        "UPDATE notifications SET dedupe_key = 'legacy:' || id WHERE dedupe_key IS NULL"
    )
    con.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS ix_notifications_dedupe_key "
        "ON notifications (dedupe_key)"
    )

    for column, ddl in (
        ("email_notifications", "BOOLEAN NOT NULL DEFAULT 0"),
        ("dose_before_30", "BOOLEAN NOT NULL DEFAULT 1"),
        ("dose_before_15", "BOOLEAN NOT NULL DEFAULT 1"),
        ("dose_before_5", "BOOLEAN NOT NULL DEFAULT 1"),
        ("dose_at_time", "BOOLEAN NOT NULL DEFAULT 1"),
        ("dose_after_15", "BOOLEAN NOT NULL DEFAULT 1"),
        ("dose_after_30", "BOOLEAN NOT NULL DEFAULT 1"),
        ("dose_overdue", "BOOLEAN NOT NULL DEFAULT 1"),
        ("email_recipient", "VARCHAR(320)"),
        ("email_sender", "VARCHAR(320)"),
        ("smtp_host", "VARCHAR(200)"),
        ("smtp_port", "INTEGER NOT NULL DEFAULT 587"),
        ("smtp_username", "VARCHAR(320)"),
        ("smtp_password_protected", "TEXT"),
        ("smtp_security", "VARCHAR(10) NOT NULL DEFAULT 'starttls'"),
    ):
        _add_column(con, "settings", column, ddl)

    # Check BEFORE committing: raising here rolls the whole migration back and
    # leaves the original file untouched. After a commit it would be too late.
    violations = con.execute("PRAGMA foreign_key_check").fetchall()
    if violations:
        raise RuntimeError(f"migration left dangling references: {violations[:5]}")

    con.commit()
    con.execute("PRAGMA foreign_keys = ON")
