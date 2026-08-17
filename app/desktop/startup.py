"""Start with Windows.

One registry value under the current user's `Run` key:

    HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run
        MedTracker = "C:\\...\\Medication Organizer.exe" --background

Chosen over a Task Scheduler task because it needs no administrator rights, is
one value to write and remove, and is the mechanism Windows itself shows in
Task Manager → Startup, so the user can always see and override it there.

Everything here is written so the rest of the application never has to ask what
platform it is on: on anything that is not Windows the backend simply reports
that it is unavailable, and the setting is still stored and still shown, it just
has nothing to act on.
"""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "MedTracker"
# Told to the executable so it knows to start hidden rather than pop a window
# open in the user's face while they are logging in.
BACKGROUND_FLAG = "--background"


@dataclass
class StartupState:
    """What the machine actually says, as opposed to what the setting says."""

    supported: bool
    enabled: bool
    command: str | None = None
    error: str | None = None
    # Registered, but pointing at something that is no longer there.
    stale: bool = False

    def to_dict(self) -> dict:
        return {
            "supported": self.supported,
            "enabled": self.enabled,
            "command": self.command,
            "error": self.error,
            "stale": self.stale,
        }


def is_supported() -> bool:
    return sys.platform == "win32"


def launch_command() -> str:
    """The command Windows should run at logon.

    Frozen by PyInstaller this is the executable itself. Running from source it
    is the interpreter plus the module, which keeps the feature usable while
    developing without pretending an .exe exists.
    """
    if getattr(sys, "frozen", False):
        return f'"{Path(sys.executable)}" {BACKGROUND_FLAG}'

    from app.config import PROJECT_ROOT

    script = PROJECT_ROOT / "desktop.py"
    return f'"{_windowless_interpreter()}" "{script}" {BACKGROUND_FLAG}'


def _windowless_interpreter() -> Path:
    """`pythonw.exe` where there is one, otherwise `python.exe`.

    The whole promise of starting with Windows is that reminders work without
    anything being in the way. Registering `python.exe` keeps that promise and
    breaks the spirit of it: a black console window opens at every single logon
    and sits there until it is closed — which most people will do, taking the
    reminders with it. `pythonw.exe` is the same interpreter with no console.
    """
    interpreter = Path(sys.executable)
    if interpreter.name.lower() == "python.exe":
        windowless = interpreter.with_name("pythonw.exe")
        if windowless.is_file():
            return windowless
    return interpreter


# --------------------------------------------------------------------------- #
# Reading and writing the registry
# --------------------------------------------------------------------------- #
def read_state() -> StartupState:
    """What is registered right now. Never raises."""
    if not is_supported():
        return StartupState(supported=False, enabled=False)
    try:
        import winreg

        try:
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY)
        except FileNotFoundError:
            # No `Run` key at all means nothing is registered. That is an
            # answer, not a fault, and reporting it as an error would light up
            # System Status in red over a machine that is working perfectly.
            return StartupState(supported=True, enabled=False)
        with key:
            try:
                value, _kind = winreg.QueryValueEx(key, VALUE_NAME)
            except FileNotFoundError:
                return StartupState(supported=True, enabled=False)
            return StartupState(
                supported=True, enabled=True, command=str(value),
                stale=not _command_is_runnable(str(value)),
            )
    except OSError as exc:  # pragma: no cover - depends on the host OS
        logger.warning("Could not read the Windows startup entry: %s", exc)
        return StartupState(supported=True, enabled=False, error=str(exc))


def _command_is_runnable(command: str) -> bool:
    """Does the program the registry points at still exist?

    `reconcile` rewrites the entry to wherever the application is running from
    now, so launching once from a temporary folder — an unzipped download, a USB
    stick — pins startup to a path that later disappears. Windows then fails
    silently at every logon while the setting still reads "on". Noticing costs
    one `exists()`.
    """
    command = command.strip()
    if not command:
        return False
    if command.startswith('"'):
        target = command[1:].partition('"')[0]
    else:
        target = command.partition(" ")[0]
    try:
        return Path(target).exists()
    except OSError:  # pragma: no cover
        return False


def apply(enabled: bool) -> StartupState:
    """Make the registry agree with `enabled`. Returns the resulting state.

    Never raises: a machine policy that forbids writing this key must not stop
    the application from starting, it must only be reported.
    """
    if not is_supported():
        return StartupState(supported=False, enabled=False)

    try:
        import winreg

        with winreg.CreateKeyEx(
            winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE
        ) as key:
            if enabled:
                command = launch_command()
                winreg.SetValueEx(key, VALUE_NAME, 0, winreg.REG_SZ, command)
                logger.info("Registered for Windows startup: %s", command)
            else:
                try:
                    winreg.DeleteValue(key, VALUE_NAME)
                    logger.info("Removed the Windows startup entry")
                except FileNotFoundError:
                    pass  # already absent, which is the state we wanted
    except OSError as exc:  # pragma: no cover - depends on the host OS
        logger.warning("Could not change the Windows startup entry: %s", exc)
        return StartupState(supported=True, enabled=not enabled, error=str(exc))

    return read_state()


def reconcile(enabled: bool) -> StartupState:
    """Bring the registry in line with the stored setting, at every launch.

    The setting is the source of truth. Doing this on each start means the
    entry is repaired if something else removed it, and — importantly — that it
    points at wherever the application lives now, rather than at the path it had
    when the switch was first turned on.
    """
    state = read_state()
    if not state.supported:
        return state
    if enabled and state.enabled and state.command == launch_command():
        return state
    if not enabled and not state.enabled:
        return state
    return apply(enabled)
