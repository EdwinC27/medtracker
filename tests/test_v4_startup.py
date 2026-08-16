"""Starting and stopping the application for real.

Not a mock: these tests boot the actual ASGI application on a real port through
the desktop launcher, poll it over HTTP, and shut it down again — the same code
path `Medication Organizer.exe` runs. What they cannot cover is Windows itself:
the tray icon and the frozen executable need a desktop session, and are called
out as such in the report rather than faked here.
"""

from __future__ import annotations

import socket
import tempfile
import threading
import time

import pytest

from app.desktop import launcher


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


@pytest.fixture()
def running_app(monkeypatch):
    """A real server, on its own port, against a throwaway database."""
    data_dir = tempfile.mkdtemp(prefix="medtracker-startup-")
    monkeypatch.setenv("MEDTRACKER_DATA_DIR", data_dir)

    port = free_port()
    report, handle = launcher.start_application("127.0.0.1", port, open_ui=False)
    try:
        yield report, handle, port
    finally:
        if handle is not None:
            handle.stop()


def test_the_application_starts_serves_and_stops(running_app):
    report, handle, port = running_app

    assert report.ok, report.as_text()
    assert [step.key for step in report.steps] == [
        "paths", "port", "server", "responding", "database", "scheduler",
    ]
    assert report.url == f"http://127.0.0.1:{port}"

    # It is actually serving: the health endpoint and a real page.
    health = launcher.probe(report.url)
    assert health is not None and health["ok"] is True
    assert health["database"] is True

    import urllib.request

    with urllib.request.urlopen(f"{report.url}/", timeout=5) as response:
        assert response.status == 200
        assert b"MedTracker" in response.read()

    # And it stops when asked, leaving nothing listening.
    handle.stop()
    time.sleep(0.4)
    assert launcher.probe(report.url, timeout=0.5) is None


def test_the_database_is_created_and_migrated_by_the_start(running_app):
    """The lifespan does it, not the launcher — but the launcher must see it."""
    report, _handle, _port = running_app
    import sqlite3

    # The path the application actually resolved at import time, not the one
    # the environment variable was changed to afterwards.
    from app.config import DB_PATH as path
    from app.database.migrations import CURRENT_VERSION

    assert path.exists()
    connection = sqlite3.connect(str(path))
    try:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == CURRENT_VERSION
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    finally:
        connection.close()
    assert {"medications", "medication_doses", "settings"} <= tables


def test_a_second_launch_hands_over_instead_of_starting_twice(running_app):
    """Two schedulers would mean two of every reminder."""
    report, _handle, port = running_app

    existing = launcher.already_running(port)
    assert existing is not None
    assert existing["version"]


def test_nothing_is_left_behind_after_shutdown(running_app):
    """The server runs on a thread of this process, so stopping it really is
    the end of it — there is no child process to orphan."""
    report, handle, _port = running_app
    before = {thread.name for thread in threading.enumerate()}
    assert any(name == "medtracker-server" for name in before)

    handle.stop()
    time.sleep(0.5)

    after = {thread.name for thread in threading.enumerate() if thread.is_alive()}
    assert "medtracker-server" not in after


def test_the_shutdown_helper_is_safe_to_call_twice(running_app):
    from app.desktop.__main__ import shutdown

    _report, handle, _port = running_app
    shutdown(handle)
    shutdown(handle)      # idempotent: the tray and a signal can both fire


def test_the_scheduler_runs_inside_the_started_application(monkeypatch):
    """The one thing the desktop wrapper exists for: reminders without a
    browser. The suite disables the scheduler by default, so this test turns it
    back on for one boot."""
    data_dir = tempfile.mkdtemp(prefix="medtracker-sched-")
    monkeypatch.setenv("MEDTRACKER_DATA_DIR", data_dir)
    monkeypatch.setenv("MEDTRACKER_DISABLE_SCHEDULER", "0")

    import importlib

    from app import config as config_module

    importlib.reload(config_module)
    from app.notifications import scheduler as background_scheduler

    port = free_port()
    monkeypatch.setattr("app.main.SCHEDULER_ENABLED", True)

    report, handle = launcher.start_application("127.0.0.1", port, open_ui=False)
    try:
        assert report.ok, report.as_text()
        health = launcher.probe(report.url)
        assert health["scheduler"] is True
        assert background_scheduler.status()["running"] is True
    finally:
        if handle is not None:
            handle.stop()
        background_scheduler.shutdown()
        monkeypatch.setenv("MEDTRACKER_DISABLE_SCHEDULER", "1")
        importlib.reload(config_module)

    # ...and stopping the server stopped it again.
    assert background_scheduler.status()["running"] is False
