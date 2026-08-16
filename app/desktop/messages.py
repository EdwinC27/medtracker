"""Text for the moments before the web interface exists.

A startup failure has to be explained without the browser, without the
JavaScript translator and possibly without the database — so these few strings
are read straight from the JSON catalogs, with a hard-coded English fallback for
the one case where even the catalogs cannot be loaded.

This is not a second translation system: it reads the same files as everything
else. It only removes the dependency on the parts of the application that may
be the very thing that failed.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# Used only if the catalogs themselves cannot be read.
_FALLBACK = {
    "title": "Medication Organizer could not start",
    "intro": "The application cannot safely continue.",
    "outro": "Please restart the application. Technical details are in the log:",
    "ok": "OK",
    "failed": "Failed",
    "paths": "Data folder",
    "port": "Network port",
    "server": "Web server",
    "responding": "Web server responding",
    "database": "Database",
    "scheduler": "Notification scheduler",
    "remedy_port": (
        "Another program is already using this port. Close it, or start the "
        "application again with a different port."
    ),
    "remedy_paths": "The application cannot write to its data folder.",
}

# A named failure gets a sentence about what to do about it, instead of the
# generic "please restart" — which, for a port that is taken, would be advice
# that fails in exactly the same way for ever.
_REMEDIES = {"port": "remedy_port", "paths": "remedy_paths"}


def _catalog_language() -> str:
    """The saved language if the database can be read, else the default."""
    try:
        from app.database.db import session_scope
        from app.services.settings_service import get_settings

        with session_scope() as db:
            saved = get_settings(db).language
            if saved:
                return saved
    except Exception:  # noqa: BLE001 - the database may be the failure itself
        pass
    from app.config import DEFAULT_LANGUAGE

    return DEFAULT_LANGUAGE


def _translate(key: str, language: str) -> str | None:
    try:
        from app.i18n import t

        value = t(f"startup.{key}", language)
        return None if value == f"startup.{key}" else value
    except Exception:  # noqa: BLE001
        return None


def startup_failure_text(report) -> str:
    """The message box shown when a required component did not start."""
    language = _catalog_language()

    def text(key: str) -> str:
        return _translate(key, language) or _FALLBACK.get(key, key)

    from app.config import APP_VERSION, LOG_FILE

    lines = [text("intro"), ""]
    for step in report.steps:
        label = text(step.key)
        mark = text("ok") if step.ok else text("failed")
        line = f"{label}: {mark}"
        if not step.ok and step.detail:
            line += f" ({step.detail})"
        lines.append(line)
    first = next((step for step in report.steps if not step.ok), None)
    remedy = _REMEDIES.get(first.key) if first else None
    if remedy:
        lines += ["", text(remedy)]

    lines += ["", text("outro"), str(LOG_FILE), "", f"v{APP_VERSION}"]
    return "\n".join(lines)
