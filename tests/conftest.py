"""Test fixtures.

Every test runs against a throwaway SQLite file in a temp directory and with the
background scheduler disabled, so nothing touches the real data.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

# Point the app at a temporary data directory BEFORE anything imports config.
_TEMP_DIR = tempfile.mkdtemp(prefix="medtracker-tests-")
os.environ["MEDTRACKER_DATA_DIR"] = _TEMP_DIR
os.environ["MEDTRACKER_DISABLE_SCHEDULER"] = "1"

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from app.models.models import Base  # noqa: E402
from app.services.settings_service import ensure_settings  # noqa: E402


@pytest.fixture(autouse=True)
def _clean_process_wide_state(monkeypatch):
    """Three module-level caches emptied around every test — and no PowerShell.

    Each cache is a deliberate performance decision — the last device that
    connected, whether the network is public, whether TLS is on — and each is
    process-wide, so without this a test's result depends on what ran before
    it. Every `TestClient` request writes to the first of them.

    The network profile is pinned off for the whole suite as well. Left alone,
    a test that renders the status page really does run `Get-NetConnectionProfile`
    on the machine running the tests, and the answer decides whether the row is
    `ok` or `warning` — green on Linux, and on Windows dependent on which Wi-Fi
    the laptop happens to be joined to. That failed his build once already.
    A test that wants a profile says so itself.
    """
    from app.desktop import netprofile
    from app.routes import origin
    from app.services import reach

    def clear():
        reach.forget()
        netprofile.forget()
        origin.forget_tls()

    clear()
    monkeypatch.setattr(netprofile, "supported", lambda: False)
    yield
    clear()


@pytest.fixture()
def db():
    """An isolated in-memory database session."""
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, future=True)
    session = Session()
    ensure_settings(session)
    session.commit()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """A TestClient backed by its own SQLite file."""
    from app.database import db as db_module
    from app.main import app
    from sqlalchemy import create_engine as _create_engine

    engine = _create_engine(
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
        finally:
            session.close()

    app.dependency_overrides[db_module.get_db] = override_get_db
    # A loopback base URL rather than the default "testserver": the application
    # refuses a `Host` header it does not recognise (see `app/routes/origin.py`),
    # and a client that does not look like the real browser would be testing a
    # different application from the one that ships.
    with TestClient(app, base_url="http://127.0.0.1:8000") as test_client:
        yield test_client
    app.dependency_overrides.clear()
    engine.dispose()
