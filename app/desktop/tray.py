"""The system tray icon.

Four items, which is all the specification asks for and all a reminder app
needs:

    Open Medication Organizer
    Status
    Lock application       (only while the app lock is on)
    Exit

The tray is what makes "close the window" and "quit the application" two
different things. Closing the browser tab leaves the icon — and therefore the
scheduler, and therefore the reminders — running; Exit is the one way out, and
it shuts everything down in order.

`pystray` is optional. Without it (or on a machine with no tray) the
application still runs perfectly well headless, which is what lets the whole
desktop layer be developed and tested away from Windows.
"""

from __future__ import annotations

import logging
import threading
from typing import Callable

logger = logging.getLogger(__name__)

ICON_SIZE = 64


def is_available() -> bool:
    try:
        import PIL  # noqa: F401
        import pystray  # noqa: F401
    except Exception:  # noqa: BLE001
        return False
    return True


def unavailable_reason() -> str | None:
    try:
        import PIL  # noqa: F401
        import pystray  # noqa: F401
    except Exception as exc:  # noqa: BLE001
        return str(exc)
    return None


def _build_icon_image():
    """A plain pill-shaped mark, drawn rather than shipped as a binary asset."""
    from PIL import Image, ImageDraw

    image = Image.new("RGBA", (ICON_SIZE, ICON_SIZE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle(
        (6, 18, ICON_SIZE - 6, ICON_SIZE - 18), radius=14, fill=(47, 111, 237, 255)
    )
    draw.line((ICON_SIZE // 2, 18, ICON_SIZE // 2, ICON_SIZE - 18),
              fill=(255, 255, 255, 255), width=4)
    return image


class Tray:
    """Wraps pystray so the caller never has to know whether it is there."""

    def __init__(
        self,
        *,
        on_open: Callable[[], None],
        on_status: Callable[[], None],
        on_lock: Callable[[], None],
        on_exit: Callable[[], None],
        labels: dict[str, str],
        lock_enabled: Callable[[], bool],
    ):
        self._on_open = on_open
        self._on_status = on_status
        self._on_lock = on_lock
        self._on_exit = on_exit
        self._labels = labels
        self._lock_enabled = lock_enabled
        self._icon = None
        self._thread: threading.Thread | None = None

    def start(self, blocking: bool = True) -> bool:
        """Show the icon. False when there is no tray to show it in."""
        if not is_available():
            logger.info("No system tray available (%s)", unavailable_reason())
            return False

        import pystray

        menu = pystray.Menu(
            pystray.MenuItem(
                self._labels.get("open", "Open"), lambda *_: self._on_open(), default=True
            ),
            pystray.MenuItem(self._labels.get("status", "Status"), lambda *_: self._on_status()),
            pystray.MenuItem(
                self._labels.get("lock", "Lock"),
                lambda *_: self._on_lock(),
                # Hidden entirely when the lock is off: an item that does
                # nothing is worse than no item.
                visible=lambda _item: self._lock_enabled(),
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(self._labels.get("exit", "Exit"), lambda *_: self.stop_and_exit()),
        )
        self._icon = pystray.Icon(
            "medtracker",
            _build_icon_image(),
            self._labels.get("title", "Medication Organizer"),
            menu,
        )

        if blocking:
            self._icon.run()
            return True

        self._thread = threading.Thread(
            target=self._icon.run, name="medtracker-tray", daemon=True
        )
        self._thread.start()
        return True

    def stop_and_exit(self) -> None:
        self.stop()
        self._on_exit()

    def stop(self) -> None:
        if self._icon is not None:
            try:
                self._icon.stop()
            except Exception as exc:  # noqa: BLE001
                logger.warning("Could not stop the tray icon: %s", exc)
            self._icon = None

    def notify(self, message: str, title: str | None = None) -> None:
        """A one-off balloon. Not used for reminders — those are the Windows
        channel's job and go through the existing notification system."""
        if self._icon is None:
            return
        try:
            self._icon.notify(message, title or self._labels.get("title", "MedTracker"))
        except Exception as exc:  # noqa: BLE001
            logger.debug("Tray balloon unavailable: %s", exc)
