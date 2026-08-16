"""Windows desktop toasts.

Uses `winotify`, which writes a temporary PowerShell script that talks to the
Windows Toast API. It needs no COM registration, no admin rights and no service
install, which is what makes it a good fit for a personal local app.

On any non-Windows system (or if winotify is not installed) the module degrades
gracefully: `is_available()` returns False and `send_toast()` reports the reason
instead of raising, so the rest of the application keeps working.
"""

from __future__ import annotations

import logging
import sys

logger = logging.getLogger(__name__)

_APP_ID = "MedTracker"

_unavailable_reason: str | None = None
_Notification = None
_audio = None

if sys.platform == "win32":
    try:  # pragma: no cover - depends on the host OS
        from winotify import Notification as _WinNotification
        from winotify import audio as _winotify_audio

        _Notification = _WinNotification
        _audio = _winotify_audio
    except Exception as exc:  # pragma: no cover
        _unavailable_reason = f"winotify: {exc}"
else:
    _unavailable_reason = f"platform {sys.platform}"


def is_available() -> bool:
    return _Notification is not None


def unavailable_reason() -> str | None:
    return _unavailable_reason


def send_toast(title: str, body: str, icon: str | None = None) -> tuple[bool, str | None]:
    """Show a Windows toast. Returns `(sent, error)` and never raises."""
    if _Notification is None:
        return False, _unavailable_reason or "unavailable"
    try:  # pragma: no cover - only runs on Windows
        toast = _Notification(
            app_id=_APP_ID, title=title, msg=body, icon=icon or "", duration="long"
        )
        if _audio is not None:
            toast.set_audio(_audio.Default, loop=False)
        toast.show()
        return True, None
    except Exception as exc:  # pragma: no cover
        logger.warning("Windows toast failed: %s", exc)
        return False, str(exc)
