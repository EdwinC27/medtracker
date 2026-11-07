"""Did another device actually get in — and by which address?

Everything else about the network is a prediction: the port is open, the
certificate names the machine, the profile is Private, therefore the phone
*should* work. This is the one part that is not a prediction. It records that a
request arrived from something that is not this computer, and what address it
was addressed to.

That answers the question the user actually has, which is never "is the port
bound" but "did my phone reach it, and does the name work or do I still need
the number". A name that resolves proves itself the first time it is used.

Kept in memory on purpose. It is worth nothing after a restart, it is not
medical data, and writing it to the database would mean a disk write on every
single request.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import datetime

_lock = threading.Lock()


@dataclass
class Visit:
    """The last request that came from somewhere other than this computer."""

    address: str          # the requesting device, e.g. the phone's 192.168.1.34
    host: str             # what it asked for: a name, or this machine's number
    by_name: bool
    at: datetime

    def to_dict(self) -> dict:
        from app.utils.timeutil import iso

        return {
            "address": self.address,
            "host": self.host,
            "by_name": self.by_name,
            "at": iso(self.at),
        }


_last: Visit | None = None

LOOPBACK = ("127.", "::1", "::ffff:127.", "localhost")


def note(client_address: str | None, host_header: str | None) -> None:
    """Record one request. Cheap enough to call on every single one."""
    if not client_address or is_this_computer(client_address):
        return

    from app.routes.origin import _hostname
    from app.utils.timeutil import now_local

    host = _hostname(host_header or "")
    if not host:
        return

    global _last
    with _lock:
        _last = Visit(
            address=client_address,
            host=host,
            by_name=looks_like_a_name(host),
            at=now_local(),
        )


def is_this_computer(address: str) -> bool:
    """Loopback — and this machine's own LAN address, which is the trap.

    Opening `https://Edwin-Castillo.local:8000` in a browser *on the PC* does
    not come from 127.0.0.1: the kernel picks the LAN address as the source,
    because that is the address the name resolved to. Counting that would let
    the page announce "another device reached it, by name — the name works"
    when nothing but this computer ever connected, which is worse than saying
    nothing, since the user stops testing and believes it.
    """
    address = address.strip().strip("[]").lower()
    if any(address.startswith(prefix) for prefix in LOOPBACK):
        return True

    return address in _own_addresses()


_own: tuple[float, frozenset[str]] | None = None
OWN_ADDRESS_SECONDS = 60.0


def _own_addresses() -> frozenset[str]:
    """This machine's own addresses, remembered for a minute.

    Asking costs a UDP socket and a `getaddrinfo`, and this runs on every
    request that is not loopback — which is every request the phone makes. The
    answer changes when the router hands out a new number, so it is re-asked,
    just not sixty times a second.
    """
    global _own
    import time

    now = time.monotonic()
    if _own is not None and now - _own[0] < OWN_ADDRESS_SECONDS:
        return _own[1]

    try:
        from app.desktop.network import local_addresses

        addresses = frozenset(own.strip().lower() for own in local_addresses())
    except Exception:  # noqa: BLE001 - a diagnostic never breaks a request
        addresses = frozenset()
    _own = (now, addresses)
    return addresses


def looks_like_a_name(host: str) -> bool:
    """`Edwin-Castillo.local` is a name; `192.168.1.20` is not.

    The distinction is the whole point of reporting this: only the name keeps
    working when the router hands out a different number.
    """
    import ipaddress

    try:
        ipaddress.ip_address(host.strip("[]"))
        return False
    except ValueError:
        return True


def last() -> Visit | None:
    with _lock:
        return _last


def forget() -> None:
    global _last, _own
    with _lock:
        _last = None
    _own = None


def note_request(request) -> None:
    """Called from the cross-site guard, on the requests it lets through.

    There rather than in a middleware of its own: the guard already runs on
    every request and has already decided the request is ours, so a refused
    request is never counted as a device that got in — and the application does
    not grow a whole extra layer around every response for a diagnostic. It is
    also outside the PIN lock, so a phone sitting on the lock screen still
    counts as having arrived, which is exactly the thing being reported.
    """
    try:
        client = request.client.host if request.client else None
        note(client, request.headers.get("host"))
    except Exception:  # noqa: BLE001 - never turn a real request into a 500
        pass
