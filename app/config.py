"""Application configuration.

Everything is local-first: paths are resolved relative to the project root so the
application can be copied anywhere on disk and still work.
"""

from __future__ import annotations

import os
from pathlib import Path

# Project root = the folder that contains the "app" package.
BASE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BASE_DIR.parent

DATA_DIR = Path(os.environ.get("MEDTRACKER_DATA_DIR", PROJECT_ROOT / "data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)

DB_PATH = DATA_DIR / "medtracker.db"
DATABASE_URL = f"sqlite:///{DB_PATH}"

UPLOAD_DIR = BASE_DIR / "static" / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"
I18N_DIR = BASE_DIR / "i18n"

LOG_DIR = DATA_DIR / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = LOG_DIR / "medtracker.log"

APP_NAME = "MedTracker"
APP_VERSION = "1.0.0"

HOST = os.environ.get("MEDTRACKER_HOST", "0.0.0.0")
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
