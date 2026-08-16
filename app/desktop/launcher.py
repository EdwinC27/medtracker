"""Starting the application as a desktop program.

The web application is unchanged: this module only puts a Windows-shaped
wrapper around it. One process holds everything — the HTTP server runs on a
thread inside it, and so does the scheduler that FastAPI's own lifespan starts.
That is deliberate: with nothing spawned as a child process, exiting cannot
leave an orphaned `python.exe` behind serving the port or firing reminders.

The startup sequence is explicit so a failure can be named:

    paths → (adopt) → port → server → responding → database → scheduler → UI

Each step records whether it worked. If a required one did not, the caller gets
a report it can put in front of the user instead of a blank window.
"""

from __future__ import annotations

import logging
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field

from app.config import APP_VERSION, PORT

logger = logging.getLogger(__name__)

# How long to wait for the server to answer before calling the start a failure.
STARTUP_TIMEOUT_SECONDS = 30
POLL_SECONDS = 0.25


@dataclass
class Step:
    """One stage of the start, and whether it got there."""

    key: str
    ok: bool
    required: bool = True
    detail: str | None = None

    def to_dict(self) -> dict:
        return {"key": self.key, "ok": self.ok, "required": self.required,
                "detail": self.detail}


@dataclass
class StartupReport:
    steps: list[Step] = field(default_factory=list)
    url: str | None = None

    def add(self, key: str, ok: bool, required: bool = True, detail: str | None = None) -> Step:
        step = Step(key=key, ok=ok, required=required, detail=detail)
        self.steps.append(step)
        if not ok:
            logger.error("startup: %s failed (%s)", key, detail)
        return step

    @property
    def ok(self) -> bool:
        return all(step.ok for step in self.steps if step.required)

    @property
    def failures(self) -> list[Step]:
        return [step for step in self.steps if not step.ok]

    def to_dict(self) -> dict:
        return {"ok": self.ok, "url": self.url,
                "steps": [step.to_dict() for step in self.steps]}

    def as_text(self) -> str:
        """Plain, translated-at-the-edge summary for a message box or a log."""
        lines = [f"MedTracker {APP_VERSION}", ""]
        for step in self.steps:
            lines.append(f"{'OK    ' if step.ok else 'FAILED'}  {step.key}"
                         + (f"  — {step.detail}" if step.detail else ""))
        return "\n".join(lines)


# --------------------------------------------------------------------------- #
# The server, on a thread of this process
# --------------------------------------------------------------------------- #
class PortInUse(OSError):
    """The address is already taken — by our own other copy, or by something else."""


class ServerHandle:
    """A uvicorn server owned by this process, startable and stoppable."""

    def __init__(self, host: str, port: int):
        self.host = host
        self.port = port
        self._server = None
        self._socket = None
        self._thread: threading.Thread | None = None
        self.error: str | None = None

    @property
    def url(self) -> str:
        # Whatever the server is bound to, the local UI talks to the loopback
        # address — and by default that is also all it is bound to.
        return f"http://127.0.0.1:{self.port}"

    def bind(self) -> None:
        """Claim the port first, and only then let anything else happen.

        This is not a micro-optimisation. Uvicorn runs the application's
        lifespan *before* it binds, so a second copy started on an occupied port
        would migrate the database, mark doses missed, queue reminders, send
        e-mails and write a backup — all against the file the first copy has
        open — and only then discover it could not listen and shut down again.
        Binding here means a start that cannot have the port does nothing at
        all, which is what "a failure changes nothing" has to mean here.
        """
        import socket

        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            # Deliberately no SO_REUSEADDR: we want "somebody is already there"
            # to be an error, because that is the question being asked.
            sock.bind((self.host, self.port))
            sock.listen(128)
        except OSError as exc:
            sock.close()
            raise PortInUse(str(exc)) from exc
        self._socket = sock

    def start(self) -> bool:
        import uvicorn

        from app.main import app

        if self._socket is None:
            self.bind()

        from app.utils.streams import ensure_streams

        # A windowed build has no stdout, and uvicorn's own log configuration
        # asks the terminal whether it wants colour. Both halves of that are
        # handled: the streams exist, and uvicorn is told not to reconfigure
        # logging at all — `app.main` already set it up, and letting a library
        # replace the application's logging from underneath it was never
        # something we wanted.
        ensure_streams()

        # No reloader, no dev server: the packaged application runs the same
        # ASGI app through a plain uvicorn Server object, on the socket that is
        # already ours.
        config = uvicorn.Config(
            app, host=self.host, port=self.port, log_level="info",
            access_log=False, lifespan="on", log_config=None,
        )
        self._server = uvicorn.Server(config)
        sock = self._socket

        def run() -> None:
            try:
                self._server.run(sockets=[sock])
            except Exception as exc:  # noqa: BLE001 - reported, never swallowed
                self.error = str(exc)
                logger.exception("The web server stopped: %s", exc)

        self._thread = threading.Thread(target=run, name="medtracker-server", daemon=True)
        self._thread.start()
        return True

    def stop(self, timeout: float = 10.0) -> None:
        """Ask uvicorn to shut down, which runs FastAPI's lifespan shutdown —
        and that is what stops the scheduler and closes the database."""
        if self._server is not None:
            self._server.should_exit = True
        if self._thread is not None:
            self._thread.join(timeout=timeout)
            if self._thread.is_alive():  # pragma: no cover - timing dependent
                logger.warning("The web server did not stop within %ss", timeout)
        if self._socket is not None:
            try:
                self._socket.close()
            except OSError:  # pragma: no cover
                pass
        self._server = None
        self._socket = None
        self._thread = None


# --------------------------------------------------------------------------- #
# Probing
# --------------------------------------------------------------------------- #
def probe(url: str, timeout: float = 2.0) -> dict | None:
    """Ask the health endpoint who is there. None if nobody answers."""
    import json

    try:
        with urllib.request.urlopen(f"{url}/api/health", timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError, TimeoutError):
        return None


def wait_until_healthy(url: str, timeout: float | None = None) -> dict | None:
    # Read at call time, not bound as a default at import, so changing the
    # constant actually changes the wait.
    timeout = STARTUP_TIMEOUT_SECONDS if timeout is None else timeout
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        payload = probe(url, timeout=1.0)
        if payload is not None:
            return payload
        time.sleep(POLL_SECONDS)
    return None


def already_running(port: int = PORT) -> dict | None:
    """Is one of our own instances already on this port?

    Two copies would mean two schedulers and two sets of reminders, so a second
    launch hands over to the first instead of starting anything.
    """
    return probe(f"http://127.0.0.1:{port}", timeout=1.0)


# --------------------------------------------------------------------------- #
# The sequence
# --------------------------------------------------------------------------- #
def start_application(host: str, port: int, *, open_ui: bool = True) -> tuple[StartupReport, ServerHandle | None]:
    """Bring everything up and report on each step.

    The database and the scheduler are started by FastAPI's lifespan, not here:
    duplicating that would give the application two places that initialise it.
    What this does is *observe* them through the health endpoint, so a failure
    in either is caught and named rather than discovered later by a user
    wondering why no reminder arrived.
    """
    from app.services import system_status

    report = StartupReport()
    system_status.mark_started()

    # 1. Somewhere to write. Everything else depends on it.
    try:
        from app.config import DATA_DIR, ensure_directories

        ensure_directories()
        probe_file = DATA_DIR / ".write-test"
        probe_file.write_text("ok", encoding="utf-8")
        probe_file.unlink()
        report.add("paths", True, detail=str(DATA_DIR))
    except OSError as exc:
        report.add("paths", False, detail=str(exc))
        return report, None

    # 2. On the packaged application's very first run, bring an existing
    #    installation's data across. Here rather than inside the server's
    #    lifespan because copying a large database and a folder of backups can
    #    take longer than we are prepared to wait for the server to answer —
    #    and a slow copy reported as "the application did not start" would be
    #    both wrong and alarming. Every later run finds a database and returns
    #    immediately.
    try:
        from app.utils.datamove import adopt_existing_database

        adopted = adopt_existing_database()
        if adopted is not None:
            report.add("adopted", True, required=False, detail=str(adopted))
    except Exception as exc:  # noqa: BLE001 - never fatal: an empty app still runs
        logger.warning("Could not bring existing data across: %s", exc)

    # 3. The port, claimed before anything else happens — see `ServerHandle.bind`.
    handle = ServerHandle(host, port)
    try:
        handle.bind()
        report.add("port", True, detail=str(port))
    except PortInUse as exc:
        report.add("port", False, detail=str(exc))
        return report, None

    # 4. The server, which on start runs the lifespan: database, then scheduler.
    try:
        handle.start()
        report.add("server", True, detail=handle.url)
    except Exception as exc:  # noqa: BLE001
        handle.stop()
        report.add("server", False, detail=str(exc))
        return report, None

    health = wait_until_healthy(handle.url)
    if health is None:
        report.add("responding", False, detail=handle.error or "no response")
        return report, handle

    report.add("responding", True, detail=f"v{health.get('version')}")
    report.add("database", bool(health.get("database")), detail=None)
    # A stopped scheduler is bad but not fatal: the UI still works and the user
    # can still see and mark their doses, which is better than refusing to run.
    report.add(
        "scheduler",
        bool(health.get("scheduler")),
        required=False,
        detail=health.get("scheduler_last_error"),
    )

    report.url = handle.url
    if open_ui and report.ok:
        open_browser(handle.url)
    return report, handle


def open_browser(url: str) -> bool:
    import webbrowser

    try:
        return bool(webbrowser.open(url))
    except Exception as exc:  # noqa: BLE001 - never fatal
        logger.warning("Could not open the browser: %s", exc)
        return False


def show_error_dialog(title: str, message: str) -> None:
    """Say something on a machine with no console and no UI yet.

    ctypes talks straight to the Win32 message box, so this needs no extra
    dependency and works before — or instead of — the web interface. Anywhere
    else it falls back to the log and standard error.
    """
    import sys

    logger.error("%s: %s", title, message.replace("\n", " | "))
    if sys.platform == "win32":  # pragma: no cover - depends on the host OS
        try:
            import ctypes

            MB_ICONERROR = 0x10
            ctypes.windll.user32.MessageBoxW(None, message, title, MB_ICONERROR)
            return
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not show the error dialog: %s", exc)
    print(f"{title}\n{message}", file=sys.stderr)
