"""The change stream the open screens listen to.

One endpoint, `GET /api/events`, held open by every browser that has the
application on screen. When anything is written — by this browser, by the phone
in the other room, or by the scheduler marking a dose overdue — the stream says
so and the screens reload themselves.

Server-sent events rather than websockets: the traffic only ever goes one way,
the browser reconnects on its own when a phone wakes from sleep, and it is
ordinary HTTP, so it passes through the same middleware as everything else with
no special handling.

The stream is polled internally rather than pushed to. A counter checked once a
second costs nothing and, more importantly, needs no cross-thread signalling:
the scheduler runs on its own thread and would otherwise have to reach into the
event loop to wake anybody up. One second of latency is not perceptible when the
alternative was sixty.

Two rules it must not break:

* **A locked application says nothing.** The stream is opened while unlocked and
  outlives the lock, so being locked has to be re-checked inside the loop, not
  only at the door.
* **It is never a keep-alive.** The stream does not count as activity, so
  leaving a tab open cannot stop the idle timer from locking the application.
"""

from __future__ import annotations

import asyncio
import json
import logging

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from app.services import live

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api")

# How often the stream looks at the counter.
POLL_SECONDS = 1.0
# A comment line often enough to keep the connection from being reaped by a
# phone's radio, a proxy, or an aggressive power saver.
HEARTBEAT_SECONDS = 20.0


def _event(name: str, payload: dict) -> str:
    return f"event: {name}\ndata: {json.dumps(payload)}\n\n"


async def _is_locked(token: str) -> bool:
    from app.routes.lock import is_locked_now

    try:
        return await is_locked_now(token)
    except Exception as exc:  # noqa: BLE001 - a broken check ends the stream
        logger.warning("The change stream could not check the lock: %s", exc)
        return True


@router.get("/events")
async def events(request: Request):
    """Long-lived: yields whenever the revision changes."""
    from app.services import applock

    token = request.cookies.get(applock.COOKIE_NAME, "")

    async def stream():
        seen = live.revision()
        # Say hello immediately, so the browser knows the stream is alive and
        # can tell "connected" apart from "still connecting".
        yield _event("hello", {"revision": seen})

        quiet = 0.0
        while True:
            if await request.is_disconnected():
                return

            current = live.revision()
            if current != seen:
                if await _is_locked(token):
                    # Locked while this was open: say so and stop. The page
                    # turns that into a redirect to the lock screen.
                    yield _event("locked", {})
                    return
                seen = current
                quiet = 0.0
                yield _event("changed", {"revision": current})
            else:
                quiet += POLL_SECONDS
                if quiet >= HEARTBEAT_SECONDS:
                    quiet = 0.0
                    yield ": keep-alive\n\n"

            await asyncio.sleep(POLL_SECONDS)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


# --------------------------------------------------------------------------- #
# Noticing that something changed
# --------------------------------------------------------------------------- #
# Requests the browsers make *of their own accord*, on a timer. They are not
# changes anybody made, and bumping the revision for them would have every
# device reload every thirty seconds for ever.
NOT_A_CHANGE = frozenset({
    "/api/lock/activity",
    "/api/notifications/delivered",
})


async def revision_middleware(request: Request, call_next):
    """Any successful write is a change every open screen should hear about."""
    response = await call_next(request)
    if (
        request.method not in ("GET", "HEAD", "OPTIONS")
        and 200 <= response.status_code < 300
        and request.url.path not in NOT_A_CHANGE
    ):
        live.bump(f"{request.method} {request.url.path}")
    return response
