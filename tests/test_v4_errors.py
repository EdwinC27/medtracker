"""v4: what happens when things go wrong.

The rule the whole version is built around: a failure changes nothing, says
something the user can act on, keeps the technical detail in the log, and never
takes an unrelated part of the application down with it.
"""

from __future__ import annotations

import json
from datetime import datetime, time, timedelta

import pytest
from sqlalchemy.exc import OperationalError

from app.models.models import Notification
from app.notifications import dispatcher
from app.services import medications as medication_service
from app.services.settings_service import get_settings, update_settings
from tests.test_medications import make_payload, register_before_start


# --------------------------------------------------------------------------- #
# A client that behaves like the running server
# --------------------------------------------------------------------------- #
@pytest.fixture()
def client(tmp_path, monkeypatch):
    """Like the shared fixture, with the two details this file depends on.

    `raise_server_exceptions=False`: Starlette's error middleware re-raises
    after it has produced the 500, which under uvicorn only reaches the log.
    The default TestClient turns that into a test failure instead of letting us
    read the response the user would actually get.

    And the session is wired exactly like `get_db` — rollback on the way out —
    because "a failed write changes nothing" is a promise of that dependency,
    and a fixture that only closes the session would be testing the fixture.
    """
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.database import db as db_module
    from app.main import app
    from app.models.models import Base

    engine = create_engine(
        f"sqlite:///{tmp_path / 'test.db'}", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    TestingSession = sessionmaker(bind=engine, autoflush=False, future=True)

    monkeypatch.setattr(db_module, "engine", engine)
    monkeypatch.setattr(db_module, "SessionLocal", TestingSession)

    def override_get_db():
        session = TestingSession()
        try:
            yield session
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    app.dependency_overrides[db_module.get_db] = override_get_db
    with TestClient(
        app, base_url="http://127.0.0.1:8000", raise_server_exceptions=False
    ) as test_client:
        yield test_client
    app.dependency_overrides.clear()
    engine.dispose()


# --------------------------------------------------------------------------- #
# The user never sees a stack trace
# --------------------------------------------------------------------------- #
def test_a_database_failure_becomes_a_translated_message(client, monkeypatch):
    from app.services import medications as service

    def explode(*_args, **_kwargs):
        raise OperationalError("INSERT", {}, Exception("database is locked"))

    monkeypatch.setattr(service, "create_medication", explode)

    response = client.post("/api/medications", json=make_payload())

    assert response.status_code == 500
    body = response.json()
    assert body["error"] == "error.database"
    assert "Traceback" not in response.text
    assert "OperationalError" not in response.text
    assert "sqlite" not in response.text.lower()


def test_an_unexpected_failure_becomes_a_translated_message(client, monkeypatch):
    from app.services import medications as service

    monkeypatch.setattr(
        service, "list_medications",
        lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("something odd")),
    )
    response = client.get("/api/medications")

    assert response.status_code == 500
    assert response.json()["error"] == "error.generic"
    assert "something odd" not in response.text


def test_a_failed_write_leaves_the_data_exactly_as_it_was(client, monkeypatch):
    """The promise in §30: no partially modified state."""
    created = client.post("/api/medications", json=make_payload(name="Keep me"))
    assert created.status_code == 201
    before = client.get("/api/medications").json()["items"]

    from app.services import medications as service

    original = service.update_medication

    def half_way(db, medication_id, data):
        medication = original(db, medication_id, data)   # mutates the session
        raise RuntimeError("the disk went away")

    monkeypatch.setattr(service, "update_medication", half_way)

    failed = client.put(
        f"/api/medications/{before[0]['id']}",
        json=make_payload(name="Ruined", start_date=before[0]["start_date"]),
    )
    assert failed.status_code == 500

    after = client.get("/api/medications").json()["items"]
    assert [item["name"] for item in after] == ["Keep me"]
    assert after == before


def test_a_validation_error_still_names_the_field(client):
    response = client.post("/api/medications", json={"name": "", "frequency_hours": None})
    assert response.status_code == 422
    body = response.json()
    assert body["error"] == "error.validation"
    assert body["fields"]["name"] == "validation.name_required"


# --------------------------------------------------------------------------- #
# One channel failing is not the others' problem
# --------------------------------------------------------------------------- #
@pytest.fixture()
def due_dose(db):
    from app.utils.timeutil import now_local

    today = now_local().date()
    medication = medication_service.create_medication(
        db,
        make_payload(
            start_date=today.isoformat(),
            end_date=today.isoformat(),
            frequency_hours=24,
            first_dose_time=(now_local() - timedelta(minutes=1)).strftime("%H:%M"),
        ),
    )
    register_before_start(db, medication)
    db.commit()
    return medication


def test_a_broken_email_channel_does_not_stop_the_others(db, due_dose, monkeypatch):
    update_settings(db, {
        "email_notifications": True, "smtp_host": "smtp.example.com",
        "email_recipient": "x@example.com", "email_sender": "y@example.com",
    })
    db.commit()

    monkeypatch.setattr(
        "smtplib.SMTP",
        lambda *a, **k: (_ for _ in ()).throw(OSError("the network is down")),
    )

    summary = dispatcher.run_tick(db, send_windows=False)

    # The reminders were still worked out and queued; only the sending failed.
    assert summary["dose_notifications"] >= 1
    assert db.query(Notification).count() >= 1
    assert summary["emails_sent"] == 0


def test_a_channel_that_raises_outright_does_not_end_the_tick(db, due_dose, monkeypatch):
    """Not a handled failure — an unexpected one, from inside the channel."""
    update_settings(db, {
        "email_notifications": True, "smtp_host": "smtp.example.com",
        "email_recipient": "x@example.com", "email_sender": "y@example.com",
    })
    db.commit()

    monkeypatch.setattr(
        dispatcher, "_send_email_notifications",
        lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("channel exploded")),
    )

    summary = dispatcher.run_tick(db, send_windows=False)

    assert summary["emails_sent"] == 0
    assert "errors" in summary and any("emails_sent" in e for e in summary["errors"])
    # The rest of the pass still happened.
    assert summary["dose_notifications"] >= 1


def test_one_broken_stage_does_not_stop_the_next(db, due_dose, monkeypatch):
    """§33: a bad task must not terminate the scheduler."""
    monkeypatch.setattr(
        dispatcher, "_queue_dose_notifications",
        lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("bad dose")),
    )

    summary = dispatcher.run_tick(db, send_windows=False, send_email=False)

    assert summary["dose_notifications"] == 0
    assert any("dose_notifications" in e for e in summary["errors"])
    # The stages after it still ran and reported normally.
    assert "missed_doses" in summary
    assert "appointment_notifications" in summary
    assert summary["backup"] is None or isinstance(summary["backup"], dict)


def test_the_scheduler_thread_survives_a_failing_tick(db, monkeypatch):
    """The wrapper around the whole tick, which is what keeps the thread alive."""
    from app.notifications import scheduler as background_scheduler

    monkeypatch.setattr(
        "app.notifications.dispatcher.run_tick",
        lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("the whole tick failed")),
    )

    background_scheduler._job()          # must not raise

    status = background_scheduler.status()
    assert status["last_error"] and "the whole tick failed" in status["last_error"]


# --------------------------------------------------------------------------- #
# Backup, restore, import, export
# --------------------------------------------------------------------------- #
def test_a_failed_backup_leaves_the_database_alone_and_is_reported(db, tmp_path, monkeypatch):
    from app.services import backup as backup_service

    live = tmp_path / "medtracker.db"
    live.write_bytes(b"the real data")
    monkeypatch.setattr(backup_service, "DB_PATH", live)

    settings = get_settings(db)
    settings.backup_enabled = True
    settings.backup_location = str(tmp_path / "backups")
    settings.backup_time = time(0, 0)      # long past, whatever hour it is now
    settings.last_backup_at = None
    db.flush()

    monkeypatch.setattr(
        backup_service, "create_backup",
        lambda *_a, **_k: (_ for _ in ()).throw(OSError("the drive was unplugged")),
    )

    summary = dispatcher.run_tick(db, send_windows=False, send_email=False)

    assert live.read_bytes() == b"the real data"     # untouched
    assert summary["backup"] is None
    db.refresh(settings)
    assert settings.last_backup_error                # System Status can say so

    from app.services import system_status

    row = system_status._backup(settings)
    assert row["level"] == "error"
    assert row["detail_key"] == "status.backup_failed"


def test_a_backup_the_application_expected_to_fail_is_still_reported(db, tmp_path, monkeypatch):
    """Regression: `run_scheduled_backup` used to swallow its own AppError, so a
    missing backup folder looked exactly like "nothing was due yet" — the tick
    reported success and System Status stayed green."""
    from app.services import backup as backup_service
    from app.services.errors import AppError

    settings = get_settings(db)
    settings.backup_enabled = True
    settings.backup_location = str(tmp_path / "backups")
    settings.backup_time = time(0, 0)
    settings.last_backup_at = None
    db.flush()

    monkeypatch.setattr(
        backup_service, "create_backup",
        lambda *_a, **_k: (_ for _ in ()).throw(AppError("error.backup_failed")),
    )

    summary = dispatcher.run_tick(db, send_windows=False, send_email=False)

    assert summary["backup"] is None
    db.refresh(settings)
    # A translation key, not an English sentence and not a Python repr.
    assert settings.last_backup_error == "error.backup_failed"
    assert settings.last_backup_at is None

    from app.services import system_status

    assert system_status._backup(settings)["level"] == "error"


def test_a_failure_recorded_for_the_user_is_never_english_prose(client, monkeypatch):
    """Whatever the manual backup writes down has to survive being read in
    Spanish, so it is a key the frontend can translate."""
    from app.services import backup as backup_service
    from app.services.errors import AppError

    monkeypatch.setattr(
        backup_service, "create_backup",
        lambda *_a, **_k: (_ for _ in ()).throw(AppError("error.backup_location_unwritable")),
    )
    assert client.post("/api/backups").status_code == 400

    row = next(
        item for item in client.get("/api/system/status").json()["components"]
        if item["key"] == "backup"
    )
    assert row["last_error"] == "error.backup_location_unwritable"


def test_a_manual_backup_failure_says_so_and_changes_nothing(client, tmp_path, monkeypatch):
    from app.services import backup as backup_service

    monkeypatch.setattr(
        backup_service, "create_backup",
        lambda *_a, **_k: (_ for _ in ()).throw(
            backup_service.AppError("error.backup_failed")
        ),
    )

    response = client.post("/api/backups")

    assert response.status_code == 400
    assert response.json()["error"] == "error.backup_failed"
    assert "Traceback" not in response.text
    # And the medications are all still there.
    assert client.get("/api/medications").status_code == 200


def test_a_refused_restore_leaves_the_live_database_untouched(db, tmp_path, monkeypatch):
    import sqlite3

    from app.services import backup as backup_service
    from app.services.errors import ValidationError

    live = tmp_path / "medtracker.db"
    connection = sqlite3.connect(str(live))
    connection.execute("CREATE TABLE medications (id INTEGER PRIMARY KEY, name TEXT)")
    connection.execute("INSERT INTO medications VALUES (1, 'real')")
    connection.commit()
    connection.close()
    monkeypatch.setattr(backup_service, "DB_PATH", live)

    folder = tmp_path / "backups"
    folder.mkdir()
    rubbish = folder / "medtracker-manual-20260801-010000.db"
    rubbish.write_bytes(b"not a database at all" * 50)

    settings = get_settings(db)
    settings.backup_location = str(folder)
    db.flush()

    with pytest.raises(ValidationError):
        backup_service.restore_backup(db, rubbish.name)

    connection = sqlite3.connect(str(live))
    try:
        assert connection.execute("SELECT name FROM medications").fetchall() == [("real",)]
    finally:
        connection.close()


def test_a_rejected_import_changes_nothing(client):
    client.post("/api/medications", json=make_payload(name="Mine"))
    before = client.get("/api/medications").json()["items"]

    for bad in (b"{not json", json.dumps({"format": "something-else"}).encode()):
        response = client.post("/api/import", files={"file": ("x.json", bad, "application/json")})
        assert response.status_code == 422
        assert "Traceback" not in response.text

    assert client.get("/api/medications").json()["items"] == before


def test_a_failed_export_reports_instead_of_crashing(client, monkeypatch):
    from app.services import export_service

    monkeypatch.setattr(
        export_service, "export_json",
        lambda *_a, **_k: (_ for _ in ()).throw(OSError("no space left on device")),
    )

    response = client.post("/api/export", json={"format": "json"})

    assert response.status_code == 500
    assert response.json()["error"] == "error.generic"
    assert "no space left" not in response.text
    # The application is still alive.
    assert client.get("/api/health").json()["ok"] is True


def test_a_missing_export_file_is_a_clean_404(client):
    response = client.get("/api/export/medtracker-does-not-exist.json")
    assert response.status_code == 404
    assert response.json()["error"] == "error.not_found"


# --------------------------------------------------------------------------- #
# Nothing is silently destroyed
# --------------------------------------------------------------------------- #
def test_no_error_path_deletes_the_users_data(client, monkeypatch):
    """§40, checked end to end: provoke a failure on every write endpoint and
    count the rows afterwards."""
    medication = client.post("/api/medications", json=make_payload(name="Amoxicillin")).json()
    doctor = client.post("/api/doctors", json={"name": "Dr. Smith"}).json()
    client.post("/api/appointments", json={
        "doctor_id": doctor["id"],
        "scheduled_at": (datetime.now() + timedelta(days=3)).replace(microsecond=0).isoformat(),
    })

    counts = {
        "medications": len(client.get("/api/medications").json()["items"]),
        "doctors": len(client.get("/api/doctors").json()["items"]),
        "appointments": len(client.get("/api/appointments?scope=all").json()["items"]),
    }
    assert counts == {"medications": 1, "doctors": 1, "appointments": 1}

    from app.services import appointments as appointment_service
    from app.services import doctors as doctor_service
    from app.services import medications as medication_svc

    boom = lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("failure"))  # noqa: E731
    monkeypatch.setattr(medication_svc, "update_medication", boom)
    monkeypatch.setattr(doctor_service, "update_doctor", boom)
    monkeypatch.setattr(appointment_service, "update_appointment", boom)

    client.put(f"/api/medications/{medication['id']}", json=make_payload())
    client.put(f"/api/doctors/{doctor['id']}", json={"name": "x"})
    client.put("/api/appointments/1", json={"doctor_id": doctor["id"],
                                            "scheduled_at": "2026-09-01T10:00:00"})

    assert {
        "medications": len(client.get("/api/medications").json()["items"]),
        "doctors": len(client.get("/api/doctors").json()["items"]),
        "appointments": len(client.get("/api/appointments?scope=all").json()["items"]),
    } == counts


def test_the_settings_survive_a_failed_save(client):
    before = client.get("/api/settings").json()

    refused = client.put("/api/settings", json={"missed_after_minutes": 99999})
    assert refused.status_code == 422

    after = client.get("/api/settings").json()
    assert after["missed_after_minutes"] == before["missed_after_minutes"]


# --------------------------------------------------------------------------- #
# The frontend has somewhere to put an error
# --------------------------------------------------------------------------- #
def test_the_interface_has_an_error_boundary_and_a_retry():
    from pathlib import Path

    ui = (Path(__file__).resolve().parent.parent / "app" / "static" / "js" / "ui.js").read_text(
        encoding="utf-8"
    )
    assert "showPageError" in ui and "sectionError" in ui
    assert "common.retry" in ui
    # A locked application is a redirect, not an error message.
    assert "423" in ui


def test_every_error_the_backend_can_return_has_a_translation():
    import json as _json
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    catalog = _json.loads((root / "app" / "i18n" / "en.json").read_text(encoding="utf-8"))
    spanish = _json.loads((root / "app" / "i18n" / "es.json").read_text(encoding="utf-8"))

    keys = set()
    for path in (root / "app").rglob("*.py"):
        for match in re.findall(r'"((?:error|validation|message)\.[a-z_0-9]+)"',
                                path.read_text(encoding="utf-8")):
            keys.add(match)

    for key in sorted(keys):
        section, _, name = key.partition(".")
        assert name in catalog.get(section, {}), f"missing in en.json: {key}"
        assert name in spanish.get(section, {}), f"missing in es.json: {key}"
