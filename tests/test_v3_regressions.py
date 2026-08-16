"""Regressions found by reviewing v3 after it was written.

Each test here failed before the corresponding fix and passes after it.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, time, timedelta

import pytest

from app.database.migrations import CURRENT_VERSION, run_migrations
from app.models.models import DoseStatus, Notification
from app.notifications import dispatcher
from app.services import backup as backup_service
from app.services import import_service
from app.services import medications as medication_service
from app.services import scheduling
from app.services.errors import ValidationError
from app.services.settings_service import get_settings
from app.services.timeline import build_timeline
from tests.test_appointments import make_appointment, make_doctor
from tests.test_medications import make_payload

DAY = datetime(2026, 8, 20)


@pytest.fixture()
def clock(monkeypatch):
    holder = {"now": DAY.replace(hour=9)}

    def fake_now():
        return holder["now"]

    for module in (dispatcher, scheduling, medication_service):
        monkeypatch.setattr(module, "now_local", fake_now)
    monkeypatch.setattr("app.services.settings_service.now_local", fake_now)

    def at(hour, minute=0):
        holder["now"] = DAY.replace(hour=hour, minute=minute)
        return holder["now"]

    holder["at"] = at
    return holder


@pytest.fixture()
def dose(db, clock):
    medication = medication_service.create_medication(
        db,
        make_payload(
            start_date=DAY.date().isoformat(),
            end_date=DAY.date().isoformat(),
            frequency_hours=24,
            first_dose_time="10:00",
        ),
    )
    db.commit()
    return medication.doses[0]


# --------------------------------------------------------------------------- #
# A snooze must not be swallowed by the overdue rule
# --------------------------------------------------------------------------- #
def test_a_running_snooze_holds_the_dose_open_past_the_grace_period(db, dose, clock):
    """Snoozed at 11:59 for an hour: the 12:00 overdue sweep must not eat it."""
    at = clock["at"]
    at(11, 59)
    medication_service.snooze_dose(db, dose.id, 60)
    db.commit()

    at(12, 0)
    dispatcher.run_tick(db, send_windows=False, send_email=False)
    db.refresh(dose)
    assert dose.status == DoseStatus.SCHEDULED.value      # still pending

    at(12, 59)
    dispatcher.run_tick(db, send_windows=False, send_email=False)
    kinds = [row.kind for row in db.query(Notification).all()]
    assert "snooze" in kinds                              # the promise was kept

    at(13, 0)
    dispatcher.run_tick(db, send_windows=False, send_email=False)
    db.refresh(dose)
    assert dose.status == DoseStatus.MISSED.value         # and then it expires
    assert dose.snoozed_until is None                     # nothing stale left over
    assert dose.scheduled_at == DAY.replace(hour=10)      # never moved


def test_the_simple_overdue_sweep_honours_a_snooze_too(db, dose, clock):
    clock["at"](11, 59)
    medication_service.snooze_dose(db, dose.id, 60)
    db.commit()

    clock["at"](12, 0)
    assert scheduling.mark_overdue_doses_as_missed(db, 120) == 0
    assert dose.status == DoseStatus.SCHEDULED.value


def test_a_dose_marked_missed_does_not_keep_a_snooze_stamp(db, dose, clock):
    clock["at"](12, 0)
    assert scheduling.mark_overdue_doses_as_missed(db, 120) == 1
    assert dose.snoozed_until is None


# --------------------------------------------------------------------------- #
# Snoozing something that is not due yet
# --------------------------------------------------------------------------- #
def test_a_dose_days_away_cannot_be_snoozed(db, clock):
    """Otherwise "remind me in 10 minutes" produces an alert days too early."""
    medication = medication_service.create_medication(
        db,
        make_payload(
            start_date=(DAY.date() + timedelta(days=3)).isoformat(),
            end_date=(DAY.date() + timedelta(days=3)).isoformat(),
            frequency_hours=24,
            first_dose_time="10:00",
        ),
    )
    db.commit()

    with pytest.raises(ValidationError) as exc:
        medication_service.snooze_dose(db, medication.doses[0].id, 10)
    assert exc.value.fields["status"] == "validation.snooze_not_due_yet"


def test_a_dose_inside_its_reminder_window_can_be_snoozed(db, dose, clock):
    """The window opens with the first reminder, 30 minutes before the dose."""
    clock["at"](9, 30)
    medication_service.snooze_dose(db, dose.id, 10)
    assert dose.snoozed_until == DAY.replace(hour=9, minute=40)


def test_a_dose_already_late_can_still_be_snoozed(db, dose, clock):
    clock["at"](11, 0)
    medication_service.snooze_dose(db, dose.id, 30)
    assert dose.snoozed_until == DAY.replace(hour=11, minute=30)


# --------------------------------------------------------------------------- #
# Restoring a backup taken by an older version
# --------------------------------------------------------------------------- #
def build_v2_database(path):
    """A v2 file: no snoozed_until, no read_at, user_version 2."""
    connection = sqlite3.connect(str(path))
    connection.executescript(
        """
        CREATE TABLE medications (
            id INTEGER PRIMARY KEY, name VARCHAR(160) NOT NULL,
            image_path VARCHAR(255), dose_amount VARCHAR(40), dose_unit VARCHAR(20),
            quantity FLOAT, form VARCHAR(20), comments TEXT,
            start_date DATE NOT NULL, end_date DATE,
            frequency_hours INTEGER NOT NULL, first_dose_time TIME NOT NULL,
            status VARCHAR(20) NOT NULL, suspended_at DATETIME, completed_at DATETIME,
            created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL);
        CREATE TABLE medication_doses (
            id INTEGER PRIMARY KEY, medication_id INTEGER NOT NULL REFERENCES medications(id),
            scheduled_at DATETIME NOT NULL, status VARCHAR(20) NOT NULL,
            marked_at DATETIME, status_changed_at DATETIME,
            CONSTRAINT uq_dose_slot UNIQUE (medication_id, scheduled_at));
        CREATE TABLE doctors (
            id INTEGER PRIMARY KEY, name VARCHAR(160) NOT NULL, occupation VARCHAR(120),
            phone VARCHAR(40), notes TEXT,
            created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL);
        CREATE TABLE appointments (
            id INTEGER PRIMARY KEY, doctor_id INTEGER NOT NULL REFERENCES doctors(id),
            scheduled_at DATETIME NOT NULL, location VARCHAR(200), treatment VARCHAR(200),
            notes TEXT, next_appointment_at DATETIME,
            follow_up_of_id INTEGER REFERENCES appointments(id),
            reminder_days_3 BOOLEAN NOT NULL DEFAULT 1,
            reminder_day_1 BOOLEAN NOT NULL DEFAULT 1,
            reminder_hours_3 BOOLEAN NOT NULL DEFAULT 1,
            created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL);
        CREATE TABLE appointment_medications (
            appointment_id INTEGER NOT NULL REFERENCES appointments(id),
            medication_id INTEGER NOT NULL REFERENCES medications(id),
            PRIMARY KEY (appointment_id, medication_id));
        CREATE TABLE appointment_reminders (
            id INTEGER PRIMARY KEY, appointment_id INTEGER NOT NULL REFERENCES appointments(id),
            kind VARCHAR(20) NOT NULL, remind_at DATETIME NOT NULL, sent_at DATETIME);
        CREATE TABLE notifications (
            id INTEGER PRIMARY KEY, type VARCHAR(20) NOT NULL, kind VARCHAR(20),
            dedupe_key VARCHAR(120), reference_id INTEGER, fire_at DATETIME NOT NULL,
            title_key VARCHAR(120) NOT NULL, body_key VARCHAR(120) NOT NULL, payload TEXT,
            windows_sent_at DATETIME, browser_delivered_at DATETIME, email_sent_at DATETIME,
            error TEXT, created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE settings (
            id INTEGER PRIMARY KEY, language VARCHAR(5) NOT NULL DEFAULT 'es',
            default_first_dose_time TIME NOT NULL DEFAULT '10:00:00',
            ending_soon_days INTEGER NOT NULL DEFAULT 3,
            missed_after_minutes INTEGER NOT NULL DEFAULT 120,
            windows_notifications BOOLEAN NOT NULL DEFAULT 1,
            browser_notifications BOOLEAN NOT NULL DEFAULT 1,
            email_notifications BOOLEAN NOT NULL DEFAULT 0,
            medication_reminders BOOLEAN NOT NULL DEFAULT 1,
            appointment_reminders BOOLEAN NOT NULL DEFAULT 1,
            appt_reminder_days_3 BOOLEAN NOT NULL DEFAULT 1,
            appt_reminder_day_1 BOOLEAN NOT NULL DEFAULT 1,
            appt_reminder_hours_3 BOOLEAN NOT NULL DEFAULT 1,
            dose_before_30 BOOLEAN NOT NULL DEFAULT 1,
            dose_before_15 BOOLEAN NOT NULL DEFAULT 1,
            dose_before_5 BOOLEAN NOT NULL DEFAULT 1,
            dose_at_time BOOLEAN NOT NULL DEFAULT 1,
            dose_after_15 BOOLEAN NOT NULL DEFAULT 1,
            dose_after_30 BOOLEAN NOT NULL DEFAULT 1,
            dose_overdue BOOLEAN NOT NULL DEFAULT 1,
            email_recipient VARCHAR(200), email_sender VARCHAR(200),
            smtp_host VARCHAR(200), smtp_port INTEGER NOT NULL DEFAULT 587,
            smtp_username VARCHAR(200), smtp_security VARCHAR(20) NOT NULL DEFAULT 'starttls',
            smtp_password_protected TEXT,
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP);
        INSERT INTO settings (id) VALUES (1);
        INSERT INTO medications VALUES (1,'Ryaltris',NULL,'1','spray',1,'spray',NULL,
            '2026-08-01','2026-08-30',12,'10:00:00','active',NULL,NULL,
            '2026-08-01 09:00:00','2026-08-01 09:00:00');
        INSERT INTO medication_doses VALUES (1,1,'2026-08-01 10:00:00','taken',
            '2026-08-01 10:05:00','2026-08-01 10:05:00');
        INSERT INTO notifications (id,type,kind,dedupe_key,reference_id,fire_at,
            title_key,body_key,payload,created_at)
            VALUES (1,'dose','at_time','dose:1:at_time',1,'2026-08-01 10:00:00',
            'notification.medication_title','notification.dose_body','{}',
            '2026-08-01 10:00:00');
        PRAGMA user_version = 2;
        """
    )
    connection.commit()
    connection.close()
    return path


def test_a_backup_from_the_previous_version_is_restored_and_brought_forward(
    db, tmp_path, monkeypatch
):
    """The copy the upgrade itself takes must be restorable without breaking the app."""
    live = tmp_path / "medtracker.db"
    build_v2_database(live)
    run_migrations(live)                       # the live file is v3 now

    backups = tmp_path / "backups"
    backups.mkdir()
    old = backups / "medtracker-pre-v3-20260810-010000.db"
    build_v2_database(old)                     # ...but this copy is still v2

    monkeypatch.setattr(backup_service, "DB_PATH", live)
    settings = get_settings(db)
    settings.backup_location = str(backups)
    db.flush()

    # It is listed, and listed as what it is.
    listed = {item.path.name: item.kind for item in backup_service.list_backups(settings)}
    assert listed[old.name] == backup_service.PRE_MIGRATION

    result = backup_service.restore_backup(db, old.name)

    assert result["migrated"] == ["2->3", "3->4", "4->5"]
    connection = sqlite3.connect(str(live))
    try:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(medication_doses)")}
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        names = [row[0] for row in connection.execute("SELECT name FROM medications")]
    finally:
        connection.close()

    assert "snoozed_until" in columns          # the app can read it again
    assert version == CURRENT_VERSION
    assert names == ["Ryaltris"]               # and the data came back


def test_a_backup_from_a_newer_version_is_refused(db, tmp_path, monkeypatch):
    live = tmp_path / "medtracker.db"
    build_v2_database(live)
    run_migrations(live)

    backups = tmp_path / "backups"
    backups.mkdir()
    future = backups / "medtracker-manual-20270101-010000.db"
    build_v2_database(future)
    connection = sqlite3.connect(str(future))
    connection.execute(f"PRAGMA user_version = {CURRENT_VERSION + 1}")
    connection.commit()
    connection.close()

    monkeypatch.setattr(backup_service, "DB_PATH", live)
    settings = get_settings(db)
    settings.backup_location = str(backups)
    db.flush()

    with pytest.raises(ValidationError) as exc:
        backup_service.restore_backup(db, future.name)
    assert exc.value.fields["backup"] == "validation.backup_newer_schema"


def test_a_stranger_in_the_backup_folder_is_not_offered(db, tmp_path, monkeypatch):
    backups = tmp_path / "backups"
    backups.mkdir()
    (backups / "medtracker-holiday-20260101-010000.db").write_text("nope")
    settings = get_settings(db)
    settings.backup_location = str(backups)
    assert backup_service.list_backups(settings) == []


# --------------------------------------------------------------------------- #
# Upgrading must not fill the bell with months of old reminders
# --------------------------------------------------------------------------- #
def test_the_upgrade_marks_existing_notifications_as_already_read(tmp_path):
    path = build_v2_database(tmp_path / "old.db")
    run_migrations(path)

    connection = sqlite3.connect(str(path))
    try:
        unread = connection.execute(
            "SELECT COUNT(*) FROM notifications WHERE read_at IS NULL"
        ).fetchone()[0]
        stamped = connection.execute("SELECT read_at FROM notifications").fetchone()[0]
    finally:
        connection.close()

    assert unread == 0
    assert stamped is not None


# --------------------------------------------------------------------------- #
# Import: a broken file must fail as a file problem, before anything is deleted
# --------------------------------------------------------------------------- #
def base_export(**extra):
    payload = {
        "format": "medtracker-export", "version": 1,
        "doctors": [], "medications": [], "appointments": [],
        "medication_doses": [], "appointment_medications": [],
    }
    payload.update(extra)
    return payload


def test_a_medication_without_a_start_date_is_refused_up_front(db):
    payload = base_export(
        medications=[{"id": 1, "name": "Amoxicillin", "frequency_hours": 8,
                      "first_dose_time": "10:00"}]
    )
    with pytest.raises(ValidationError) as exc:
        import_service.parse_payload(json.dumps(payload))
    assert exc.value.fields["file"] == "validation.import_incomplete"


def test_a_medication_without_a_frequency_is_refused_up_front(db):
    payload = base_export(
        medications=[{"id": 1, "name": "Amoxicillin", "start_date": "2026-09-01",
                      "first_dose_time": "10:00"}]
    )
    with pytest.raises(ValidationError):
        import_service.parse_payload(json.dumps(payload))


def test_two_doses_in_the_same_slot_are_refused_up_front(db):
    payload = base_export(
        medications=[{"id": 1, "name": "Amoxicillin", "start_date": "2026-09-01",
                      "frequency_hours": 8, "first_dose_time": "10:00"}],
        medication_doses=[
            {"id": 1, "medication_id": 1, "scheduled_at": "2026-09-01T10:00:00"},
            {"id": 2, "medication_id": 1, "scheduled_at": "2026-09-01T10:00:00"},
        ],
    )
    with pytest.raises(ValidationError) as exc:
        import_service.parse_payload(json.dumps(payload))
    assert exc.value.fields["file"] == "validation.import_duplicate_dose"


def test_a_broken_file_leaves_the_database_alone(db):
    medication_service.create_medication(db, make_payload(name="Keep me"))
    db.commit()
    payload = base_export(medications=[{"id": 9, "name": "Broken"}])

    with pytest.raises(ValidationError):
        import_service.apply_import(db, payload)

    assert [m.name for m in medication_service.list_medications(db, "all")] == ["Keep me"]


# --------------------------------------------------------------------------- #
# The timeline is read one page at a time
# --------------------------------------------------------------------------- #
def test_the_timeline_is_paged(db):
    doctor = make_doctor(db, "Dr. Many")
    for index in range(7):
        make_appointment(
            db,
            when=(datetime(2026, 9, 1, 9, 0) + timedelta(days=index)),
            doctor=doctor,
        )
    db.commit()

    first = build_timeline(db, limit=3)
    assert len(first["entries"]) == 3
    assert first["total"] == 7
    assert first["has_more"] is True

    last = build_timeline(db, limit=3, offset=6)
    assert len(last["entries"]) == 1
    assert last["has_more"] is False


def test_a_silly_page_size_is_clamped(db):
    payload = build_timeline(db, limit=10 ** 6, offset=-4)
    assert payload["limit"] == 500
    assert payload["offset"] == 0


def test_the_timeline_endpoint_takes_a_page(client):
    body = client.get("/api/timeline?limit=2&offset=0").json()
    assert body["limit"] == 2 and body["offset"] == 0 and body["total"] == 0


# --------------------------------------------------------------------------- #
# Settings: the automatic-backup switch is reachable
# --------------------------------------------------------------------------- #
def test_the_automatic_backup_switch_round_trips_through_the_api(client):
    assert client.get("/api/settings").json()["backup_enabled"] is True

    client.put("/api/settings", json={"backup_enabled": False})
    assert client.get("/api/settings").json()["backup_enabled"] is False

    client.put("/api/settings", json={"backup_enabled": True})
    assert client.get("/api/settings").json()["backup_enabled"] is True


def test_the_settings_screen_reads_and_writes_that_switch():
    """The bug was in the browser, not the API: the checkbox was in neither list."""
    source = (
        __import__("pathlib").Path(__file__).resolve().parent.parent
        / "app" / "static" / "js" / "settings.js"
    ).read_text(encoding="utf-8")
    assert source.count("'backup_enabled'") == 2


def test_the_calendar_steps_month_by_month_from_the_first():
    """A month step taken from the 31st used to skip a month entirely."""
    source = (
        __import__("pathlib").Path(__file__).resolve().parent.parent
        / "app" / "static" / "js" / "calendar.js"
    ).read_text(encoding="utf-8")
    assert "d.setDate(1);" in source


# --------------------------------------------------------------------------- #
# The one new dependency, on an installation that was never updated
# --------------------------------------------------------------------------- #
def test_pdf_export_without_reportlab_says_what_to_do(db, monkeypatch):
    """CSV and JSON must keep working, and the message must be actionable."""
    import builtins

    from app.services import export_service
    from app.services.errors import AppError

    real_import = builtins.__import__

    def no_reportlab(name, *args, **kwargs):
        if name.startswith("reportlab"):
            raise ImportError("No module named 'reportlab'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_reportlab)

    with pytest.raises(AppError) as exc:
        export_service.export(db, "pdf", None, "en")
    assert exc.value.message_key == "error.pdf_unavailable"

    monkeypatch.undo()
    assert export_service.export(db, "json", None, "en").exists()


# --------------------------------------------------------------------------- #
# A v3 database and a v4 database have the same schema
# --------------------------------------------------------------------------- #
def test_a_v3_file_that_was_never_stamped_is_still_migrated(tmp_path):
    """v3 stamped `user_version` only on its *second* start, so a database from
    a first-run session looks brand new. It must not be mistaken for one: the
    3 -> 4 reclassification would be skipped forever."""
    from app.database.migrations import _migrate_2_to_3

    path = build_v2_database(tmp_path / "unstamped.db")
    # Exactly a v3 file: the v3 schema, and no stamp - which is the state v3's
    # own first run left behind.
    connection = sqlite3.connect(str(path))
    _migrate_2_to_3(connection)
    connection.close()

    connection = sqlite3.connect(str(path))
    connection.execute("PRAGMA user_version = 0")     # as v3's first run left it
    # A dose due at 08:00 for a medication registered at 09:00: exactly the
    # backlog a treatment entered late produces, swept to "missed" by v3.
    connection.execute(
        "INSERT INTO medication_doses (id, medication_id, scheduled_at, status, "
        "marked_at, status_changed_at) VALUES (50, 1, '2026-08-01 08:00:00', "
        "'missed', NULL, NULL)"
    )
    connection.commit()
    connection.close()

    report = run_migrations(path)

    assert "3->4" in report["applied"]
    connection = sqlite3.connect(str(path))
    try:
        assert connection.execute(
            "SELECT status FROM medication_doses WHERE id = 50"
        ).fetchone()[0] == "before_registration"
        assert connection.execute("PRAGMA user_version").fetchone()[0] == CURRENT_VERSION
    finally:
        connection.close()


def test_a_database_created_by_the_current_models_is_stamped(tmp_path, monkeypatch):
    """So the ambiguity above cannot reappear on the next version."""
    from sqlalchemy import create_engine

    from app.database import db as db_module
    from app.models.models import Base

    path = tmp_path / "fresh.db"
    monkeypatch.setattr("app.config.DB_PATH", path)
    monkeypatch.setattr(db_module, "engine", create_engine(f"sqlite:///{path}"))
    Base.metadata.create_all(bind=db_module.engine)
    db_module._stamp_schema_version()

    connection = sqlite3.connect(str(path))
    try:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == CURRENT_VERSION
    finally:
        connection.close()
