"""Launch the desktop application.

    python desktop.py

This is the file PyInstaller freezes into `Medication Organizer.exe`; it exists
at the project root so both the build and a plain double-click have one obvious
entry point.

Two things happen before anything is imported from the application, and the
order matters:

1. The project root goes on the import path.
2. The standard streams are given somewhere to write. A windowed build has
   `sys.stdout is None`, and code that assumes a console — uvicorn's log
   formatter, most obviously — fails on that at import or configuration time,
   long before any of our own error handling exists to say something useful
   about it. See `app/utils/streams.py`.
"""

from __future__ import annotations

import io
import sys
from pathlib import Path

# Frozen or not, the project root has to be importable before anything else.
sys.path.insert(0, str(Path(__file__).resolve().parent))


def _ensure_streams() -> None:
    """Inlined, deliberately: this has to work before `app` is importable."""
    try:
        from app.utils.streams import ensure_streams

        ensure_streams()
    except Exception:  # pragma: no cover - the fallback below is the point
        for name in ("stdout", "stderr", "stdin"):
            if getattr(sys, name, None) is None:
                setattr(sys, name, io.StringIO())


_ensure_streams()

from app.desktop.__main__ import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
