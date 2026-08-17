"""Two devices, one database.

The computer and a phone on the same Wi-Fi are two browsers looking at the same
rows. This file covers the two things that makes necessary: deciding whether to
answer the network at all, and telling both screens when the other one wrote
something.
"""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

import pytest

from app.services.settings_service import get_settings


# --------------------------------------------------------------------------- #
# Which address the application listens on
# --------------------------------------------------------------------------- #
def make_settings_db(path: Path, network_access: int | None) -> None:
    connection = sqlite3.connect(str(path))
    columns = "id INTEGER PRIMARY KEY"
    if network_access is not None:
        columns += ", network_access BOOLEAN NOT NULL DEFAULT 0"
    connection.execute(f"CREATE TABLE settings ({columns})")
    if network_access is None:
        connection.execute("INSERT INTO settings (id) VALUES (1)")
    else:
        connection.execute(
            "INSERT INTO settings (id, network_access) VALUES (1, ?)", (network_access,)
        )
    connection.commit()
    connection.close()


def test_the_setting_decides_the_address(monkeypatch, tmp_path):
    from app.desktop import network

    path = tmp_path / "medtracker.db"
    monkeypatch.setattr("app.config.DB_PATH", path)

    make_settings_db(path, 0)
    assert network.network_access_enabled() is False
    assert network.host_to_bind() == "127.0.0.1"

    path.unlink()
    make_settings_db(path, 1)
    assert network.network_access_enabled() is True
    assert network.host_to_bind() == "0.0.0.0"


def test_an_explicit_address_wins_over_the_setting(monkeypatch, tmp_path):
    """A shortcut or a script has to be able to override the stored preference
    in either direction, without editing the database."""
    from app.desktop import network

    path = tmp_path / "medtracker.db"
    monkeypatch.setattr("app.config.DB_PATH", path)
    make_settings_db(path, 1)

    assert network.host_to_bind("127.0.0.1") == "127.0.0.1"
    assert network.host_to_bind("0.0.0.0") == "0.0.0.0"


@pytest.mark.parametrize(
    "prepare",
    [
        pytest.param(lambda path: None, id="no database at all"),
        pytest.param(lambda path: make_settings_db(path, None), id="not migrated yet"),
        pytest.param(lambda path: path.write_bytes(b"not a database"), id="unreadable"),
    ],
)
def test_anything_it_cannot_read_means_local_only(monkeypatch, tmp_path, prepare):
    """There is no login here. A database that cannot be read must never be a
    reason to start answering the whole network."""
    from app.desktop import network

    path = tmp_path / "medtracker.db"
    monkeypatch.setattr("app.config.DB_PATH", path)
    prepare(path)

    assert network.network_access_enabled() is False
    assert network.host_to_bind() == "127.0.0.1"


def test_upgrading_does_not_open_the_application_to_the_network(tmp_path):
    """Same rule as the Windows startup entry: an update is not consent."""
    from app.database.migrations import _migrate_6_to_7

    path = tmp_path / "v6.db"
    connection = sqlite3.connect(str(path))
    connection.execute("CREATE TABLE settings (id INTEGER PRIMARY KEY)")
    connection.execute("INSERT INTO settings (id) VALUES (1)")
    connection.commit()

    _migrate_6_to_7(connection)
    _migrate_6_to_7(connection)          # idempotent

    assert connection.execute("SELECT network_access FROM settings").fetchone() == (0,)
    connection.close()


def test_the_switch_round_trips_through_the_api(client):
    before = client.get("/api/settings").json()
    assert before["network_access"] is False

    assert client.put("/api/settings", json={"network_access": True}).status_code == 200
    assert client.get("/api/settings").json()["network_access"] is True

    assert client.put("/api/settings", json={"network_access": False}).status_code == 200
    assert client.get("/api/settings").json()["network_access"] is False


def test_the_status_page_says_where_to_point_the_phone(db, monkeypatch):
    from app.services import system_status

    settings = get_settings(db)
    settings.network_access = True
    db.flush()

    monkeypatch.setattr(system_status, "_bound_host", "0.0.0.0")
    monkeypatch.setattr(
        "app.desktop.network.local_addresses", lambda: ["192.168.1.9"]
    )

    row = system_status._network(settings)
    assert row["level"] == "ok"
    assert row["detail_key"] == "status.network_open"
    assert row["addresses"] == ["http://192.168.1.9:8000"]
    assert row["restart_required"] is False


def test_the_status_page_says_when_a_restart_is_needed(db, monkeypatch):
    """The port is bound before the setting can be read, so a change only takes
    effect on the next start — and saying nothing about that would leave the
    user wondering why the phone still cannot connect."""
    from app.services import system_status

    settings = get_settings(db)
    settings.network_access = True
    db.flush()
    monkeypatch.setattr(system_status, "_bound_host", "127.0.0.1")

    row = system_status._network(settings)
    assert row["level"] == "warning"
    assert row["detail_key"] == "status.network_restart_required"
    assert row["restart_required"] is True
    assert row["addresses"] == []          # not reachable yet; do not pretend


def test_a_local_only_application_reports_itself_as_switched_off(db, monkeypatch):
    from app.services import system_status

    monkeypatch.setattr(system_status, "_bound_host", "127.0.0.1")
    row = system_status._network(get_settings(db))
    assert row["level"] == "disabled"
    assert row["detail_key"] == "status.network_local_only"


# --------------------------------------------------------------------------- #
# Telling the other screen
# --------------------------------------------------------------------------- #
def test_a_write_moves_the_revision(client):
    from app.services import live
    from tests.test_medications import make_payload

    before = live.revision()
    assert client.get("/api/today").status_code == 200
    assert live.revision() == before, "a read is not a change"

    assert client.post("/api/medications", json=make_payload()).status_code == 201
    after_create = live.revision()
    assert after_create > before

    assert client.put("/api/settings", json={"ending_soon_days": 5}).status_code == 200
    assert live.revision() > after_create


def test_the_pollers_do_not_move_the_revision(client):
    """They fire every thirty seconds, on every open device, for ever. If they
    counted as changes, every screen would reload itself all day."""
    from app.services import live

    client.post("/api/lock/activity")
    client.post("/api/notifications/delivered", json={"ids": []})
    before = live.revision()

    client.post("/api/lock/activity")
    client.post("/api/notifications/delivered", json={"ids": []})
    assert live.revision() == before


def test_a_refused_write_is_not_a_change(client):
    from app.services import live

    before = live.revision()
    assert client.post("/api/medications", json={"name": ""}).status_code == 422
    assert live.revision() == before


def test_a_quiet_scheduler_pass_does_not_wake_every_screen(db):
    """A tick that found nothing to do must not make both devices redraw.

    Backups are switched off for this one: an automatic backup is a real change
    and does deserve to move the revision, so leaving it on would make this test
    about the backup schedule rather than about a quiet tick."""
    from app.notifications import dispatcher
    from app.services import live

    settings = get_settings(db)
    settings.backup_enabled = False
    db.flush()

    before = live.revision()
    summary = dispatcher.run_tick(db, send_windows=False, send_email=False)
    assert not any(
        summary.get(key)
        for key in (
            "completed_medications", "extended_schedules", "missed_doses",
            "dose_notifications", "snooze_notifications",
            "appointment_notifications", "backup",
        )
    ), summary
    assert live.revision() == before


def test_a_scheduler_pass_that_did_something_does(db):
    from datetime import timedelta

    from app.notifications import dispatcher
    from app.services import live, medications as medication_service
    from app.utils.timeutil import now_local
    from tests.test_medications import make_payload, register_before_start

    today = now_local().date()
    medication = medication_service.create_medication(
        db,
        make_payload(
            start_date=today.isoformat(), end_date=today.isoformat(),
            frequency_hours=24,
            first_dose_time=(now_local() - timedelta(minutes=1)).strftime("%H:%M"),
        ),
    )
    register_before_start(db, medication)
    db.commit()

    before = live.revision()
    summary = dispatcher.run_tick(db, send_windows=False, send_email=False)
    assert summary["dose_notifications"] >= 1
    assert live.revision() > before


def test_the_stream_is_not_reachable_while_locked(client):
    from tests.test_v4_review import enable_lock

    enable_lock(client)
    client.post("/api/lock/lock")
    assert client.get("/api/events").status_code == 423


@pytest.fixture(scope="module")
def live_server():
    """A real server on a real port.

    The change stream is a long-lived response, and reading one through
    `TestClient` deadlocks its portal: the test thread blocks on the next line
    while the request that would produce that line is waiting to be scheduled on
    the same loop. Nothing about that is a property of the application, so the
    stream is exercised the way a browser exercises it — over a socket.
    """
    import socket
    import tempfile

    data_dir = tempfile.mkdtemp(prefix="medtracker-live-")
    old = os.environ.get("MEDTRACKER_DATA_DIR")
    os.environ["MEDTRACKER_DATA_DIR"] = data_dir

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]

    from app.desktop import launcher

    report, handle = launcher.start_application("127.0.0.1", port, open_ui=False)
    assert report.ok, report.as_text()
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        if handle is not None:
            handle.stop()
        if old is None:
            os.environ.pop("MEDTRACKER_DATA_DIR", None)
        else:
            os.environ["MEDTRACKER_DATA_DIR"] = old


def browser():
    """One client with one cookie jar, the way a browser behaves.

    The unlock is proved by a cookie, so a helper that forgot its cookies would
    be a different device on every call — and would find the application locked
    even immediately after entering the PIN.
    """
    import http.cookiejar
    import urllib.request

    return urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
    )


def open_stream(opener, base: str, timeout: float = 15.0):
    import urllib.request

    request = urllib.request.Request(
        f"{base}/api/events", headers={"Accept": "text/event-stream"}
    )
    return opener.open(request, timeout=timeout)


def post(opener, base: str, path: str, body: dict) -> int:
    import urllib.error
    import urllib.request

    request = urllib.request.Request(
        f"{base}{path}",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", "X-Requested-With": "MedTracker"},
        method="POST",
    )
    try:
        with opener.open(request, timeout=10) as response:
            return response.status
    except urllib.error.HTTPError as exc:
        return exc.code


def read_event(stream, tries: int = 40):
    """The next `event:` line and its data, or (None, None)."""
    for _ in range(tries):
        line = stream.readline().decode("utf-8").strip()
        if line.startswith("event: "):
            data = stream.readline().decode("utf-8").strip()
            payload = json.loads(data.removeprefix("data: ")) if data.startswith("data:") else {}
            return line.removeprefix("event: "), payload
    return None, None


def test_the_stream_says_hello_and_then_reports_a_change_from_elsewhere(live_server):
    """The whole point: the phone writes, and the computer's screen hears about
    it without anybody pressing refresh."""
    from tests.test_medications import make_payload

    computer = browser()
    phone = browser()

    stream = open_stream(computer, live_server)
    try:
        name, payload = read_event(stream)
        assert name == "hello"
        first = payload["revision"]

        # ...the other device adds something.
        assert post(
            phone, live_server, "/api/medications", make_payload(name="From the phone")
        ) == 201

        name, payload = read_event(stream)
        assert name == "changed", "the stream never reported the change"
        assert payload["revision"] > first
    finally:
        stream.close()


def test_the_stream_reports_only_once_for_a_burst(live_server):
    from tests.test_medications import make_payload

    computer, phone = browser(), browser()
    stream = open_stream(computer, live_server)
    try:
        assert read_event(stream)[0] == "hello"
        for index in range(3):
            assert post(
                phone, live_server, "/api/medications", make_payload(name=f"Burst {index}")
            ) == 201

        name, payload = read_event(stream)
        assert name == "changed"
        # One notification carrying the latest revision, not one per write: the
        # screen reloads everything anyway.
        assert payload["revision"] >= 3
    finally:
        stream.close()


def test_a_locked_application_says_nothing_on_a_stream_that_was_already_open(live_server):
    """The stream outlives the lock, so being locked has to be re-checked inside
    it and not only at the door."""
    from tests.test_medications import make_payload

    computer = browser()
    assert post(
        computer, live_server, "/api/lock/enable", {"pin": "4821", "confirm_pin": "4821"}
    ) == 200

    stream = open_stream(computer, live_server)
    try:
        assert read_event(stream)[0] == "hello"
        assert post(computer, live_server, "/api/lock/lock", {}) == 200
        post(computer, live_server, "/api/medications", make_payload(name="while locked"))

        name, _payload = read_event(stream)
        assert name == "locked"
    finally:
        stream.close()
        assert post(computer, live_server, "/api/lock/unlock", {"pin": "4821"}) == 200
        assert post(computer, live_server, "/api/lock/disable", {"current_pin": "4821"}) == 200


# --------------------------------------------------------------------------- #
# The interface
# --------------------------------------------------------------------------- #
def test_the_page_will_not_reload_under_somebody_who_is_typing():
    live_js = (
        Path(__file__).resolve().parent.parent / "app" / "static" / "js" / "live.js"
    ).read_text(encoding="utf-8")
    assert "dialog[open]" in live_js
    assert "'INPUT', 'TEXTAREA', 'SELECT'" in live_js
    assert "isContentEditable" in live_js
    # A stream that cannot be held open must fail quietly; the page keeps its
    # own timer.
    assert "EventSource" in live_js and "onerror" in live_js


def test_the_settings_screen_opts_out_of_live_reloading():
    """It is almost entirely a form: reloading it would replace half-typed
    settings with the saved ones."""
    settings_js = (
        Path(__file__).resolve().parent.parent / "app" / "static" / "js" / "settings.js"
    ).read_text(encoding="utf-8")
    assert "{ live: false }" in settings_js


def test_every_switch_on_the_settings_page_saves_and_loads():
    """There used to be two hand-written copies of the list of switches — one
    to fill the form, one to read it back. A switch added to only one of them
    loads correctly and never saves, which reads as a broken save button."""
    import re

    root = Path(__file__).resolve().parent.parent
    settings_js = (root / "app" / "static" / "js" / "settings.js").read_text("utf-8")
    template = (root / "app" / "templates" / "settings.html").read_text("utf-8")

    assert settings_js.count("const SWITCHES") == 1
    listed = set(re.findall(r"'([a-z_0-9]+)'", settings_js.split("const SWITCHES = [")[1].split("];")[0]))

    in_form = set(re.findall(r'<input type="checkbox" name="([a-z_0-9]+)"', template))
    assert in_form, "no checkboxes found in the template"
    assert in_form <= listed, f"never saved: {sorted(in_form - listed)}"

    from app.services.settings_service import BOOLEAN_SETTINGS

    assert in_form <= set(BOOLEAN_SETTINGS), (
        f"the backend ignores: {sorted(in_form - set(BOOLEAN_SETTINGS))}"
    )


# --------------------------------------------------------------------------- #
# One database, whichever way it is started
# --------------------------------------------------------------------------- #
def frozen_at(monkeypatch, exe: Path, appdata: Path):
    import sys as _sys

    exe.parent.mkdir(parents=True, exist_ok=True)
    exe.write_bytes(b"")
    monkeypatch.setattr(_sys, "frozen", True, raising=False)
    monkeypatch.setattr(_sys, "executable", str(exe))
    monkeypatch.setenv("LOCALAPPDATA", str(appdata))
    monkeypatch.delenv("MEDTRACKER_DATA_DIR", raising=False)


def reload_config():
    import importlib

    from app import config

    return importlib.reload(config)


def test_the_exe_uses_the_same_database_as_start_bat(monkeypatch, tmp_path):
    """The trap this closes: the packaged application kept its own copy, so a
    setting changed with `start.bat` — or a dose marked there — was simply not
    there when the `.exe` was opened, and the two drifted apart silently."""
    import importlib

    from app import config

    project = tmp_path / "ProyectoPersonal"
    data = project / "data"
    data.mkdir(parents=True)
    (data / "medtracker.db").write_bytes(b"the one database")

    frozen_at(monkeypatch, project / "dist" / "Medication Organizer" / "app.exe",
              tmp_path / "AppData")
    try:
        reloaded = reload_config()
        assert reloaded.DATA_DIR == data
        assert reloaded.DB_PATH == data / "medtracker.db"
        # ...and it wrote down where it went.
        assert reloaded.DATA_POINTER.read_text(encoding="utf-8").strip() == str(data)
    finally:
        monkeypatch.undo()
        importlib.reload(config)


def test_moving_the_program_folder_does_not_lose_the_database(monkeypatch, tmp_path):
    """The note lives with the user, not with the program, so the folder can be
    copied anywhere afterwards."""
    import importlib

    from app import config

    data = tmp_path / "ProyectoPersonal" / "data"
    data.mkdir(parents=True)
    (data / "medtracker.db").write_bytes(b"the one database")
    appdata = tmp_path / "AppData"

    frozen_at(monkeypatch, tmp_path / "ProyectoPersonal" / "dist" / "app.exe", appdata)
    try:
        assert reload_config().DATA_DIR == data          # first run, from dist
        monkeypatch.undo()

        # ...and now it lives somewhere else entirely.
        frozen_at(monkeypatch, tmp_path / "Program Files" / "MedTracker" / "app.exe",
                  appdata)
        assert reload_config().DATA_DIR == data
    finally:
        monkeypatch.undo()
        importlib.reload(config)


def test_a_program_folder_with_no_installation_beside_it_keeps_its_own_data(
    monkeypatch, tmp_path
):
    import importlib

    from app import config

    appdata = tmp_path / "AppData"
    frozen_at(monkeypatch, tmp_path / "Somewhere" / "app.exe", appdata)
    try:
        assert reload_config().DATA_DIR == appdata / "MedTracker" / "data"
    finally:
        monkeypatch.undo()
        importlib.reload(config)


def test_the_data_folder_is_never_the_one_a_rebuild_replaces(monkeypatch, tmp_path):
    """A `data` folder *inside* the program folder is exactly what PyInstaller
    overwrites, so finding one there must not be taken as an answer."""
    import importlib

    from app import config

    program = tmp_path / "Medication Organizer"
    inside = program / "data"
    inside.mkdir(parents=True)
    (inside / "medtracker.db").write_bytes(b"about to be deleted by the next build")

    frozen_at(monkeypatch, program / "app.exe", tmp_path / "AppData")
    try:
        resolved = reload_config().DATA_DIR
        assert resolved != inside
        assert resolved == tmp_path / "AppData" / "MedTracker" / "data"
    finally:
        monkeypatch.undo()
        importlib.reload(config)


def test_a_note_pointing_at_nothing_is_ignored(monkeypatch, tmp_path):
    """The user deleted the old installation. Falling over would be a poor
    answer; quietly using the per-user folder is the right one."""
    import importlib

    from app import config

    appdata = tmp_path / "AppData"
    (appdata / "MedTracker").mkdir(parents=True)
    (appdata / "MedTracker" / "data-location.txt").write_text(
        str(tmp_path / "gone"), encoding="utf-8"
    )

    frozen_at(monkeypatch, tmp_path / "Somewhere" / "app.exe", appdata)
    try:
        assert reload_config().DATA_DIR == appdata / "MedTracker" / "data"
    finally:
        monkeypatch.undo()
        importlib.reload(config)


# --------------------------------------------------------------------------- #
# Two devices, two sets of eyes
# --------------------------------------------------------------------------- #
def test_the_computer_does_not_eat_the_phones_reminder(db):
    """The defect Edwin found by using it: `browser_delivered_at` marked a
    reminder as shown for the whole application, so whichever browser polled
    first took it and the other never saw it. With the application open on a
    computer and a phone — which is the entire point of the network access —
    the phone silently stopped being reminded of anything."""
    from datetime import timedelta

    from app.models.models import Notification, NotificationType
    from app.notifications.dispatcher import mark_browser_delivered, pending_for_browser
    from app.utils.timeutil import now_local

    computer, phone = "aaaa1111", "bbbb2222"

    # Both devices have the application open — that is when they are first
    # seen — and only then does a dose come due.
    assert pending_for_browser(db, "es", computer) == []
    assert pending_for_browser(db, "es", phone) == []

    db.add(Notification(
        type=NotificationType.DOSE.value, kind="at_time", fire_at=now_local(),
        dedupe_key="dose:1:at_time", payload="{}",
        title_key="notification.dose_title", body_key="notification.dose_at_time",
    ))
    db.flush()

    on_computer = pending_for_browser(db, "es", computer)
    assert len(on_computer) == 1
    mark_browser_delivered(db, [item["id"] for item in on_computer], computer)

    # The phone has not seen it, and must still be told.
    on_phone = pending_for_browser(db, "es", phone)
    assert len(on_phone) == 1, "the computer took the phone's reminder"

    mark_browser_delivered(db, [item["id"] for item in on_phone], phone)
    assert pending_for_browser(db, "es", phone) == []
    assert pending_for_browser(db, "es", computer) == []


def test_a_device_seen_for_the_first_time_is_not_shouted_at(db):
    """Opening the application on a new phone must not replay months of
    reminders that were dealt with long ago."""
    from app.models.models import Notification, NotificationType
    from app.notifications.dispatcher import pending_for_browser
    from app.utils.timeutil import now_local

    for index in range(3):
        db.add(Notification(
            type=NotificationType.DOSE.value, kind="at_time", fire_at=now_local(),
            dedupe_key=f"dose:{index}:at_time", payload="{}",
            title_key="notification.dose_title", body_key="notification.dose_at_time",
        ))
    db.flush()

    assert pending_for_browser(db, "es", "newphone01") == []


def test_a_browser_with_no_identity_still_works(db):
    """An old cached page, or something that is not a browser at all."""
    from app.models.models import Notification, NotificationType
    from app.notifications.dispatcher import mark_browser_delivered, pending_for_browser
    from app.utils.timeutil import now_local

    db.add(Notification(
        type=NotificationType.DOSE.value, kind="at_time", fire_at=now_local(),
        dedupe_key="dose:9:at_time", payload="{}",
        title_key="notification.dose_title", body_key="notification.dose_at_time",
    ))
    db.flush()

    items = pending_for_browser(db, "es", None)
    assert len(items) == 1
    mark_browser_delivered(db, [items[0]["id"]], None)
    assert pending_for_browser(db, "es", None) == []


def test_devices_that_stopped_coming_back_are_forgotten(db):
    from datetime import timedelta

    from app.models.models import BrowserClient
    from app.notifications.dispatcher import forget_idle_browsers
    from app.utils.timeutil import now_local

    db.add(BrowserClient(id="old", last_notification_id=1,
                         last_seen_at=now_local() - timedelta(days=200)))
    db.add(BrowserClient(id="current", last_notification_id=1, last_seen_at=now_local()))
    db.flush()

    assert forget_idle_browsers(db) == 1
    assert db.get(BrowserClient, "old") is None
    assert db.get(BrowserClient, "current") is not None


def test_each_browser_identifies_itself(client):
    """End to end, through HTTP, the way the two devices really do it."""
    from app.models.models import Notification, NotificationType
    from app.utils.timeutil import now_local
    from app.database.db import SessionLocal

    computer = {"X-Medtracker-Client": "cccc3333"}
    phone = {"X-Medtracker-Client": "dddd4444"}

    # Both have the page open before the reminder exists, as they would.
    client.get("/api/notifications/pending", headers=computer)
    client.get("/api/notifications/pending", headers=phone)

    session = SessionLocal()
    try:
        session.add(Notification(
            type=NotificationType.DOSE.value, kind="at_time", fire_at=now_local(),
            dedupe_key="dose:77:at_time", payload="{}",
            title_key="notification.dose_title", body_key="notification.dose_at_time",
        ))
        session.commit()
    finally:
        session.close()

    first = client.get("/api/notifications/pending", headers=computer).json()["items"]
    assert len(first) == 1
    client.post("/api/notifications/delivered",
                json={"ids": [item["id"] for item in first]}, headers=computer)

    second = client.get("/api/notifications/pending", headers=phone).json()["items"]
    assert len(second) == 1, "the phone was never told"


def test_the_browser_keeps_one_identity_across_reloads():
    root = Path(__file__).resolve().parent.parent
    api = (root / "app" / "static" / "js" / "api.js").read_text(encoding="utf-8")
    assert "localStorage.getItem(CLIENT_KEY)" in api
    assert "'X-Medtracker-Client'" in api


def test_a_device_is_remembered_across_polls(client):
    """The row is created during a GET, and a GET commits nothing by default —
    so it was thrown away every time, the device was met as a stranger on every
    poll, and a stranger is caught up to now. It would never have been shown
    anything at all."""
    from app.database.db import SessionLocal
    from app.models.models import BrowserClient

    headers = {"X-Medtracker-Client": "eeee5555"}
    client.get("/api/notifications/pending", headers=headers)

    session = SessionLocal()
    try:
        assert session.get(BrowserClient, "eeee5555") is not None
    finally:
        session.close()
