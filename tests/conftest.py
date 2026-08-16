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
