"""Whether a lock exists at all — remembered, so the middleware is cheap.

The lock check runs before every single request. Reading the settings row each
time means a synchronous SQLite query on the event loop for people who have
never turned the lock on, and, when the scheduler happens to be writing, a wait
of up to the connection timeout for *the whole server*.

So the answer to the cheap question — "is a PIN configured?" — is cached, and
the expensive one — "is it locked right now?" — is only asked when it is. Every
route that can change the answer calls `invalidate()`.

The cache also decides what happens when the database cannot be read:

* Never read successfully → the application has not finished starting. Let the
  request through; there is nothing to protect yet.
* Known to have no lock → let it through, which is the correct answer anyway.
* Known to have a lock → refuse. Failing open here would mean an unreadable
  database, which is something a bystander can *cause* by keeping the file
  busy, silently removed the PIN screen.

Why the epoch
-------------
Reads happen on a worker thread, so a read that began before the PIN was
created can finish after it and write "no lock configured" over the truth. That
one direction never heals — `known() is False` skips the database entirely, so
nothing would ever read it again — and the application would serve everything
with the lock switched on. The epoch is the fix: a result is only remembered if
nothing invalidated the cache while the read was in flight.
"""

from __future__ import annotations

import threading

_lock = threading.Lock()
_configured: bool | None = None
_epoch = 0


def invalidate() -> None:
    """Forget what we knew, and disown any read still in flight."""
    global _configured, _epoch
    with _lock:
        _configured = None
        _epoch += 1


def epoch() -> int:
    """Take before starting a read; hand back to `remember`."""
    with _lock:
        return _epoch


def remember(configured: bool, at_epoch: int | None = None) -> bool:
    """Record what a read found, unless it was overtaken. True if kept."""
    global _configured
    with _lock:
        if at_epoch is not None and at_epoch != _epoch:
            return False
        _configured = configured
        return True


def known() -> bool | None:
    with _lock:
        return _configured
