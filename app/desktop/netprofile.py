"""Public or Private: the setting that silently decides whether the phone works.

Windows asks "Do you want your PC to be discoverable on this network?" once,
when a Wi-Fi is joined for the first time, and most people click past it. The
answer becomes the network's *profile*, and on a **Public** profile Windows
Firewall drops incoming connections by default — so the phone gets nothing,
with no error anywhere: the browser simply spins and times out.

That is the single most likely reason this application appears to stop working
after moving to another network, and it is invisible unless something goes
looking. So System Status looks, and says so in a sentence.

Read-only by construction: nothing here changes a profile or a firewall rule.
Changing either is the user's decision to make in Windows, not something an
application about medication should do behind their back.
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
import time
from dataclasses import dataclass

logger = logging.getLogger(__name__)

PUBLIC = "public"
PRIVATE = "private"
DOMAIN = "domain"
UNKNOWN = "unknown"

# PowerShell costs about a second to start, and the page is opened by hand, so
# caching only has to survive a refresh — not an afternoon. Longer would mean
# reporting the café's network for minutes after getting home, and sending the
# user to change a Windows setting for a network they already left.
CACHE_SECONDS = 30.0
TIMEOUT_SECONDS = 8.0

# `NetworkCategory` and `IPv4Connectivity` are enumerations, and `ConvertTo-Json`
# serialises an enumeration as its *number*: `"NetworkCategory":0`, not
# `"Public"`. Asking for the string form explicitly is the difference between
# this feature working on Windows and silently reporting "unknown" forever —
# and the numbers are accepted below as well, because a version of PowerShell
# that ignores the cast must not turn the warning off.
_COMMAND = (
    "Get-NetConnectionProfile | ForEach-Object { [pscustomobject]@{ "
    "Name = $_.Name; "
    "NetworkCategory = [string]$_.NetworkCategory; "
    "IPv4Connectivity = [string]$_.IPv4Connectivity } } | ConvertTo-Json -Compress"
)

_CATEGORIES = {
    "public": PUBLIC,
    "private": PRIVATE,
    "domainauthenticated": DOMAIN,
    # NetworkCategory as a bare enumeration value.
    "0": PUBLIC,
    "1": PRIVATE,
    "2": DOMAIN,
}

# IPv4Connectivity: Disconnected(0), NoTraffic(1), Subnet(2), LocalNetwork(3),
# Internet(4). The first two mean an adapter that is up but on no network —
# a disconnected card, or a virtual one from VirtualBox or WSL.
_NOT_CONNECTED = {"", "disconnected", "notraffic", "0", "1"}


@dataclass
class Profile:
    """One network Windows says this machine is currently on."""

    name: str
    category: str
    connected: bool

    def to_dict(self) -> dict:
        return {"name": self.name, "category": self.category, "connected": self.connected}


_cache: tuple[float, list[Profile]] | None = None


def supported() -> bool:
    return sys.platform == "win32"


def current(force: bool = False) -> list[Profile]:
    """The networks this machine is on. Empty when it cannot be known.

    Never raises and never blocks for long: a machine where PowerShell is
    restricted by policy reports nothing, and reporting nothing must read as
    "not known", never as "everything is fine".
    """
    global _cache

    if not supported():
        return []
    if not force and _cache is not None and time.monotonic() - _cache[0] < CACHE_SECONDS:
        return _cache[1]

    profiles = _ask_windows()
    _cache = (time.monotonic(), profiles)
    return profiles


def forget() -> None:
    """Drop the cache. Nothing in the application needs to: the cache is short
    enough that a network change is reported within half a minute."""
    global _cache
    _cache = None


def _ask_windows() -> list[Profile]:
    try:
        completed = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", _COMMAND],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_SECONDS,
            creationflags=_no_window(),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.info("Could not ask Windows about the network profile: %s", exc)
        return []

    if completed.returncode != 0:
        logger.info(
            "Windows refused the network profile query (%s): %s",
            completed.returncode, (completed.stderr or "").strip()[:200],
        )
        return []
    return parse(completed.stdout)


def _no_window() -> int:
    """Keep a console window from flashing up in the user's face.

    The application runs windowed from the tray; a bare `subprocess.run` on
    Windows opens a black box for as long as PowerShell takes, every time the
    status page is opened.
    """
    return getattr(subprocess, "CREATE_NO_WINDOW", 0)


def parse(raw: str) -> list[Profile]:
    """`ConvertTo-Json` gives an object for one network and a list for several."""
    raw = (raw or "").strip()
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except ValueError:
        logger.info("Unreadable answer from Get-NetConnectionProfile")
        return []
    if isinstance(data, dict):
        data = [data]
    if not isinstance(data, list):
        return []

    profiles = []
    for entry in data:
        if not isinstance(entry, dict):
            continue
        category = str(entry.get("NetworkCategory", "")).strip().lower()
        connectivity = str(entry.get("IPv4Connectivity", "")).strip().lower()
        profiles.append(
            Profile(
                name=str(entry.get("Name", "") or ""),
                category=_CATEGORIES.get(category, UNKNOWN),
                connected=connectivity not in _NOT_CONNECTED,
            )
        )
    return profiles


def blocks_other_devices() -> Profile | None:
    """The connected network that will stop the phone, if there is one.

    A machine on both a Public Wi-Fi and a Private one is not a warning: the
    private network is a way in. Only a machine whose every connected network is
    Public is actually unreachable.
    """
    profiles = [profile for profile in current() if profile.connected]
    if not profiles:
        return None
    if any(profile.category in (PRIVATE, DOMAIN) for profile in profiles):
        return None
    public = [profile for profile in profiles if profile.category == PUBLIC]
    return public[0] if public else None
