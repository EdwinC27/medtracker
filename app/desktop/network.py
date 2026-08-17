"""Which address to listen on, decided before the application exists.

The setting lives in the database, and the address has to be chosen *before* the
port is bound — which is before the ORM, the session factory, or anything else
the application normally uses to read a setting. So this reads the one column
directly, with `sqlite3`, on a read-only connection, and treats every possible
failure as "no".

"No" being the safe answer is the whole design: there is no login, so a database
that cannot be read must never be a reason to start answering the network.
"""

from __future__ import annotations

import logging
import socket
import sqlite3

logger = logging.getLogger(__name__)

LOCAL_ONLY = "127.0.0.1"
EVERY_INTERFACE = "0.0.0.0"


def _stored_flag(column: str) -> bool:
    """One boolean setting, read without starting the application.

    Everything that can go wrong here answers False, and that is the design:
    both settings this reads open something up, so a database that cannot be
    read must never be the reason either of them turns on.
    """
    from app.config import DB_PATH

    if not DB_PATH.exists():
        return False
    try:
        connection = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=2)
    except sqlite3.Error as exc:
        logger.warning("Could not read %s: %s", column, exc)
        return False
    try:
        row = connection.execute(
            f"SELECT {column} FROM settings ORDER BY id LIMIT 1"
        ).fetchone()
    except sqlite3.Error as exc:
        # An older database that has not been migrated yet has no such column.
        logger.info("No %s in this database yet (%s)", column, exc)
        return False
    finally:
        connection.close()
    return bool(row and row[0])


def network_access_enabled() -> bool:
    return _stored_flag("network_access")


def https_enabled() -> bool:
    """Whether to speak TLS.

    Only meaningful together with network access: `127.0.0.1` is already a
    secure context as far as every browser is concerned, so HTTPS buys the
    computer nothing and would only add a certificate warning. It is the phone,
    on a real address, that cannot be offered a notification without it.
    """
    return _stored_flag("https_enabled") and network_access_enabled()


def scheme() -> str:
    return "https" if https_enabled() else "http"


def host_to_bind(requested: str | None = None) -> str:
    """`--host` if it was given, otherwise whatever the setting says.

    An explicit argument wins, so a shortcut or a script can always override the
    stored preference in either direction without editing the database.
    """
    if requested:
        return requested
    return EVERY_INTERFACE if network_access_enabled() else LOCAL_ONLY


def local_addresses() -> list[str]:
    """This machine's addresses on the local network, best effort.

    Used to tell the user what to type into their phone. Asking the routing
    table which address would be used to reach the outside world is the reliable
    way to get the LAN address; no packet is actually sent.
    """
    addresses: list[str] = []
    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            probe.connect(("192.0.2.1", 9))       # TEST-NET-1, never routed
            addresses.append(probe.getsockname()[0])
        finally:
            probe.close()
    except OSError:
        pass

    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            address = info[4][0]
            if address not in addresses and not address.startswith("127."):
                addresses.append(address)
    except OSError:
        pass

    return addresses
