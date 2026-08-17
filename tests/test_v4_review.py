"""Regressions for the defects the v4 review found.

Every test here failed before the fix it names. They are kept together because
what they have in common is the failure mode, not the feature: each one is a
case where the application looked fine and quietly did the wrong thing —
served medical data, lost the database, ran a whole notification pass it was
never supposed to run.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.services.settings_service import get_settings

BASE = "http://127.0.0.1:8000"


def enable_lock(client, pin="1234"):
    response = client.post("/api/lock/enable", json={"pin": pin, "confirm_pin": pin})
    assert response.status_code == 200, response.text
    return response


# --------------------------------------------------------------------------- #
# Where the data lives (CRITICAL: the packaged build used to lose all of it)
# --------------------------------------------------------------------------- #
def test_the_frozen_application_keeps_its_data_outside_its_own_folder(monkeypatch, tmp_path):
    """`__file__` inside a PyInstaller bundle points at a folder the next build
    replaces. Anchoring the database there means every upgrade deletes the
    user's entire history.

    `sys.executable` is pinned somewhere empty on purpose. Left alone, this test
    reads whatever happens to sit near the interpreter running it — which is
    nothing on a build machine and *the developer's own installation* when the
    virtual environment lives inside the project, so it passed here and failed
    on the one machine that matters.
    """
    import importlib

    lonely = tmp_path / "Program Files" / "MedTracker" / "app.exe"
    lonely.parent.mkdir(parents=True)
    lonely.write_bytes(b"")

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(lonely))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "AppData"))
    monkeypatch.delenv("MEDTRACKER_DATA_DIR", raising=False)

    from app import config

    reloaded = importlib.reload(config)
    try:
        assert reloaded.FROZEN is True
        assert reloaded.DATA_DIR == tmp_path / "AppData" / "MedTracker" / "data"
        # And nowhere near the code, or near the executable.
        assert reloaded.BASE_DIR not in reloaded.DATA_DIR.parents
        assert lonely.parent not in reloaded.DATA_DIR.parents
        assert reloaded.DB_PATH.parent == reloaded.DATA_DIR
    finally:
        monkeypatch.undo()
        importlib.reload(config)


def test_the_data_folder_is_not_created_at_import_time(monkeypatch, tmp_path):
    """Installing into a folder the user cannot write to used to raise inside an
    `import`, before any code existed to turn it into a message."""
    import importlib

    blocker = tmp_path / "a-file"
    blocker.write_text("in the way", encoding="utf-8")
    monkeypatch.setenv("MEDTRACKER_DATA_DIR", str(blocker / "data"))

    from app import config

    reloaded = importlib.reload(config)          # must not raise
    try:
        assert isinstance(reloaded.DIRECTORY_ERROR, OSError)
        with pytest.raises(OSError):
            reloaded.ensure_directories()        # ...but says so when asked
    finally:
        monkeypatch.undo()
        importlib.reload(config)


def test_an_existing_installation_is_adopted_on_the_first_frozen_run(monkeypatch, tmp_path):
    """Running the .exe for the first time on a machine that already has data
    must not look like a brand new, empty application."""
    from app.utils import datamove

    old = tmp_path / "ProyectoPersonal" / "data"
    old.mkdir(parents=True)
    connection = sqlite3.connect(str(old / "medtracker.db"))
    connection.execute("CREATE TABLE medications (id INTEGER PRIMARY KEY, name TEXT)")
    connection.execute("INSERT INTO medications VALUES (1, 'Amoxicillin')")
    connection.commit()
    connection.close()
    (old / "uploads").mkdir()
    (old / "uploads" / "photo.png").write_bytes(b"a photograph")

    exe = tmp_path / "ProyectoPersonal" / "dist" / "Medication Organizer" / "app.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"")
    new = tmp_path / "AppData" / "MedTracker" / "data"

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe))
    monkeypatch.setattr("app.config.DATA_DIR", new)
    monkeypatch.setattr("app.config.DB_PATH", new / "medtracker.db")
    monkeypatch.setattr("app.config.UPLOAD_DIR", new / "uploads")

    assert datamove.adopt_existing_database() == old

    connection = sqlite3.connect(str(new / "medtracker.db"))
    try:
        assert connection.execute("SELECT name FROM medications").fetchall() == [
            ("Amoxicillin",)
        ]
    finally:
        connection.close()
    assert (new / "uploads" / "photo.png").read_bytes() == b"a photograph"
    # The original is left exactly where it was, as its own fallback.
    assert (old / "medtracker.db").exists()

    # And it is a one-off: a second run finds a database and does nothing.
    assert datamove.adopt_existing_database() is None


def test_pre_v4_photographs_are_brought_across(monkeypatch, tmp_path):
    from app.utils import datamove

    legacy = tmp_path / "static" / "uploads"
    legacy.mkdir(parents=True)
    (legacy / "old.png").write_bytes(b"an old photograph")
    destination = tmp_path / "data" / "uploads"

    monkeypatch.setattr(datamove, "legacy_upload_dir", lambda: legacy)
    monkeypatch.setattr("app.config.UPLOAD_DIR", destination)
    monkeypatch.setattr("app.config._default_data_dir", lambda: destination.parent)
    monkeypatch.setattr("app.config.DATA_DIR", destination.parent)

    assert datamove.migrate_legacy_uploads() == 1
    assert (destination / "old.png").read_bytes() == b"an old photograph"
    assert datamove.migrate_legacy_uploads() == 0        # idempotent


# --------------------------------------------------------------------------- #
# The lock (CRITICAL: it used to fail open, and to leak the photographs)
# --------------------------------------------------------------------------- #
def test_an_unreadable_database_locks_the_door_rather_than_opening_it(client, monkeypatch):
    """Holding the database busy must not be a way past the PIN screen."""
    from app.routes import lock

    enable_lock(client)
    client.post("/api/lock/lock")
    assert client.get("/api/today").status_code == 423

    def unreadable(*_args, **_kwargs):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(lock, "_read_lock_state", unreadable)
    assert client.get("/api/today").status_code == 423
    assert client.get("/medications", follow_redirects=False).status_code == 303


def test_an_unreadable_database_does_not_lock_out_an_application_with_no_lock(
    client, monkeypatch
):
    """The other half of the same rule: refusing everything because the lock
    state is unknown, on a machine with no lock at all, would be worse."""
    from app.routes import lock

    assert client.get("/api/today").status_code == 200   # primes the cache

    monkeypatch.setattr(
        lock, "_read_lock_state",
        lambda *_a, **_k: (_ for _ in ()).throw(sqlite3.OperationalError("busy")),
    )
    assert client.get("/api/today").status_code == 200


def test_the_photographs_are_not_served_while_locked(client, tmp_path, monkeypatch):
    """They used to live under /static/, which the lock has to let through."""
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    (uploads / "pill.png").write_bytes(b"a photograph of a medication")
    monkeypatch.setattr("app.routes.api.UPLOAD_DIR", uploads)

    assert client.get("/api/uploads/pill.png").status_code == 200

    enable_lock(client)
    client.post("/api/lock/lock")
    assert client.get("/api/uploads/pill.png").status_code == 423
    # ...while the lock screen's own stylesheet still loads.
    assert client.get("/static/css/styles.css").status_code == 200


def test_an_upload_cannot_be_talked_into_leaving_its_folder(client, tmp_path, monkeypatch):
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    monkeypatch.setattr("app.routes.api.UPLOAD_DIR", uploads)
    secret = tmp_path / "secret.txt"
    secret.write_text("not yours", encoding="utf-8")

    assert client.get("/api/uploads/..%2Fsecret.txt").status_code in (404, 400)
    assert client.get("/api/uploads/nope.png").status_code == 404


def another_browser():
    """A second client against the *same running* application.

    Deliberately not `with TestClient(...)`: entering that context runs the
    lifespan, and the lifespan locks the application — which would make any
    "the second browser is locked out" assertion pass for entirely the wrong
    reason, and would silently throw the first browser out too. A phone joining
    the network does not restart the server.
    """
    from app.main import app

    return TestClient(app, base_url=BASE)


def test_the_unlock_belongs_to_one_browser(client, tmp_path, monkeypatch):
    """It used to be a single flag on the process, so one person unlocking let
    every other client straight in."""
    enable_lock(client)
    client.post("/api/lock/lock")
    assert client.post("/api/lock/unlock", json={"pin": "1234"}).status_code == 200
    assert client.get("/api/today").status_code == 200

    other = another_browser()          # its own, empty, cookie jar
    assert other.get("/api/today").status_code == 423
    assert other.get("/api/bootstrap").json()["settings"] is None


def test_polling_does_not_count_as_somebody_being_there(db):
    """Auto-lock measured idleness against traffic, and the page polls itself
    every thirty seconds — so it never fired."""
    from datetime import datetime, timedelta

    from app.services import applock

    applock.reset_for_tests()
    settings = get_settings(db)
    applock.enable(db, settings, "1234", "1234")
    applock.set_auto_lock(db, settings, 5)
    applock.unlock()

    now = datetime(2026, 8, 20, 12, 0)
    applock._session.last_seen = now - timedelta(minutes=30)

    # Thirty minutes of polling later, it is still thirty minutes idle...
    assert applock.is_locked(settings, reference=now) is True

    # ...and a person doing something is what changes that.
    applock.reset_for_tests()
    applock.unlock()
    applock._session.last_seen = now - timedelta(minutes=30)
    applock.touch()
    assert applock.is_locked(settings) is False
    applock.reset_for_tests()


def test_the_activity_endpoint_is_the_only_thing_that_resets_the_clock(client):
    """And it is behind the lock, so it cannot be used to hold the door open."""
    enable_lock(client)
    assert client.post("/api/lock/activity").status_code == 200
    client.post("/api/lock/lock")
    assert client.post("/api/lock/activity").status_code == 423


def test_restoring_a_backup_asks_for_the_pin_again(db, tmp_path, monkeypatch):
    """The restored file carries its own PIN — possibly a different one."""
    from app.services import applock, backup as backup_service

    applock.reset_for_tests()
    settings = get_settings(db)
    applock.enable(db, settings, "1234", "1234")
    db.commit()
    assert applock.is_locked(settings) is False

    from app.database.migrations import CURRENT_VERSION

    def make(path):
        connection = sqlite3.connect(str(path))
        connection.execute("CREATE TABLE medications (id INTEGER PRIMARY KEY, name TEXT)")
        connection.execute(f"PRAGMA user_version = {CURRENT_VERSION}")
        connection.commit()
        connection.close()

    live = tmp_path / "medtracker.db"
    make(live)
    folder = tmp_path / "backups"
    folder.mkdir()
    snapshot = folder / "medtracker-manual-20260801-010000.db"
    make(snapshot)

    settings.backup_location = str(folder)
    db.flush()
    monkeypatch.setattr(backup_service, "DB_PATH", live)
    monkeypatch.setattr("app.database.migrations.run_migrations", lambda *_a, **_k: {"applied": []})

    backup_service.restore_backup(db, snapshot.name)

    assert applock.is_locked(settings) is True
    applock.reset_for_tests()


# --------------------------------------------------------------------------- #
# Another website must not be able to drive this one
# --------------------------------------------------------------------------- #
def test_a_write_from_another_website_is_refused(client):
    from tests.test_medications import make_payload

    hostile = {"Origin": "https://evil.example"}
    response = client.post("/api/medications", json=make_payload(), headers=hostile)
    assert response.status_code == 403
    assert response.json()["error"] == "error.cross_site"
    assert client.get("/api/medications").json()["items"] == []


def test_the_import_endpoint_is_not_reachable_from_another_website(client):
    """The worst case: `/api/import` replaces the whole database, and a
    multipart post needs no permission from us before the browser sends it."""
    import json

    body = json.dumps({"format": "medtracker", "medications": []}).encode()
    response = client.post(
        "/api/import",
        files={"file": ("x.json", body, "application/json")},
        headers={"Origin": "https://evil.example"},
    )
    assert response.status_code == 403


def test_a_hostile_domain_pointed_at_this_machine_is_refused(client):
    """DNS rebinding: the request arrives on the loopback interface, but the
    browser still puts the attacker's name in `Host`."""
    response = client.get("/api/today", headers={"Host": "rebind.evil.example"})
    assert response.status_code == 403


def test_the_application_still_answers_its_own_browser(client):
    from tests.test_medications import make_payload

    ours = {"Origin": BASE, "Referer": f"{BASE}/medications"}
    assert client.post("/api/medications", json=make_payload(), headers=ours).status_code == 201
    assert client.get("/api/today", headers=ours).status_code == 200


def test_a_command_line_client_is_not_treated_as_an_attacker(client):
    """No Origin and no Referer at all is curl or the tray, not a web page."""
    from tests.test_medications import make_payload

    assert client.post("/api/medications", json=make_payload()).status_code == 201


# --------------------------------------------------------------------------- #
# Starting up
# --------------------------------------------------------------------------- #
def test_a_start_that_cannot_have_the_port_does_nothing_at_all(monkeypatch, tmp_path):
    """Uvicorn runs the lifespan before it binds, so a second copy on a taken
    port used to migrate the database, mark doses missed, send the reminders and
    write a backup — against the file the first copy had open — and only then
    report that it could not start."""
    import socket

    from app.desktop import launcher

    monkeypatch.setenv("MEDTRACKER_DATA_DIR", str(tmp_path))

    with socket.socket() as squatter:
        squatter.bind(("127.0.0.1", 0))
        squatter.listen(1)
        port = squatter.getsockname()[1]

        started = []
        monkeypatch.setattr("app.database.db.init_db", lambda: started.append("db"))

        report, handle = launcher.start_application("127.0.0.1", port, open_ui=False)

    assert report.ok is False
    assert handle is None
    assert [step.key for step in report.steps] == ["paths", "port"]
    assert report.steps[-1].ok is False
    # Nothing ran: no migration, no tick, no notification, no backup.
    assert started == []


def test_the_message_for_a_taken_port_says_so_and_is_translated():
    from app.desktop import launcher, messages

    report = launcher.StartupReport()
    report.add("paths", True)
    report.add("port", False, detail="address already in use")

    english = messages._translate("remedy_port", "en")
    spanish = messages._translate("remedy_port", "es")
    assert english and spanish and english != spanish

    # The message the user actually gets names the real problem, instead of the
    # old "please restart the application" — advice that, for a port somebody
    # else is holding, fails in exactly the same way for ever.
    body = messages.startup_failure_text(report)
    assert english in body or spanish in body
    assert "Traceback" not in body


def test_the_startup_timeout_can_actually_be_changed(monkeypatch):
    """It used to be bound as a default argument at import, so changing the
    constant did nothing and the wait was always thirty seconds."""
    import time

    from app.desktop import launcher

    monkeypatch.setattr(launcher, "STARTUP_TIMEOUT_SECONDS", 0.3)
    monkeypatch.setattr(launcher, "POLL_SECONDS", 0.05)

    started = time.monotonic()
    assert launcher.wait_until_healthy("http://127.0.0.1:1") is None
    assert time.monotonic() - started < 5


def test_upgrading_does_not_silently_add_the_app_to_windows_startup(tmp_path):
    """Installing an update is not consent to appear in the user's Startup
    list. A brand new database may default to on; a migrated one must not."""
    from app.database.migrations import _migrate_5_to_6

    path = tmp_path / "v5.db"
    connection = sqlite3.connect(str(path))
    connection.execute("CREATE TABLE settings (id INTEGER PRIMARY KEY)")
    connection.execute("INSERT INTO settings (id) VALUES (1)")
    connection.commit()

    _migrate_5_to_6(connection)

    row = connection.execute("SELECT start_with_windows, app_lock_enabled FROM settings").fetchone()
    connection.close()
    assert row == (0, 0)


def test_a_missing_run_key_is_not_an_error():
    """`OpenKey` raises FileNotFoundError when the key is absent, which used to
    be reported as a red 'startup failed' card."""
    from app.desktop import startup

    if startup.is_supported():           # pragma: no cover - only on Windows
        state = startup.read_state()
        assert state.error is None or "not found" not in state.error.lower()
    else:
        state = startup.read_state()
        assert state.supported is False and state.error is None


def test_a_startup_entry_pointing_at_nothing_is_noticed(tmp_path):
    from app.desktop import startup

    # A file that exists, made here rather than borrowed from the machine: what
    # is being checked is "does this path exist", not anything about Python.
    real = tmp_path / "Medication Organizer.exe"
    real.write_bytes(b"")

    assert startup._command_is_runnable(f'"{real}" --background') is True
    assert startup._command_is_runnable(f'"{tmp_path / "gone.exe"}"') is False
    assert startup._command_is_runnable('"C:\\gone\\Medication Organizer.exe"') is False
    assert startup._command_is_runnable("") is False


# --------------------------------------------------------------------------- #
# Nothing user-visible is raw technical text
# --------------------------------------------------------------------------- #
def test_a_windows_error_is_never_shown_to_a_spanish_reader():
    """`str(exc)` on Windows is localised to the *operating system's* language
    and contains a file path. It goes to the log, not to the status card."""
    from app.notifications.dispatcher import failure_key
    from app.services.errors import AppError

    disk_full = OSError(28, "No space left on device")
    disk_full.errno = 28
    missing = FileNotFoundError(2, "The system cannot find the path specified")
    missing.errno = 2

    assert failure_key(disk_full) == "error.disk_full"
    assert failure_key(missing) == "error.backup_location_unreachable"
    assert failure_key(AppError("error.backup_failed")) == "error.backup_failed"
    assert failure_key(RuntimeError("something odd")) == "error.backup_failed"

    import json

    root = Path(__file__).resolve().parent.parent
    for language in ("en", "es"):
        catalog = json.loads((root / "app" / "i18n" / f"{language}.json").read_text("utf-8"))
        for key in ("disk_full", "backup_location_unreachable", "backup_failed", "cross_site"):
            assert key in catalog["error"], f"{key} missing from {language}.json"


def test_the_interface_translates_a_recorded_reason():
    ui = (
        Path(__file__).resolve().parent.parent
        / "app" / "static" / "js" / "settings_v4.js"
    ).read_text(encoding="utf-8")
    assert "function reason(" in ui
    assert "reason(item.last_error)" in ui


def test_the_lock_screen_will_not_send_the_user_to_another_site():
    lock_js = (
        Path(__file__).resolve().parent.parent / "app" / "static" / "js" / "lock.js"
    ).read_text(encoding="utf-8")
    # `startsWith('/')` accepts "//evil.example", which is a link off-site.
    assert "startsWith('/')" not in lock_js
    assert "new URL(raw, window.location.origin)" in lock_js
    assert "url.origin !== window.location.origin" in lock_js


def test_the_pollers_identify_themselves_and_react_to_a_lock():
    root = Path(__file__).resolve().parent.parent / "app" / "static" / "js"
    api = (root / "api.js").read_text(encoding="utf-8")
    assert "X-Requested-With" in api
    assert "X-Medtracker-Poll" in api
    assert "status === 423" in api and "goToLock" in api

    for name in ("today.js", "notifications.js", "ui.js"):
        text = (root / name).read_text(encoding="utf-8")
        assert "poll: true" in text, f"{name} polls without saying so"


# --------------------------------------------------------------------------- #
# Packaging
# --------------------------------------------------------------------------- #
def test_the_build_does_not_ship_anybody_photographs():
    spec = (Path(__file__).resolve().parent.parent / "medtracker.spec").read_text("utf-8")
    assert "_without_uploads" in spec

    namespace: dict = {}
    exec(compile(spec.split("a = Analysis(")[0], "medtracker.spec", "exec"),
         {"SPECPATH": str(Path(__file__).resolve().parent.parent)}, namespace)
    keep = namespace["_without_uploads"]([
        ("app/static/css/app.css", "/x/app.css", "DATA"),
        ("app/static/uploads/private.png", "/x/private.png", "DATA"),
        ("app\\static\\uploads\\private2.png", "/x/private2.png", "DATA"),
    ])
    assert [entry[0] for entry in keep] == ["app/static/css/app.css"]


def test_the_packaged_application_does_not_publish_an_api_console():
    """`/docs` is a full interactive UI for every write endpoint."""
    import app.main as main

    source = Path(main.__file__).read_text(encoding="utf-8")
    assert "docs_url" in source and "FROZEN" in source


# --------------------------------------------------------------------------- #
# Round two: regressions for the defects the fixes themselves introduced
# --------------------------------------------------------------------------- #
def test_a_slow_lock_read_cannot_switch_the_lock_off_for_good():
    """The read runs on a worker thread. One that started before the PIN was
    created used to finish after it and write "no lock configured" over the
    truth — and because that answer short-circuits the database entirely,
    nothing ever read it again. The application served everything, for ever,
    with the lock switched on."""
    from app.routes import lock_cache

    lock_cache.invalidate()
    stale = lock_cache.epoch()                 # a read begins...
    lock_cache.invalidate()                    # ...the PIN is created meanwhile
    assert lock_cache.remember(False, stale) is False
    assert lock_cache.known() is None           # so the next request re-reads

    fresh = lock_cache.epoch()
    assert lock_cache.remember(True, fresh) is True
    assert lock_cache.known() is True
    lock_cache.invalidate()


def test_an_overtaken_read_refuses_the_request_it_was_answering(client, monkeypatch):
    from app.routes import lock, lock_cache

    enable_lock(client)
    real = lock._read_lock_state

    def read_then_get_overtaken(token):
        result = real(token)
        lock_cache.invalidate()                # something changed under us
        return result

    monkeypatch.setattr(lock, "_read_lock_state", read_then_get_overtaken)
    # Unlocked, and the read says so — but it was overtaken, so it is not
    # trusted in the permissive direction.
    assert client.get("/api/today").status_code == 423


def test_the_tray_lock_is_seen_by_the_web_server(client):
    """The tray calls into the same process; the middleware has to notice."""
    from app.desktop.__main__ import _lock_now

    enable_lock(client)
    assert client.get("/api/today").status_code == 200
    _lock_now()
    assert client.get("/api/today").status_code == 423


def test_adopting_a_database_that_is_being_written_does_not_lose_it(tmp_path, monkeypatch):
    """`shutil.copy2` of the .db and the -wal is three copies at three instants:
    a checkpoint in between produces a file that passes `integrity_check` and
    has lost everything."""
    from app.utils.datamove import _copy_database

    source = tmp_path / "source.db"
    connection = sqlite3.connect(str(source))
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("CREATE TABLE medications (id INTEGER PRIMARY KEY, name TEXT)")
    connection.execute("INSERT INTO medications VALUES (1, 'Amoxicillin')")
    connection.commit()
    try:
        # Still open, still in WAL mode — exactly the state a running instance
        # leaves the file in.
        destination = tmp_path / "copy.db"
        _copy_database(source, destination)
    finally:
        connection.close()

    copied = sqlite3.connect(str(destination))
    try:
        assert copied.execute("SELECT name FROM medications").fetchall() == [("Amoxicillin",)]
        assert copied.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        copied.close()
    # And no side files were dragged along.
    assert not (tmp_path / "copy.db-wal").exists()
    assert not (tmp_path / "copy.db-shm").exists()


def test_a_database_that_copies_to_rubbish_is_not_adopted(tmp_path, monkeypatch):
    from app.utils import datamove

    source = tmp_path / "old" / "data"
    source.mkdir(parents=True)
    connection = sqlite3.connect(str(source / "medtracker.db"))
    connection.execute("CREATE TABLE something_else (id INTEGER)")   # not ours
    connection.commit()
    connection.close()

    exe = tmp_path / "old" / "dist" / "app.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"")
    new = tmp_path / "new"

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe))
    monkeypatch.setattr("app.config.DATA_DIR", new)
    monkeypatch.setattr("app.config.DB_PATH", new / "medtracker.db")

    assert datamove.adopt_existing_database() is None
    # Nothing half-copied is left to be mistaken for the real thing next time.
    assert not (new / "medtracker.db").exists()


def test_the_old_photograph_folder_is_emptied_not_just_copied(monkeypatch, tmp_path):
    """A copy left in `app/static/uploads` stays reachable without the PIN."""
    from app.utils import datamove

    legacy = tmp_path / "static" / "uploads"
    legacy.mkdir(parents=True)
    (legacy / "pill.png").write_bytes(b"a photograph")
    destination = tmp_path / "data" / "uploads"

    monkeypatch.setattr(datamove, "legacy_upload_dir", lambda: legacy)
    monkeypatch.setattr("app.config.UPLOAD_DIR", destination)
    monkeypatch.setattr("app.config._default_data_dir", lambda: destination.parent)
    monkeypatch.setattr("app.config.DATA_DIR", destination.parent)

    datamove.migrate_legacy_uploads()

    assert (destination / "pill.png").read_bytes() == b"a photograph"
    assert not (legacy / "pill.png").exists()


def test_an_unusable_backup_folder_is_reported_as_such_not_as_a_form_error():
    """`create_backup` raises a ValidationError, whose own message key is the
    generic "review the highlighted fields" — useless on a status card."""
    from app.notifications.dispatcher import failure_key
    from app.services import backup as backup_service
    from app.services.errors import ValidationError

    missing = FileNotFoundError(2, "No such file or directory")
    assert backup_service.reason_key(missing) == "error.backup_location_unreachable"

    refused = PermissionError(13, "Permission denied")
    assert backup_service.reason_key(refused) == "error.backup_location_unwritable"

    full = OSError(28, "No space left on device")
    assert backup_service.reason_key(full) == "error.disk_full"
    assert backup_service.reason_key(sqlite3.OperationalError("database or disk is full")) == (
        "error.disk_full"
    )

    tagged = backup_service._tagged(
        ValidationError({"backup_location": "validation.backup_location_unusable"}),
        "error.backup_location_unreachable",
    )
    assert failure_key(tagged) == "error.backup_location_unreachable"
    assert failure_key(ValidationError({})) == "error.validation"


def test_an_unplugged_backup_drive_says_so_in_the_users_language(db, tmp_path, monkeypatch):
    """End to end: the tick, the settings row and the status card."""
    from datetime import time as _time

    from app.services import backup as backup_service, system_status

    settings = get_settings(db)
    settings.backup_enabled = True
    settings.backup_location = "/definitely/not/here/E-drive"
    settings.backup_time = _time(0, 0)
    settings.last_backup_at = None
    db.flush()

    monkeypatch.setattr(
        backup_service.Path, "mkdir",
        lambda *_a, **_k: (_ for _ in ()).throw(FileNotFoundError(2, "no such path")),
    )

    from app.notifications import dispatcher

    dispatcher.run_tick(db, send_windows=False, send_email=False)
    db.refresh(settings)

    assert settings.last_backup_error == "error.backup_location_unreachable"
    assert system_status._backup(settings)["level"] == "error"


def test_the_startup_wait_is_not_spent_copying_a_database(monkeypatch, tmp_path):
    """The adoption used to run inside the server's lifespan, so a first run
    with a large database looked exactly like a server that never came up."""
    from app.desktop import launcher

    monkeypatch.setenv("MEDTRACKER_DATA_DIR", str(tmp_path / "data"))
    calls = []
    monkeypatch.setattr(
        "app.utils.datamove.adopt_existing_database",
        lambda: calls.append("before the server") or None,
    )
    monkeypatch.setattr(launcher.ServerHandle, "bind",
                        lambda self: calls.append("bind"))
    monkeypatch.setattr(launcher.ServerHandle, "start",
                        lambda self: calls.append("start") or True)
    monkeypatch.setattr(launcher, "wait_until_healthy", lambda *_a, **_k: None)

    launcher.start_application("127.0.0.1", 0, open_ui=False)
    assert calls[:1] == ["before the server"]


def test_the_origin_check_reads_a_header_the_way_a_browser_writes_one():
    from app.routes.origin import _hostname, is_acceptable_host

    # The trap: a value that ends in a name we trust but is addressed elsewhere.
    assert _hostname("http://127.0.0.1:8000@evil.example") == ""
    assert is_acceptable_host("127.0.0.1:8000@evil.example") is False
    # An IPv6 literal is not a host:port pair.
    assert _hostname("[::1]:8000") == "[::1]"
    assert is_acceptable_host("[::1]:8000") is True
    # And a request with no Host at all is not "probably fine".
    assert is_acceptable_host("") is False
    assert is_acceptable_host("127.0.0.1:8000") is True
    assert is_acceptable_host("192.168.1.9:8000") is True
    assert is_acceptable_host("rebind.evil.example") is False


def test_a_page_asked_for_by_the_wrong_name_gets_a_page_not_json(client):
    response = client.get(
        "/", headers={"Host": "some-alias", "Accept": "text/html,*/*"},
    )
    assert response.status_code == 403
    assert response.headers["content-type"].startswith("text/html")
    assert "<html" in response.text and "127.0.0.1" in response.text
    assert "error.cross_site" not in response.text        # translated, not a key


def test_a_phone_on_the_lan_can_still_use_the_application(client):
    """With MEDTRACKER_HOST=0.0.0.0 the browser sends the PC's address, which is
    neither loopback nor the bound address."""
    lan = {"Host": "192.168.1.9:8000", "Origin": "http://192.168.1.9:8000"}
    from tests.test_medications import make_payload

    assert client.post("/api/medications", json=make_payload(), headers=lan).status_code == 201


def test_the_deep_link_keeps_its_query(client):
    enable_lock(client)
    client.post("/api/lock/lock")
    response = client.get("/calendar?view=week&anchor=2026-08-01", follow_redirects=False)
    assert response.status_code == 303
    assert "view%3Dweek" in response.headers["location"] or \
           "view=week" in response.headers["location"]


def test_an_empty_data_directory_variable_does_not_mean_here(monkeypatch, tmp_path):
    import importlib

    monkeypatch.setenv("MEDTRACKER_DATA_DIR", "   ")
    from app import config

    reloaded = importlib.reload(config)
    try:
        assert reloaded.DATA_DIR != Path(".")
        assert reloaded.DATA_DIR == reloaded.PROJECT_ROOT / "data"
    finally:
        monkeypatch.undo()
        importlib.reload(config)


def test_the_activity_listeners_do_not_depend_on_a_stale_snapshot():
    """They used to be attached only if the page had loaded *after* the lock was
    switched on — which is never true on the page you switch it on from."""
    ui = (
        Path(__file__).resolve().parent.parent / "app" / "static" / "js" / "ui.js"
    ).read_text(encoding="utf-8")
    body = ui[ui.index("function setupActivity"):ui.index("function setupActivity") + 900]
    assert "app_lock_enabled" not in body
    assert "mousemove" in body


def test_the_lock_screen_does_not_reload_itself_out_of_its_own_error():
    """Everything the lock screen asks for that is not on the allow-list gets a
    423, and a 423 sends the browser to the lock screen. On the lock screen that
    is a reload, which wipes whatever it was showing — including "Incorrect
    PIN", which is the one message it exists to show."""
    root = Path(__file__).resolve().parent.parent / "app" / "static" / "js"

    api = (root / "api.js").read_text(encoding="utf-8")
    assert "window.location.pathname === '/lock'" in api

    ui = (root / "ui.js").read_text(encoding="utf-8")
    body = ui[ui.index("function setupActivity"):]
    assert "classList.contains('locked')" in body[:600]


# --------------------------------------------------------------------------- #
# Round three: the windowed build has no console
# --------------------------------------------------------------------------- #
def test_a_program_with_no_console_can_still_configure_its_logging():
    """What Edwin actually saw on the first `.exe`:

        Servidor web: Falló (Unable to configure formatter 'default')

    A PyInstaller build with `console=False` starts with `sys.stdout` set to
    None — not a closed file, None — and uvicorn's log formatter asks
    `sys.stdout.isatty()` to decide about colour. `dictConfig` catches the
    AttributeError and re-raises it as that ValueError, and the user gets a
    message box instead of their medication list.
    """
    import logging.config

    import uvicorn.config

    from app.utils.streams import ensure_streams

    real_out, real_err = sys.stdout, sys.stderr
    try:
        sys.stdout = None
        sys.stderr = None

        # Without the fix, this is the failure, exactly.
        with pytest.raises(ValueError, match="formatter"):
            logging.config.dictConfig(uvicorn.config.LOGGING_CONFIG)

        assert set(ensure_streams()) >= {"stdout", "stderr"}
        assert sys.stdout.isatty() is False
        logging.config.dictConfig(uvicorn.config.LOGGING_CONFIG)   # now fine
    finally:
        sys.stdout, sys.stderr = real_out, real_err
        logging.config.dictConfig(
            {"version": 1, "disable_existing_loggers": False}
        )


def test_the_sink_survives_what_a_console_would_be_asked_to_do():
    from app.utils.streams import _Sink

    sink = _Sink()
    assert sink.writable() is True
    assert sink.write("anything at all") == len("anything at all")
    assert sink.isatty() is False
    sink.flush()
    print("via print", file=sink)          # the common accidental caller


def test_the_desktop_entry_point_fixes_the_streams_before_importing_the_app():
    """Order matters: `app.main` configures logging at import, so the streams
    have to exist before the import statement, not after it."""
    source = (Path(__file__).resolve().parent.parent / "desktop.py").read_text("utf-8")
    assert source.index("_ensure_streams()") < source.index("from app.desktop.__main__ import main")


def test_the_server_does_not_reconfigure_the_applications_logging():
    """`log_config=None`: uvicorn replacing our logging setup was how a library
    got to decide where the application's own log records went."""
    launcher_source = (
        Path(__file__).resolve().parent.parent / "app" / "desktop" / "launcher.py"
    ).read_text(encoding="utf-8")
    assert "log_config=None" in launcher_source
    assert "ensure_streams()" in launcher_source


def test_logging_without_a_console_installs_something_rather_than_nothing(monkeypatch):
    from app import main as main_module

    monkeypatch.setattr("sys.stderr", None)
    handlers = main_module._log_handlers()
    assert handlers                                  # never an empty list
    assert not any(
        type(handler) is __import__("logging").StreamHandler for handler in handlers
    )
    for handler in handlers:
        handler.close()


def test_running_the_tests_cannot_move_the_users_real_photographs(monkeypatch, tmp_path):
    """The move deletes the source once it has copied, and the test-suite points
    `MEDTRACKER_DATA_DIR` at a throwaway folder. Together that is a way to lose
    a user's photographs by *checking that the software works* — the suite runs
    the real start sequence, and `scripts\\build_windows.bat` runs the suite
    inside the user's own installation before every build."""
    from app.utils import datamove

    legacy = tmp_path / "static" / "uploads"
    legacy.mkdir(parents=True)
    (legacy / "pill.png").write_bytes(b"a real photograph")

    scratch = tmp_path / "throwaway" / "uploads"
    monkeypatch.setattr(datamove, "legacy_upload_dir", lambda: legacy)
    monkeypatch.setattr("app.config.UPLOAD_DIR", scratch)
    monkeypatch.setattr("app.config.DATA_DIR", scratch.parent)
    monkeypatch.setattr(
        "app.config._default_data_dir", lambda: tmp_path / "the-real-one"
    )

    assert datamove.migrate_legacy_uploads() == 0
    assert (legacy / "pill.png").read_bytes() == b"a real photograph"
    assert not scratch.exists()


def test_the_computer_and_the_phone_can_both_be_unlocked(client):
    """One token meant the second browser to enter the PIN silently threw the
    first one out — so a person using their computer and their phone on the same
    network would push each other back to the lock screen, for ever."""
    from app.services import applock

    enable_lock(client)                       # the computer sets it up

    phone = another_browser()
    assert phone.get("/api/today").status_code == 423
    assert phone.post("/api/lock/unlock", json={"pin": "1234"}).status_code == 200
    assert phone.get("/api/today").status_code == 200

    # ...and the computer is still inside, which is the whole point.
    assert client.get("/api/today").status_code == 200

    # Locking clears every one of them at once, which is the property that
    # actually has to hold.
    assert client.post("/api/lock/lock").status_code == 200
    assert phone.get("/api/today").status_code == 423
    assert client.get("/api/today").status_code == 423
    assert applock.current_token() is None


def test_the_number_of_unlocked_browsers_is_bounded(db):
    """Nothing in memory may grow without limit just because someone keeps
    entering their PIN."""
    from app.services import applock

    applock.reset_for_tests()
    settings = get_settings(db)
    applock.enable(db, settings, "1234", "1234")

    tokens = [applock.unlock() for _ in range(applock.MAX_UNLOCKED_CLIENTS + 5)]
    assert len(applock._session.tokens) == applock.MAX_UNLOCKED_CLIENTS
    assert applock.is_locked(settings, token=tokens[-1]) is False
    assert applock.is_locked(settings, token=tokens[0]) is True     # aged out
    applock.reset_for_tests()


def test_no_test_pretends_to_be_frozen_without_saying_where_from():
    """A guard against the way this suite lied to itself.

    `sys.frozen = True` makes the application look for a data folder near
    `sys.executable`. A test that sets the first and not the second reads
    whatever happens to sit near the interpreter — nothing on a build machine,
    and the developer's own installation when the virtual environment lives
    inside the project. That is a test that passes everywhere except on the one
    computer the software is for.
    """
    import re

    root = Path(__file__).resolve().parent
    offenders = []
    for path in sorted(root.glob("test_*.py")):
        text = path.read_text(encoding="utf-8")
        for block in re.split(r"\ndef test_|\ndef [a-z_]+\(", text):
            if '"frozen", True' in block and '"executable"' not in block:
                name = block.split("(")[0].strip()
                offenders.append(f"{path.name}::{name}")
    assert not offenders, f"frozen without an executable: {offenders}"


def test_starting_with_windows_does_not_open_a_console_every_time(monkeypatch, tmp_path):
    """The promise is that reminders work without anything being in the way.
    Registering `python.exe` opens a black console window at every logon, and
    most people close it — taking the reminders with it."""
    import sys as _sys

    from app.desktop import startup

    scripts = tmp_path / "Scripts"
    scripts.mkdir()
    (scripts / "python.exe").write_bytes(b"")
    (scripts / "pythonw.exe").write_bytes(b"")

    monkeypatch.setattr(_sys, "executable", str(scripts / "python.exe"))
    monkeypatch.setattr(_sys, "frozen", False, raising=False)

    command = startup.launch_command()
    assert "pythonw.exe" in command
    assert "\\python.exe" not in command and "/python.exe" not in command
    assert startup.BACKGROUND_FLAG in command
    # Quoted, because the path has a space in it on every real installation.
    assert command.startswith('"')


def test_the_frozen_application_registers_itself(monkeypatch, tmp_path):
    import sys as _sys

    from app.desktop import startup

    exe = tmp_path / "Medication Organizer" / "Medication Organizer.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"")
    monkeypatch.setattr(_sys, "frozen", True, raising=False)
    monkeypatch.setattr(_sys, "executable", str(exe))

    command = startup.launch_command()
    assert str(exe) in command
    assert "desktop.py" not in command, "the frozen build must not need the source"
    assert command.startswith('"') and startup.BACKGROUND_FLAG in command


def test_a_python_with_no_windowless_twin_is_used_as_it_is(monkeypatch, tmp_path):
    import sys as _sys

    from app.desktop import startup

    lonely = tmp_path / "bin" / "python.exe"
    lonely.parent.mkdir(parents=True)
    lonely.write_bytes(b"")
    monkeypatch.setattr(_sys, "executable", str(lonely))
    monkeypatch.setattr(_sys, "frozen", False, raising=False)

    assert "python.exe" in startup.launch_command()


def test_no_test_asserts_against_the_live_interpreter_path():
    """The pattern that has now blocked a build twice.

    A test that checks the real `sys.executable` appears in something the
    application produced is really asserting a fact about the machine it runs
    on. Both times it passed on Linux and failed on Windows — which is the
    worst possible direction, because Windows is the only place this software
    is used, and the failure lands in the middle of a build.

    Pin a fake interpreter with `monkeypatch` and assert on that instead.
    """
    import re

    root = Path(__file__).resolve().parent
    offenders = []
    for path in sorted(root.glob("test_*.py")):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            stripped = line.strip()
            if not stripped.startswith("assert "):
                continue
            if re.search(r"\bsys\.executable\b", stripped) and "monkeypatch" not in stripped:
                offenders.append(f"{path.name}:{number}: {stripped[:70]}")
    assert not offenders, "assertions that depend on this machine:\n" + "\n".join(offenders)
