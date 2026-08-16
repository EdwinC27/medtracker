"""Storage for the one real secret in this app: the SMTP password.

Windows
-------
The password is encrypted with **DPAPI** (`CryptProtectData`), reached through
`ctypes` so there is no extra dependency. DPAPI derives the key from your
Windows user account, which means:

* the ciphertext can only be decrypted while signed in as the same Windows
  user, on the same machine;
* copying `medtracker.db` (or a backup of it) to another PC leaves the password
  unreadable;
* nothing readable is ever written to the database, to a log or to the repo.

The trade-off, worth knowing: reinstalling Windows, or moving the folder to
another machine or user account, makes the stored password undecryptable and
you simply type it again in Settings. That is the intended behaviour, not a
failure.

Other platforms
---------------
DPAPI does not exist outside Windows, so the value is written to
`data/secret_store.json` with `0600` permissions (owner-only) and the database
stores a reference instead of the secret. This path exists so the test-suite
and development on a non-Windows machine work; it is documented in the README
as noticeably weaker than DPAPI.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import sys

logger = logging.getLogger(__name__)

_DPAPI_PREFIX = "dpapi:"
_FILE_PREFIX = "file:"
_FILE_KEY = "smtp_password"


# --------------------------------------------------------------------------- #
# Windows DPAPI through ctypes
# --------------------------------------------------------------------------- #
def dpapi_available() -> bool:
    return sys.platform == "win32"


def _dpapi_encrypt(plaintext: str) -> str:  # pragma: no cover - Windows only
    import ctypes
    from ctypes import wintypes

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    raw = plaintext.encode("utf-8")
    source = ctypes.create_string_buffer(raw, len(raw))
    blob_in = DATA_BLOB(len(raw), ctypes.cast(source, ctypes.POINTER(ctypes.c_char)))
    blob_out = DATA_BLOB()

    crypt32 = ctypes.windll.crypt32
    if not crypt32.CryptProtectData(
        ctypes.byref(blob_in), "MedTracker SMTP", None, None, None, 0, ctypes.byref(blob_out)
    ):
        raise OSError("CryptProtectData failed")
    try:
        encrypted = ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(blob_out.pbData)
    return _DPAPI_PREFIX + base64.b64encode(encrypted).decode("ascii")


def _dpapi_decrypt(stored: str) -> str:  # pragma: no cover - Windows only
    import ctypes
    from ctypes import wintypes

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    raw = base64.b64decode(stored[len(_DPAPI_PREFIX) :])
    source = ctypes.create_string_buffer(raw, len(raw))
    blob_in = DATA_BLOB(len(raw), ctypes.cast(source, ctypes.POINTER(ctypes.c_char)))
    blob_out = DATA_BLOB()

    crypt32 = ctypes.windll.crypt32
    if not crypt32.CryptUnprotectData(
        ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out)
    ):
        raise OSError("CryptUnprotectData failed")
    try:
        decrypted = ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(blob_out.pbData)
    return decrypted.decode("utf-8")


# --------------------------------------------------------------------------- #
# Owner-only file fallback
# --------------------------------------------------------------------------- #
def _store_path():
    from app.config import DATA_DIR

    return DATA_DIR / "secret_store.json"


def _file_write(value: str) -> str:
    path = _store_path()
    data = {}
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            data = {}
    data[_FILE_KEY] = value
    path.write_text(json.dumps(data), encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:  # pragma: no cover - filesystem without POSIX modes
        pass
    return _FILE_PREFIX + _FILE_KEY


def _file_read() -> str | None:
    path = _store_path()
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8")).get(_FILE_KEY)
    except (ValueError, OSError):
        return None


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #
def protect(plaintext: str) -> str:
    """Encrypt a secret for storage. Returns the opaque value to persist."""
    if not plaintext:
        return ""
    if dpapi_available():
        try:
            return _dpapi_encrypt(plaintext)
        except OSError as exc:  # pragma: no cover
            logger.warning("DPAPI unavailable (%s); falling back to the local file", exc)
    return _file_write(plaintext)


def unprotect(stored: str | None) -> str | None:
    """Read a secret back, or None if it cannot be decrypted here."""
    if not stored:
        return None
    if stored.startswith(_DPAPI_PREFIX):
        if not dpapi_available():
            return None
        try:
            return _dpapi_decrypt(stored)
        except OSError as exc:  # pragma: no cover
            logger.warning("Could not decrypt the stored SMTP password: %s", exc)
            return None
    if stored.startswith(_FILE_PREFIX):
        return _file_read()
    return None


def describe_backend() -> str:
    """Which mechanism this machine will use, for the Settings screen."""
    return "dpapi" if dpapi_available() else "file"


def clear() -> None:
    """Forget the stored password (used when the user empties the field)."""
    path = _store_path()
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            data.pop(_FILE_KEY, None)
            path.write_text(json.dumps(data), encoding="utf-8")
        except (ValueError, OSError):  # pragma: no cover
            pass
