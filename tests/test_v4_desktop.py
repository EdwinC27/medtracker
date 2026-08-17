"""v4: the desktop shell, Windows startup, System Status and the locked API.

What is testable here and what is not, stated plainly: the startup sequence,
the registry abstraction, the status page and the lock's effect on every route
are all exercised for real. Building the Windows executable and drawing a tray
icon are not — they need Windows and a desktop session, and pretending
otherwise in a test would be worse than admitting it.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from app.desktop import launcher, startup as desktop_startup
from app.services import applock, system_status
from app.services.settings_service import get_settings, update_settings


@pytest.fixture(autouse=True)
def clean_lock_state():
    applock.reset_for_tests()
    applock.unlock()
    yield
    applock.reset_for_tests()


# --------------------------------------------------------------------------- #
# Windows startup
# --------------------------------------------------------------------------- #
class FakeRegistry:
    """Stands in for HKCU\\...\\Run, so the behaviour can be tested anywhere."""

    def __init__(self):
        self.values: dict[str, str] = {}

    def install(self, monkeypatch):
        monkeypatch.setattr(desktop_startup, "is_supported", lambda: True)

        def read_state():
            command = self.values.get(desktop_startup.VALUE_NAME)
            return desktop_startup.StartupState(
                supported=True, enabled=command is not None, command=command
            )

        def apply(enabled):
            if enabled:
                self.values[desktop_startup.VALUE_NAME] = desktop_startup.launch_command()
            else:
                self.values.pop(desktop_startup.VALUE_NAME, None)
            return read_state()

        monkeypatch.setattr(desktop_startup, "read_state", read_state)
        monkeypatch.setattr(desktop_startup, "apply", apply)
        return self


@pytest.fixture()
def registry(monkeypatch):
    return FakeRegistry().install(monkeypatch)


def test_turning_the_switch_on_registers_the_application(db, registry):
    update_settings(db, {"start_with_windows": True})
    db.commit()

    assert get_settings(db).start_with_windows is True
    assert desktop_startup.VALUE_NAME in registry.values
    assert "--background" in registry.values[desktop_startup.VALUE_NAME]


def test_turning_it_off_removes_the_entry(db, registry):
    update_settings(db, {"start_with_windows": True})
    update_settings(db, {"start_with_windows": False})
    db.commit()

    assert get_settings(db).start_with_windows is False
    assert registry.values == {}


def test_the_setting_persists_and_is_reported(db, registry):
    update_settings(db, {"start_with_windows": True})
    db.commit()

    from app.services.settings_service import settings_to_dict

    data = settings_to_dict(get_settings(db))
    assert data["start_with_windows"] is True
    assert data["startup"]["enabled"] is True
    assert data["startup"]["supported"] is True


def test_the_registry_is_repaired_at_every_launch(db, registry):
    """Something else removed the entry; the next start puts it back."""
    update_settings(db, {"start_with_windows": True})
    db.commit()
    registry.values.clear()

    desktop_startup.reconcile(True)
    assert desktop_startup.VALUE_NAME in registry.values


def test_reconcile_removes_an_entry_the_user_switched_off(db, registry):
    registry.values[desktop_startup.VALUE_NAME] = "stale command"
    desktop_startup.reconcile(False)
    assert registry.values == {}


def test_nothing_happens_and_nothing_breaks_off_windows(monkeypatch, db):
    monkeypatch.setattr(desktop_startup, "is_supported", lambda: False)
    state = desktop_startup.apply(True)
    assert state.supported is False
    assert desktop_startup.read_state().supported is False
    # And the setting still saves, so the preference survives to a Windows box.
    update_settings(db, {"start_with_windows": True})
    assert get_settings(db).start_with_windows is True


def test_the_startup_command_points_at_something_real():
    """What matters is that the command names a program that exists and is
    quoted, not *which* program.

    This used to assert `sys.executable` appeared verbatim, which stopped being
    true the moment the source install started registering `pythonw.exe`
    instead of `python.exe` to avoid opening a console at every logon. It kept
    passing on Linux — where there is no `pythonw.exe` to prefer — and failed
    on the only machine that runs it.
    """
    import shlex

    command = desktop_startup.launch_command()

    assert command.startswith('"'), "a path with a space in it must be quoted"
    assert desktop_startup.BACKGROUND_FLAG in command

    parts = shlex.split(command, posix=False)
    program = Path(parts[0].strip('"'))
    assert program.exists(), f"{program} does not exist"

    # Either the packaged application on its own, or an interpreter plus the
    # entry point it needs — never an interpreter with nothing to run.
    if "Medication Organizer" not in program.name:
        script = Path(parts[1].strip('"'))
        assert script.name == "desktop.py" and script.exists()

    assert desktop_startup._command_is_runnable(command) is True


# --------------------------------------------------------------------------- #
# The startup sequence
# --------------------------------------------------------------------------- #
def test_the_report_names_every_step(db):
    report = launcher.StartupReport()
    report.add("paths", True)
    report.add("server", True)
    report.add("database", True)
    report.add("scheduler", False, required=False, detail="not started")

    assert report.ok is True                    # the scheduler is not required
    assert [step.key for step in report.failures] == ["scheduler"]
    assert "scheduler" in report.as_text()
    assert "FAILED" in report.as_text()


def test_a_required_step_failing_fails_the_start(db):
    report = launcher.StartupReport()
    report.add("paths", True)
    report.add("database", False, detail="disk is read-only")

    assert report.ok is False
    assert report.to_dict()["steps"][1]["detail"] == "disk is read-only"


def test_the_startup_message_names_the_component_that_failed(db):
    from app.desktop.messages import startup_failure_text

    report = launcher.StartupReport()
    report.add("paths", True)
    report.add("server", False, detail="port already in use")

    text = startup_failure_text(report)
    assert "port already in use" in text
    assert "medtracker.log" in text
    # Translated, and never a stack trace.
    assert "Traceback" not in text
    assert any(word in text for word in ("Web server", "Servidor web"))


def test_an_unwritable_data_folder_stops_the_start_before_anything_else(monkeypatch, tmp_path):
    """The first step, and the one everything else depends on."""
    from app.desktop import launcher as module

    # A data folder that cannot be created because its parent is a file. Unlike
    # a read-only directory, this fails for every user including root, so the
    # test means the same thing wherever it runs.
    blocker = tmp_path / "not-a-folder"
    blocker.write_text("in the way", encoding="utf-8")
    monkeypatch.setattr("app.config.DATA_DIR", blocker / "data")
    report, handle = module.start_application("127.0.0.1", 0, open_ui=False)

    assert report.ok is False
    assert handle is None
    assert [step.key for step in report.steps] == ["paths"]


def test_probing_a_port_with_nobody_on_it_says_so():
    assert launcher.probe("http://127.0.0.1:1", timeout=0.2) is None
    assert launcher.already_running(1) is None


# --------------------------------------------------------------------------- #
# System Status
# --------------------------------------------------------------------------- #
def test_the_page_reports_every_component(db):
    payload = system_status.collect(db)
    keys = {item["key"] for item in payload["components"]}

    assert keys == {
        "application", "database", "scheduler", "windows_notifications",
        "browser_notifications", "email_notifications", "backup", "network",
        "startup", "app_lock",
    }
    assert payload["overall"] in {"ok", "warning", "error"}
    assert payload["app_version"]
    assert payload["schema_version"]


def test_every_component_carries_a_level_and_a_translation_key(db):
    import json
    from pathlib import Path

    catalog = json.loads(
        (Path(__file__).resolve().parent.parent / "app" / "i18n" / "en.json").read_text(
            encoding="utf-8"
        )
    )

    for item in system_status.collect(db)["components"]:
        assert item["level"] in {"ok", "warning", "error", "disabled"}
        assert item["detail_key"], item["key"]
        section, _, key = item["detail_key"].partition(".")
        assert key in catalog[section], item["detail_key"]
        # And a title for the row itself.
        assert item["key"] in catalog["status"]


def test_the_database_row_reports_a_real_round_trip(db):
    database = next(
        item for item in system_status.collect(db)["components"] if item["key"] == "database"
    )
    assert database["level"] in {"ok", "warning"}
    assert database["engine"] == "SQLite"
    assert database["expected_schema_version"]


def test_a_database_that_does_not_answer_is_reported_not_raised(db, monkeypatch):
    class DeadSession:
        def execute(self, *_args, **_kwargs):
            raise RuntimeError("database is locked")

    row = system_status._database(DeadSession())
    assert row["level"] == "error"
    assert row["detail_key"] == "status.database_error"


def test_the_scheduler_row_says_whether_it_is_running(db):
    row = next(
        item for item in system_status.collect(db)["components"] if item["key"] == "scheduler"
    )
    # The suite disables the scheduler, so "not running" is the honest answer.
    assert row["level"] == "error"
    assert row["detail_key"] == "status.scheduler_stopped"


def test_email_is_reported_from_its_configuration_and_never_sent(db, monkeypatch):
    sent = []
    monkeypatch.setattr(
        "app.notifications.email.send_email",
        lambda *args, **kwargs: sent.append(args) or (True, None),
    )

    settings = get_settings(db)
    row = system_status._email_notifications(settings)
    assert row["level"] == "disabled"          # off by default

    update_settings(db, {
        "email_notifications": True, "smtp_host": "smtp.example.com",
        "email_recipient": "someone@example.com", "email_sender": "app@example.com",
    })
    row = system_status._email_notifications(get_settings(db))
    assert row["level"] == "ok"
    assert row["detail_key"] == "status.email_configured"

    assert sent == [], "System Status must never send an e-mail"


def test_an_incomplete_email_configuration_is_an_error_not_a_surprise(db):
    # Set straight on the row: the settings form refuses a half-filled e-mail
    # configuration, and this is about what happens when one exists anyway.
    settings = get_settings(db)
    settings.email_notifications = True
    settings.email_recipient = "x@example.com"
    settings.smtp_host = None
    db.flush()

    row = system_status._email_notifications(settings)
    assert row["level"] == "error"
    assert "smtp_host" in row["missing"]


def test_the_backup_row_never_creates_a_backup(db, tmp_path, monkeypatch):
    from app.services import backup as backup_service

    monkeypatch.setattr(backup_service, "DB_PATH", tmp_path / "medtracker.db")
    (tmp_path / "medtracker.db").write_bytes(b"")
    folder = tmp_path / "backups"
    folder.mkdir()
    settings = get_settings(db)
    settings.backup_location = str(folder)
    settings.backup_enabled = True
    db.flush()

    row = system_status._backup(settings)

    assert row["level"] == "warning"
    assert row["detail_key"] == "status.backup_never"
    # The whole point: looking at the page wrote nothing.
    assert list(folder.glob("*.db")) == []


def test_a_failed_backup_is_reported_as_failed(db, tmp_path):
    settings = get_settings(db)
    settings.backup_location = str(tmp_path)
    settings.last_backup_error = "the folder disappeared"
    db.flush()

    row = system_status._backup(settings)
    assert row["level"] == "error"
    assert row["detail_key"] == "status.backup_failed"


def test_one_broken_probe_does_not_break_the_page(db, monkeypatch):
    monkeypatch.setattr(
        system_status, "_backup",
        lambda _settings: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    payload = system_status.collect(db)

    backup_row = next(i for i in payload["components"] if i["key"] == "backup")
    assert backup_row["level"] == "error"
    assert backup_row["detail_key"] == "status.unavailable"
    # ...and the other eight are still there.
    assert len(payload["components"]) == 10


def test_the_health_probe_is_small_and_honest(db):
    payload = system_status.health(db)
    assert payload["database"] is True
    assert payload["scheduler"] is False       # disabled in the suite
    assert payload["version"]


def test_the_endpoint_is_read_only(client):
    """Two calls, and nothing in the database changed between them."""
    before = client.get("/api/system/status").json()
    after = client.get("/api/system/status").json()

    assert before["overall"] == after["overall"]
    assert {i["key"] for i in before["components"]} == {i["key"] for i in after["components"]}
    # The v3 fields are still there for anything that read them.
    assert "medication_count" in after
    assert "database_path" in after
    assert client.get("/api/health").json()["ok"] is True


# --------------------------------------------------------------------------- #
# The lock, over HTTP
# --------------------------------------------------------------------------- #
def enable_lock(client, pin="1234"):
    response = client.post("/api/lock/enable", json={"pin": pin, "confirm_pin": pin})
    assert response.status_code == 200
    return response


def test_nothing_medical_is_served_while_locked(client):
    client.post("/api/medications", json={
        "name": "Amoxicillin", "start_date": "2026-09-01", "end_date": "2026-09-05",
        "frequency_hours": 8, "first_dose_time": "10:00",
    })
    enable_lock(client)
    client.post("/api/lock/lock")

    for path in ("/api/today", "/api/medications", "/api/calendar", "/api/timeline",
                 "/api/search?q=amoxi", "/api/settings", "/api/system/status",
                 "/api/notifications/history", "/api/backups"):
        response = client.get(path)
        assert response.status_code == 423, path
        assert response.json()["error"] == "error.locked"
        assert "Amoxicillin" not in response.text


def test_the_pages_redirect_to_the_lock_screen(client):
    enable_lock(client)
    client.post("/api/lock/lock")

    response = client.get("/medications", follow_redirects=False)
    assert response.status_code == 303
    # ...remembering where they were going, so unlocking lands there.
    assert response.headers["location"] == "/lock?next=/medications"

    # And the lock screen itself is served, with no medical data on it.
    page = client.get("/lock")
    assert page.status_code == 200
    assert "lock-form" in page.text
    assert "main-nav" not in page.text          # no navigation before unlocking


def test_the_lock_screen_still_gets_its_translations(client):
    enable_lock(client)
    client.post("/api/lock/lock")

    payload = client.get("/api/bootstrap").json()
    assert payload["locked"] is True
    assert payload["catalog"]["lock"]["unlock"]
    # ...but not the settings, which carry e-mail addresses and schedules.
    assert payload["settings"] is None


def test_unlocking_over_http_restores_everything(client):
    enable_lock(client)
    client.post("/api/lock/lock")
    assert client.get("/api/today").status_code == 423

    assert client.post("/api/lock/unlock", json={"pin": "1234"}).status_code == 200
    assert client.get("/api/today").status_code == 200


def test_a_wrong_pin_over_http_keeps_the_door_shut(client):
    enable_lock(client)
    client.post("/api/lock/lock")

    response = client.post("/api/lock/unlock", json={"pin": "0000"})
    assert response.status_code == 422
    assert response.json()["fields"]["pin"] == "validation.pin_incorrect"
    assert client.get("/api/today").status_code == 423


def test_health_and_static_files_stay_reachable_while_locked(client):
    enable_lock(client)
    client.post("/api/lock/lock")

    assert client.get("/api/health").status_code == 200
    assert client.get("/static/css/styles.css").status_code == 200
    assert client.get("/api/lock/state").status_code == 200


def test_disabling_over_http_needs_the_pin(client):
    enable_lock(client)

    refused = client.post("/api/lock/disable", json={"current_pin": "0000"})
    assert refused.status_code == 422
    assert client.get("/api/lock/state").json()["enabled"] is True

    accepted = client.post("/api/lock/disable", json={"current_pin": "1234"})
    assert accepted.status_code == 200
    assert client.get("/api/lock/state").json()["enabled"] is False


def test_an_application_with_no_lock_behaves_exactly_as_before(client):
    """The whole feature is inert unless it is switched on."""
    assert client.get("/api/today").status_code == 200
    assert client.get("/medications", follow_redirects=False).status_code == 200
    assert client.get("/api/lock/state").json()["enabled"] is False
