"""v3 Backups: creating, keeping, restoring — and never losing anything."""

from __future__ import annotations

import sqlite3
from datetime import datetime, time, timedelta

import pytest

from app.database.migrations import CURRENT_VERSION
from app.services import backup as service
from app.services.errors import AppError, ValidationError
from app.services.settings_service import get_settings


def make_database(path, rows=1):
    """A small but genuine SQLite database, shaped like the real one."""
    connection = sqlite3.connect(str(path))
    connection.execute("CREATE TABLE IF NOT EXISTS medications (id INTEGER PRIMARY KEY, name TEXT)")
    connection.execute("DELETE FROM medications")
    connection.executemany(
        "INSERT INTO medications (id, name) VALUES (?, ?)",
        [(i, f"Medication {i}") for i in range(1, rows + 1)],
    )
    # Claim the current schema version, so restoring it is not treated as an
    # upgrade from an older file.
    connection.execute(f"PRAGMA user_version = {CURRENT_VERSION}")
    connection.commit()
    connection.close()
    return path


def names_in(path):
    connection = sqlite3.connect(str(path))
    try:
        return [row[0] for row in connection.execute("SELECT name FROM medications ORDER BY id")]
    finally:
        connection.close()


@pytest.fixture()
def live(tmp_path, db, monkeypatch):
    """A live database file, with the service pointed at it and at a temp folder."""
    path = make_database(tmp_path / "medtracker.db", rows=2)
    monkeypatch.setattr(service, "DB_PATH", path)
    settings = get_settings(db)
    settings.backup_location = str(tmp_path / "backups")
    settings.backup_keep = 3
    db.flush()
    return path


# --------------------------------------------------------------------------- #
# Creating
# --------------------------------------------------------------------------- #
def test_a_backup_is_a_readable_copy_of_the_database(db, live):
    settings = get_settings(db)
    backup = service.create_backup(settings, service.MANUAL)

    assert backup.path.exists()
    assert backup.size > 0
    assert backup.kind == "manual"
    assert names_in(backup.path) == ["Medication 1", "Medication 2"]


def test_the_file_name_says_what_kind_it_is_and_when(db, live):
    settings = get_settings(db)
    for kind in (service.AUTOMATIC, service.MANUAL, service.SAFETY, service.PRE_IMPORT):
        backup = service.create_backup(settings, kind)
        assert backup.path.name.startswith(f"medtracker-{kind}-")
        assert backup.path.suffix == ".db"
        assert service._parse_name(backup.path)[0] == kind


def test_an_unknown_kind_is_treated_as_a_manual_one(db, live):
    backup = service.create_backup(get_settings(db), "whatever")
    assert backup.kind == "manual"


def test_backing_up_a_database_that_is_not_there_fails_cleanly(db, tmp_path, monkeypatch):
    monkeypatch.setattr(service, "DB_PATH", tmp_path / "missing.db")
    settings = get_settings(db)
    settings.backup_location = str(tmp_path / "backups")
    with pytest.raises(AppError) as exc:
        service.create_backup(settings, service.MANUAL)
    assert exc.value.message_key == "error.backup_no_database"


def test_the_backup_folder_is_created_on_demand(db, tmp_path, live):
    settings = get_settings(db)
    settings.backup_location = str(tmp_path / "deep" / "nested" / "folder")
    backup = service.create_backup(settings, service.MANUAL)
    assert backup.path.parent.is_dir()


# --------------------------------------------------------------------------- #
# Listing and retention
# --------------------------------------------------------------------------- #
def write_backup(settings, kind, stamp):
    """Place a backup file with a chosen timestamp in its name."""
    directory = service.backup_directory(settings)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"medtracker-{kind}-{stamp}.db"
    make_database(path)
    return path


def test_the_list_is_newest_first_and_ignores_strangers(db, live):
    settings = get_settings(db)
    write_backup(settings, service.AUTOMATIC, "20260801-010000")
    write_backup(settings, service.AUTOMATIC, "20260803-010000")
    write_backup(settings, service.MANUAL, "20260802-120000")
    (service.backup_directory(settings) / "holiday-photo.db").write_text("nope")

    listed = service.list_backups(settings)
    assert [item.created_at.day for item in listed] == [3, 2, 1]
    assert all(item.path.name.startswith("medtracker-") for item in listed)


def test_retention_keeps_the_newest_automatic_ones(db, live):
    settings = get_settings(db)
    settings.backup_keep = 3
    for day in range(1, 8):
        write_backup(settings, service.AUTOMATIC, f"202608{day:02d}-010000")

    removed = service.prune_backups(settings)

    assert len(removed) == 4
    kept = [item.created_at.day for item in service.list_backups(settings)]
    assert kept == [7, 6, 5]


def test_retention_never_touches_a_backup_the_user_asked_for(db, live):
    settings = get_settings(db)
    settings.backup_keep = 1
    write_backup(settings, service.AUTOMATIC, "20260801-010000")
    write_backup(settings, service.AUTOMATIC, "20260802-010000")
    write_backup(settings, service.MANUAL, "20260701-010000")
    write_backup(settings, service.SAFETY, "20260702-010000")
    write_backup(settings, service.PRE_IMPORT, "20260703-010000")

    service.prune_backups(settings)

    kinds = sorted(item.kind for item in service.list_backups(settings))
    assert kinds == ["auto", "manual", "preimport", "safety"]


def test_retention_never_deletes_the_live_database(db, live, monkeypatch):
    """Paranoia test: even if the live file sat in the backup folder."""
    settings = get_settings(db)
    settings.backup_keep = 1
    decoy = write_backup(settings, service.AUTOMATIC, "20260101-010000")
    write_backup(settings, service.AUTOMATIC, "20260801-010000")
    monkeypatch.setattr(service, "DB_PATH", decoy)

    service.prune_backups(settings)
    assert decoy.exists()


def test_a_backup_outside_the_folder_cannot_be_reached(db, live):
    settings = get_settings(db)
    with pytest.raises(ValidationError) as exc:
        service.find_backup(settings, "../../etc/passwd")
    assert exc.value.fields["backup"] == "validation.backup_not_found"


def test_a_backup_that_does_not_exist_is_reported_as_missing(db, live):
    with pytest.raises(ValidationError):
        service.find_backup(get_settings(db), "medtracker-manual-20200101-000000.db")


# --------------------------------------------------------------------------- #
# Location
# --------------------------------------------------------------------------- #
def test_no_location_means_the_default_folder(db, live):
    settings = get_settings(db)
    settings.backup_location = None
    assert service.backup_directory(settings) == service.BACKUP_DIR
    assert service.status(settings)["is_default_location"] is True


def test_a_writable_folder_is_accepted_and_created(tmp_path):
    target = tmp_path / "chosen"
    assert service.validate_location(str(target)) == str(target)
    assert target.is_dir()
    assert not (target / ".medtracker-write-test").exists()


def test_a_blank_location_means_the_default(tmp_path):
    assert service.validate_location("   ") is None


def test_an_unusable_folder_is_refused(tmp_path):
    blocker = tmp_path / "a-file"
    blocker.write_text("not a folder")
    with pytest.raises(ValidationError) as exc:
        service.validate_location(str(blocker / "inside"))
    assert exc.value.fields["backup_location"] == "validation.backup_location_unusable"


# --------------------------------------------------------------------------- #
# Scheduling
# --------------------------------------------------------------------------- #
def test_nothing_is_due_when_backups_are_switched_off(db, live):
    settings = get_settings(db)
    settings.backup_enabled = False
    assert service.is_backup_due(settings, datetime(2026, 8, 20, 23, 0)) is False


def test_the_first_backup_waits_for_the_configured_time(db, live):
    settings = get_settings(db)
    settings.backup_enabled = True
    settings.backup_time = time(2, 0)
    settings.last_backup_at = None

    assert service.is_backup_due(settings, datetime(2026, 8, 20, 1, 30)) is False
    assert service.is_backup_due(settings, datetime(2026, 8, 20, 2, 0)) is True


def test_a_daily_backup_is_not_repeated_the_same_day(db, live):
    settings = get_settings(db)
    settings.backup_enabled = True
    settings.backup_time = time(2, 0)
    settings.last_backup_at = datetime(2026, 8, 20, 2, 0)

    assert service.is_backup_due(settings, datetime(2026, 8, 20, 18, 0)) is False
    assert service.is_backup_due(settings, datetime(2026, 8, 21, 2, 0)) is True


def test_a_weekly_backup_waits_a_week(db, live):
    settings = get_settings(db)
    settings.backup_enabled = True
    settings.backup_frequency = "weekly"
    settings.backup_time = time(2, 0)
    settings.last_backup_at = datetime(2026, 8, 20, 2, 0)

    assert service.is_backup_due(settings, datetime(2026, 8, 25, 3, 0)) is False
    assert service.is_backup_due(settings, datetime(2026, 8, 27, 3, 0)) is True


def test_a_missed_backup_is_taken_at_the_next_opportunity(db, live):
    """The machine was off at 02:00; the backup happens when it comes back."""
    settings = get_settings(db)
    settings.backup_enabled = True
    settings.backup_time = time(2, 0)
    settings.last_backup_at = datetime(2026, 8, 18, 2, 0)

    assert service.is_backup_due(settings, datetime(2026, 8, 20, 9, 15)) is True


def test_the_scheduled_run_records_itself_and_prunes(db, live):
    settings = get_settings(db)
    settings.backup_enabled = True
    settings.backup_time = time(2, 0)
    settings.backup_keep = 1
    settings.last_backup_at = None
    write_backup(settings, service.AUTOMATIC, "20260101-010000")

    reference = datetime(2026, 8, 20, 9, 0)
    result = service.run_scheduled_backup(db, reference)

    assert result is not None
    assert result["backup"]["kind"] == "auto"
    assert result["pruned"] == ["medtracker-auto-20260101-010000.db"]
    assert settings.last_backup_at == reference
    # And now it is no longer due.
    assert service.run_scheduled_backup(db, reference) is None


# --------------------------------------------------------------------------- #
# Restoring
# --------------------------------------------------------------------------- #
def test_restoring_brings_the_old_contents_back_and_keeps_a_safety_copy(db, live):
    settings = get_settings(db)
    backup = service.create_backup(settings, service.MANUAL)

    # The live database moves on.
    make_database(live, rows=5)
    assert len(names_in(live)) == 5

    result = service.restore_backup(db, backup.path.name)

    assert names_in(live) == ["Medication 1", "Medication 2"]
    safety = service.backup_directory(settings) / result["safety_backup"]["name"]
    assert safety.exists()
    assert len(names_in(safety)) == 5          # the state we were in a moment ago
    assert result["restored"]["name"] == backup.path.name


def test_a_file_that_is_not_a_database_is_refused(db, live):
    settings = get_settings(db)
    directory = service.backup_directory(settings)
    directory.mkdir(parents=True, exist_ok=True)
    rubbish = directory / "medtracker-manual-20260801-010000.db"
    rubbish.write_bytes(b"this is not SQLite at all, not even close" * 20)

    with pytest.raises(ValidationError) as exc:
        service.restore_backup(db, rubbish.name)
    assert exc.value.fields["backup"] == "validation.backup_invalid"
    assert names_in(live) == ["Medication 1", "Medication 2"]   # untouched


def test_a_database_without_the_expected_tables_is_refused(db, live):
    settings = get_settings(db)
    directory = service.backup_directory(settings)
    directory.mkdir(parents=True, exist_ok=True)
    stranger = directory / "medtracker-manual-20260802-010000.db"
    connection = sqlite3.connect(str(stranger))
    connection.execute("CREATE TABLE recipes (id INTEGER)")
    connection.commit()
    connection.close()

    with pytest.raises(ValidationError) as exc:
        service.restore_backup(db, stranger.name)
    assert exc.value.fields["backup"] == "validation.backup_invalid"


def test_the_status_summary_describes_the_configuration(db, live):
    settings = get_settings(db)
    settings.backup_enabled = True
    settings.backup_frequency = "daily"
    settings.backup_time = time(2, 30)
    service.create_backup(settings, service.MANUAL)

    status = service.status(settings)
    assert status["enabled"] is True
    assert status["frequency"] == "daily"
    assert status["time"] == "02:30"
    assert status["count"] == 1
    assert status["writable"] is True
    assert status["is_default_location"] is False
