"""Telling the open screens that something changed.

Two devices on the same database — the computer and a phone — are two browsers
looking at the same rows. Marking a dose as taken on the phone has always
*written* to the same place; the computer just did not find out until its own
one-minute refresh came round. This is how it finds out immediately.

The mechanism is deliberately small. There is one number in memory, bumped
whenever anything is written, and a stream that reports it. No message bus, no
per-record events, no diff: a screen is told "the data changed", and it reloads
what it was already able to load. That means there is nothing to keep in sync
between the two halves, and a browser that misses an event is not left showing
something stale — the next event, or its own timer, brings it up to date.

The number lives in memory rather than in the database on purpose. It exists to
coordinate the browsers attached to *this* running process, and a process that
restarts has already made every browser reload.
"""

from __future__ import annotations

import threading

_lock = threading.Lock()
_revision = 0


def bump(reason: str = "") -> int:
    """Something changed. Returns the new revision."""
    global _revision
    with _lock:
        _revision += 1
        return _revision


def revision() -> int:
    with _lock:
        return _revision
