"""Migration from the v1 schema to v2, run against a real v1 database.

The fixture builds a v1 file with the exact DDL v1 shipped with, fills it with
data, and then migrates it — which is what happens on the user's machine the
first time v2 starts.
"""

from __future__ import annotations

import sqlite3

import pytest

from app.database.migrations import CURRENT_VERSION, detect_version, run_migrations

V1_SCHEMA = """
CREATE TABLE medications (
    id INTEGER NOT NULL, name VARCHAR(160) NOT NULL, image_path VARCHAR(300),
    dose_amount VARCHAR(40) NOT NULL, dose_unit VARCHAR(20) NOT NULL,
    quantity FLOAT NOT NULL, form VARCHAR(30) NOT NULL, comments TEXT,
    start_date DATE NOT NULL, end_date DATE NOT NULL,
    frequency_hours INTEGER NOT NULL, first_dose_time TIME NOT NULL,
    status VARCHAR(20) NOT NULL, suspended_at DATETIME, completed_at DATETIME,
    created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL,
    PRIMARY KEY (id)
);
CREATE TABLE medication_doses (
    id INTEGER NOT NULL, medication_id INTEGER NOT NULL,
    scheduled_at DATETIME NOT NULL, status VARCHAR(20) NOT NULL,
    marked_at DATETIME, notified_at DATETIME, PRIMARY KEY (id),
    CONSTRAINT uq_dose_slot UNIQUE (medication_id, scheduled_at),
    FOREIGN KEY(medication_id) REFERENCES medications (id) ON DELETE CASCADE
);
CREATE TABLE appointments (
    id INTEGER NOT NULL, doctor_name VARCHAR(160) NOT NULL,
    scheduled_at DATETIME NOT NULL, location VARCHAR(200), treatment VARCHAR(300),
    notes TEXT, next_appointment_at DATETIME,
    reminder_days_3 BOOLEAN NOT NULL, reminder_day_1 BOOLEAN NOT NULL,
    reminder_hours_3 BOOLEAN NOT NULL,
    created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL, PRIMARY KEY (id)
);
CREATE TABLE appointment_reminders (
    id INTEGER NOT NULL, appointment_id INTEGER NOT NULL, kind VARCHAR(20) NOT NULL,
    remind_at DATETIME NOT NULL, sent_at DATETIME, PRIMARY KEY (id),
    CONSTRAINT uq_appointment_reminder UNIQUE (appointment_id, kind),
    FOREIGN KEY(appointment_id) REFERENCES appointments (id) ON DELETE CASCADE
);
CREATE TABLE appointment_medications (
    appointment_id INTEGER NOT NULL, medication_id INTEGER NOT NULL,
    PRIMARY KEY (appointment_id, medication_id),
    FOREIGN KEY(appointment_id) REFERENCES appointments (id) ON DELETE CASCADE,
    FOREIGN KEY(medication_id) REFERENCES medications (id) ON DELETE CASCADE
);
CREATE TABLE notifications (
    id INTEGER NOT NULL, type VARCHAR(20) NOT NULL, reference_id INTEGER,
    fire_at DATETIME NOT NULL, created_at DATETIME NOT NULL,
    title_key VARCHAR(80) NOT NULL, body_key VARCHAR(80) NOT NULL, payload TEXT,
    windows_sent_at DATETIME, browser_delivered_at DATETIME, error TEXT,
    PRIMARY KEY (id)
);
CREATE TABLE settings (
    id INTEGER NOT NULL, language VARCHAR(5), default_first_dose_time TIME NOT NULL,
    ending_soon_days INTEGER NOT NULL, missed_after_minutes INTEGER NOT NULL,
    windows_notifications BOOLEAN NOT NULL, browser_notifications BOOLEAN NOT NULL,
    medication_reminders BOOLEAN NOT NULL, appointment_reminders BOOLEAN NOT NULL,
    appt_reminder_days_3 BOOLEAN NOT NULL, appt_reminder_day_1 BOOLEAN NOT NULL,
    appt_reminder_hours_3 BOOLEAN NOT NULL, updated_at DATETIME NOT NULL,
    PRIMARY KEY (id)
);
"""

NOW = "2026-08-16 00:00:00"


@pytest.fixture()
def v1_db(tmp_path):
    """A populated v1 database, shaped like the real one this app shipped."""
    path = tmp_path / "medtracker.db"
    con = sqlite3.connect(str(path))
    con.executescript(V1_SCHEMA)

    con.execute(
        "INSERT INTO medications VALUES (1,'NeilMed','pic.png','1','unit',2.0,'puff',"
        "NULL,'2026-07-17','2026-10-17',8,'06:00:00','suspended',NULL,NULL,?,?)",
        (NOW, NOW),
    )
    con.execute(
        "INSERT INTO medications VALUES (2,'Ryaltris',NULL,'1','unit',2.0,'puff',"
        "NULL,'2026-07-17','2026-10-17',12,'11:58:00','active',NULL,NULL,?,?)",
        (NOW, NOW),
    )
    for index, (medication_id, status) in enumerate(
        [(1, "taken"), (1, "missed"), (2, "skipped"), (2, "scheduled")], start=1
    ):
        con.execute(
            "INSERT INTO medication_doses VALUES (?,?,?,?,?,NULL)",
            (index, medication_id, f"2026-08-1{index} 10:00:00", status,
             NOW if status in ("taken", "skipped") else None),
        )
    con.execute(
        "INSERT INTO appointments VALUES (1,'Dra. Brianda Rosas','2026-07-17 16:30:00',"
        "NULL,NULL,NULL,'2026-08-21 16:00:00',1,1,1,?,?)",
        (NOW, NOW),
    )
    con.execute(
        "INSERT INTO appointments VALUES (2,'Dra. Brianda Rosas','2026-09-01 09:00:00',"
        "NULL,NULL,NULL,NULL,1,0,1,?,?)",
        (NOW, NOW),
    )
    con.execute("INSERT INTO appointment_reminders VALUES (1,1,'day_1','2026-07-16 16:30:00',?)", (NOW,))
    con.execute("INSERT INTO appointment_medications VALUES (1,1)")
    con.execute(
        "INSERT INTO notifications VALUES (1,'dose',1,'2026-08-15 10:00:00',?,"
        "'notification.medication_title','notification.medication_body','{}',NULL,NULL,NULL)",
        (NOW,),
    )
    con.execute(
        "INSERT INTO settings VALUES (1,'es','10:00:00',3,120,1,1,1,1,1,1,1,?)", (NOW,)
    )
    con.commit()
    con.close()
    return path


def read(path, sql, *params):
    con = sqlite3.connect(str(path))
    try:
        return con.execute(sql, params).fetchall()
    finally:
        con.close()


def test_a_v1_file_is_recognised_as_version_1(v1_db):
    con = sqlite3.connect(str(v1_db))
    assert detect_version(con) == 1
    con.close()


def test_nothing_is_lost(v1_db):
    before = {
        table: read(v1_db, f"SELECT COUNT(*) FROM {table}")[0][0]
        for table in (
            "medications", "medication_doses", "appointments",
            "appointment_reminders", "appointment_medications",
            "notifications", "settings",
        )
    }

    run_migrations(v1_db)

    for table, count in before.items():
        assert read(v1_db, f"SELECT COUNT(*) FROM {table}")[0][0] == count, table


def test_dose_history_and_statuses_survive(v1_db):
    """Everything the user decided is kept exactly; only the statuses the
    application itself had chosen for doses that predate the medication's own
    registration are corrected (the v3 -> v4 step).

    In this fixture every dose is dated before its medication's `created_at`,
    so: `taken` and `skipped` are the user's and stay, while the automatic
    `missed` and the never-swept `scheduled` become `before_registration`.
    """
    before = read(v1_db, "SELECT id, status, marked_at FROM medication_doses ORDER BY id")
    assert [row[1] for row in before] == ["taken", "missed", "skipped", "scheduled"]

    run_migrations(v1_db)

    after = read(v1_db, "SELECT id, status, marked_at FROM medication_doses ORDER BY id")
    assert [row[1] for row in after] == [
        "taken", "before_registration", "skipped", "before_registration"
    ]
    # Ids and the user's own timestamps are untouched.
    assert [row[0] for row in after] == [row[0] for row in before]
    assert [row[2] for row in after] == [row[2] for row in before]


def test_a_dose_the_user_marked_is_never_reclassified(v1_db):
    """The discriminator is `marked_at`: a status the user chose stays."""
    run_migrations(v1_db)
    rows = read(
        v1_db,
        "SELECT status FROM medication_doses WHERE marked_at IS NOT NULL ORDER BY id",
    )
    assert [row[0] for row in rows] == ["taken", "skipped"]


def test_a_dose_after_its_medication_was_registered_keeps_its_status(v1_db):
    """Only the past is reclassified. A dose that came due after the medication
    existed is an ordinary dose and keeps whatever it was."""
    con = sqlite3.connect(str(v1_db))
    con.execute(
        "INSERT INTO medication_doses VALUES (99,1,'2026-08-20 10:00:00','scheduled',NULL,NULL)"
    )
    con.commit()
    con.close()

    run_migrations(v1_db)

    assert read(v1_db, "SELECT status FROM medication_doses WHERE id = 99")[0][0] == "scheduled"


def test_the_doctor_name_becomes_a_doctor_record(v1_db):
    run_migrations(v1_db)

    doctors = read(v1_db, "SELECT id, name, occupation, phone FROM doctors")
    assert len(doctors) == 1                      # one row for the one name
    assert doctors[0][1] == "Dra. Brianda Rosas"
    assert doctors[0][2] is None                  # nothing invented

    # Both appointments point at that record; the name is no longer duplicated.
    joined = read(
        v1_db,
        "SELECT a.id, d.name FROM appointments a JOIN doctors d ON d.id = a.doctor_id "
        "ORDER BY a.id",
    )
    assert joined == [(1, "Dra. Brianda Rosas"), (2, "Dra. Brianda Rosas")]
    columns = [row[1] for row in read(v1_db, "PRAGMA table_info('appointments')")]
    assert "doctor_name" not in columns
    assert "follow_up_of_id" in columns


def test_medication_fields_become_optional_without_touching_the_values(v1_db):
    run_migrations(v1_db)

    rows = read(v1_db, "SELECT id, name, dose_amount, end_date, image_path FROM medications ORDER BY id")
    assert rows == [
        (1, "NeilMed", "1", "2026-10-17", "pic.png"),
        (2, "Ryaltris", "1", "2026-10-17", None),
    ]
    ddl = read(v1_db, "SELECT sql FROM sqlite_master WHERE name='medications'")[0][0]
    assert "end_date DATE NOT NULL" not in ddl.replace("\n", " ")

    # A NULL dose is now actually storable.
    con = sqlite3.connect(str(v1_db))
    con.execute(
        "INSERT INTO medications (id,name,start_date,end_date,frequency_hours,"
        "first_dose_time,status,created_at,updated_at) VALUES (3,'Vitamin D',"
        "'2026-08-01',NULL,24,'09:00:00','active',?,?)",
        (NOW, NOW),
    )
    con.commit()
    con.close()


def test_the_new_settings_arrive_with_sensible_defaults(v1_db):
    run_migrations(v1_db)
    row = read(
        v1_db,
        "SELECT language, email_notifications, dose_before_30, dose_overdue, "
        "smtp_port, smtp_security, smtp_password_protected FROM settings",
    )[0]
    assert row[0] == "es"        # the user's own preference is kept
    assert row[1] == 0           # e-mail starts off
    assert row[2] == 1 and row[3] == 1   # the six dose reminders start on
    assert row[4] == 587
    assert row[5] == "starttls"
    assert row[6] is None


def test_old_notifications_get_a_dedupe_key_and_the_unique_index(v1_db):
    run_migrations(v1_db)
    keys = read(v1_db, "SELECT dedupe_key FROM notifications")
    assert keys == [("legacy:1",)]

    indexes = [row[1] for row in read(v1_db, "PRAGMA index_list('notifications')")]
    assert "ix_notifications_dedupe_key" in indexes


def test_the_database_is_left_consistent(v1_db):
    run_migrations(v1_db)
    assert read(v1_db, "PRAGMA foreign_key_check") == []
    assert read(v1_db, "PRAGMA integrity_check")[0][0] == "ok"
    assert read(v1_db, "PRAGMA user_version")[0][0] == CURRENT_VERSION


def test_running_it_twice_changes_nothing(v1_db):
    first = run_migrations(v1_db)
    assert first["applied"] == ["1->2", "2->3", "3->4", "4->5"]

    snapshot = read(v1_db, "SELECT id, status FROM medication_doses ORDER BY id")
    second = run_migrations(v1_db)

    assert second["applied"] == []
    assert read(v1_db, "SELECT id, status FROM medication_doses ORDER BY id") == snapshot


def test_a_brand_new_database_is_not_migrated(tmp_path):
    """An empty file created by SQLAlchemy is already at the current version."""
    path = tmp_path / "fresh.db"
    sqlite3.connect(str(path)).close()
    assert run_migrations(path)["applied"] == []


def test_a_blank_doctor_name_still_migrates(tmp_path):
    """An appointment saved without a doctor gets one placeholder record."""
    path = tmp_path / "blank.db"
    con = sqlite3.connect(str(path))
    con.executescript(V1_SCHEMA)
    con.execute(
        "INSERT INTO appointments VALUES (1,'','2026-07-17 16:30:00',NULL,NULL,NULL,"
        "NULL,1,1,1,?,?)",
        (NOW, NOW),
    )
    con.commit()
    con.close()

    run_migrations(path)

    assert read(path, "SELECT COUNT(*) FROM appointments")[0][0] == 1
    assert read(path, "PRAGMA foreign_key_check") == []
