"""Standard output and standard error, on a program that has neither.

A windowed Windows build — PyInstaller with `console=False`, which is what makes
`Medication Organizer.exe` open without a black terminal behind it — starts with
`sys.stdout` and `sys.stderr` set to **None**. Not a closed file, not a null
device: `None`.

Most code never notices. Libraries that ask a question of the terminal do, and
they do not fail politely. Uvicorn's log formatter asks `sys.stdout.isatty()` to
decide whether to colour its output; against `None` that is an `AttributeError`,
which `logging.config.dictConfig` catches and re-raises as the memorable

    ValueError: Unable to configure formatter 'default'

— which is what the user is shown, from a message box, instead of their
medication list.

So the streams are given somewhere to go before anything else runs. Not the log
file: this is for output nobody asked for, from code that assumed a console it
does not have. It is discarded, and the application's own logging — which does
go to the log file — is untouched.
"""

from __future__ import annotations

import io
import sys


class _Sink(io.TextIOBase):
    """Writable, never a terminal, discards everything."""

    def writable(self) -> bool:
        return True

    def write(self, text: str) -> int:
        return len(text)

    def flush(self) -> None:
        pass

    def isatty(self) -> bool:
        return False

    def fileno(self) -> int:
        raise io.UnsupportedOperation("not a real file")


def ensure_streams() -> list[str]:
    """Replace any missing standard stream. Returns which ones were replaced."""
    replaced = []
    for name in ("stdout", "stderr", "stdin"):
        if getattr(sys, name, None) is None:
            setattr(sys, name, _Sink())
            replaced.append(name)
    return replaced
