"""Application configuration.

Everything is local-first: paths are resolved relative to the project root so the
application can be copied anywhere on disk and still work.

Two rules earn their own explanation, because getting either wrong loses data:

*Code and data are not the same place.* Run from source, the data folder sits
next to the `app` package, which is convenient and has always worked. Run from
the packaged `Medication Organizer.exe`, `__file__` points inside PyInstaller's
own extraction folder — a folder that is REPLACED wholesale by the next build.
Anchoring the database there would mean every upgrade silently deleted the
user's entire history, so when the application is frozen the data lives under
`%LOCALAPPDATA%`, outside anything an install or an upgrade touches.

*Uploads are data, not static assets.* The photographs of a person's medication
are as private as the rest of the record, and anything served from `/static/`
is reachable without passing the app lock. They live in the data folder and are
served through a guarded route.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Project root = the folder that contains the "app" package.
BASE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BASE_DIR.parent

FROZEN = bool(getattr(sys, "frozen", False))


def _default_data_dir() -> Path:
    """Where the database, backups, exports, uploads and logs belong."""
    if not FROZEN:
        return PROJECT_ROOT / "data"
    # Packaged: a per-user location that no reinstall or upgrade overwrites.
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
    if base:
        return Path(base) / "MedTracker" / "data"
    return Path.home() / ".medtracker" / "data"


# `.strip() or` rather than a plain default: an environment variable that is
# set but empty would otherwise resolve to Path("") — the current working
# directory — and put the database wherever a shortcut happened to start from.
DATA_DIR = Path(os.environ.get("MEDTRACKER_DATA_DIR", "").strip() or _default_data_dir())

DB_PATH = DATA_DIR / "medtracker.db"
DATABASE_URL = f"sqlite:///{DB_PATH}"

# Not under `static/`: see the module docstring.
UPLOAD_DIR = DATA_DIR / "uploads"

TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"
I18N_DIR = BASE_DIR / "i18n"

LOG_DIR = DATA_DIR / "logs"
LOG_FILE = LOG_DIR / "medtracker.log"

APP_NAME = "MedTracker"
APP_VERSION = "4.0.0"

# Loopback by default. The lock is a single application-wide flag, so anything
# that can reach the port sees whatever the person at the keyboard has unlocked
# — which on 0.0.0.0 means the whole network. Opening it up is a deliberate
# choice, made by setting MEDTRACKER_HOST, not the default.
HOST = os.environ.get("MEDTRACKER_HOST", "127.0.0.1")
PORT = int(os.environ.get("MEDTRACKER_PORT", "8000"))

# The background scheduler wakes up this often (seconds) to look for due doses,
# due appointment reminders and overdue doses.
SCHEDULER_INTERVAL_SECONDS = int(os.environ.get("MEDTRACKER_TICK", "60"))

# Set MEDTRACKER_DISABLE_SCHEDULER=1 to run the web app without the background
# worker (used by the test-suite).
SCHEDULER_ENABLED = os.environ.get("MEDTRACKER_DISABLE_SCHEDULER", "") != "1"

# Image uploads
MAX_IMAGE_BYTES = 4 * 1024 * 1024  # 4 MB
ALLOWED_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp"}

SUPPORTED_LANGUAGES = ("en", "es")
DEFAULT_LANGUAGE = "en"

# Frequencies offered by the UI. Adding a new one here is enough for it to show
# up everywhere (form, filters, notifications) as long as a matching
# `frequency.every_N_hours` translation key exists.
FREQUENCY_OPTIONS = (4, 6, 8, 12, 24)

# Dose units and pharmaceutical forms offered by the form. They are stored as
# keys and translated through `unit.*` / `form.*`, which is what lets a
# notification read "1 capsule" in English and "1 cápsula" in Spanish.
UNIT_OPTIONS = ("mg", "g", "mcg", "ml", "iu", "percent", "unit")
FORM_OPTIONS = (
    "tablet",
    "capsule",
    "pill",
    "ml",
    "drop",
    "injection",
    "sachet",
    "puff",
    "patch",
    "spray",
    "suppository",
    "other",
)

# Longest treatment accepted by validation (guards against typos in the end
# date creating an enormous dose schedule).
MAX_TREATMENT_DAYS = 366 * 5

# Open-ended treatments (no end date) get their doses generated this far ahead
# and topped up on every scheduler tick, so the table never grows without
# bound and the dashboard always knows the next dose.
DOSE_HORIZON_DAYS = int(os.environ.get("MEDTRACKER_HORIZON_DAYS", "60"))

# Reminders around each scheduled dose, in minutes relative to its time.
# Order matters: it is the order shown in Settings.
DOSE_NOTIFICATION_OFFSETS: tuple[tuple[str, int], ...] = (
    ("before_30", -30),
    ("before_15", -15),
    ("before_5", -5),
    ("at_time", 0),
    ("after_15", 15),
    ("after_30", 30),
)

# Marking a dose as taken this long before its scheduled time (or later) is
# treated as normal and asks for no confirmation.
TAKEN_CONFIRMATION_MINUTES = 30

# ---------------------------------------------------------------- v3 ------ #
# "Remind me later" options, in minutes. A snooze moves the reminder only; the
# dose keeps its original scheduled time.
SNOOZE_OPTIONS = (10, 30, 60)

# Appearance. "system" follows the operating system / browser preference.
THEME_OPTIONS = ("system", "light", "dark")

# Calendar. Views are rendered from a backend query bounded to the visible
# range, so a year of doses is never shipped to the browser at once.
CALENDAR_VIEWS = ("month", "week", "day")
# Hard ceiling on how wide a single calendar request may be.
CALENDAR_MAX_RANGE_DAYS = 62

# Backups
BACKUP_DIR = DATA_DIR / "backups"
BACKUP_FREQUENCIES = ("daily", "weekly")
BACKUP_KEEP_OPTIONS = (3, 7, 14, 30)
BACKUP_PREFIX = "medtracker"

# Generated exports live here; each one is written fresh and served once.
EXPORT_DIR = DATA_DIR / "exports"
EXPORT_DATASETS = (
    "medications",
    "doses",
    "appointments",
    "doctors",
    "timeline",
)

# How many results each group of a search returns.
SEARCH_LIMIT = 20


# --------------------------------------------------------------------------- #
# Creating the folders
# --------------------------------------------------------------------------- #
DATA_SUBDIRECTORIES = ("logs", "backups", "exports", "uploads")


def ensure_directories() -> None:
    """Create the data folders, and say so if that is impossible.

    Called from the start sequence rather than at import. Doing it at import
    meant that installing into a folder the user cannot write to — `C:\\Program
    Files`, or anything an antivirus has decided to protect — raised deep inside
    an `import` statement, before any code existed to catch it. The packaged
    application answered that with a Python traceback in a dialog box, which is
    exactly what v4 promised never to show.
    """
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    for name in DATA_SUBDIRECTORIES:
        (DATA_DIR / name).mkdir(parents=True, exist_ok=True)


def try_ensure_directories() -> Exception | None:
    """Best effort, for the import-time callers that cannot fail usefully."""
    try:
        ensure_directories()
    except OSError as exc:
        return exc
    return None


# Import-time convenience for everything that has always assumed the folders are
# there (the test-suite, `python -m app.main`, scripts). A failure here is not
# raised: the start sequence checks the same thing a moment later and reports it
# in a way the user can read.
DIRECTORY_ERROR = try_ensure_directories()
