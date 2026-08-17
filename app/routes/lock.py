"""Enforcing the app lock at the edge.

One middleware, applied once, so no route has to remember to check. The rule it
implements is short:

* Anything on the allow-list goes through — the lock screen itself, the handful
  of endpoints it needs, the static files and the health probe.
* Everything else, while locked, is refused: a page redirects to the lock
  screen, an API call answers 423 with a translation key.

423 rather than 401 or 403 on purpose: there is no identity here and nothing to
log in as. The resource exists and the application is simply locked.

Two things the allow-list gets asked about often:

*Static files are allowed and uploads are not.* `/static/` has to be reachable
or the lock screen would have no stylesheet, and it holds nothing but the
application's own code and icons. The medication photographs used to live under
it; they now go through `/api/uploads/`, on the protected side of this line,
because a photograph of somebody's medicine cabinet is medical data.

*A request is not activity.* The interface polls itself while nobody is there,
so idle time is measured from what the browser reports as real input, not from
traffic.
"""

from __future__ import annotations

import logging

from fastapi import Request
from fastapi.responses import JSONResponse, RedirectResponse
from starlette.concurrency import run_in_threadpool

from app.routes import lock_cache

logger = logging.getLogger(__name__)

LOCK_PATH = "/lock"

# Reachable while locked. Everything here is either the lock screen's own
# machinery or something that carries no medical data.
ALLOWED_EXACT = frozenset(
    {
        LOCK_PATH,
        "/api/health",
        "/api/lock/state",
        "/api/lock/unlock",
        "/favicon.ico",
        # The certificate and the page explaining it. Both have to work before
        # the PIN, because on a phone this is what has to happen first: until
        # the certificate is installed the browser will not treat this
        # application as trustworthy at all. The file is a public certificate —
        # it identifies this computer and grants nothing.
        "/certificate",
        "/api/certificate",
    }
)
ALLOWED_PREFIXES = ("/static/",)

# The bootstrap payload is what gives the lock screen its translations, so it
# has to be reachable — see `bootstrap()` in the API, which serves a reduced
# body while locked instead of the settings.
ALLOWED_BOOTSTRAP = "/api/bootstrap"


def _is_allowed(path: str) -> bool:
    if path in ALLOWED_EXACT or path == ALLOWED_BOOTSTRAP:
        return True
    return any(path.startswith(prefix) for prefix in ALLOWED_PREFIXES)


def _read_lock_state(token: str) -> tuple[bool, bool]:
    """`(locked, configured)`, on a short-lived session of its own.

    Runs on a worker thread: SQLite here is blocking, and blocking the event
    loop on every request would let one slow write stall the entire server.
    """
    from app.database.db import session_scope
    from app.services import applock
    from app.services.settings_service import get_settings

    with session_scope() as db:
        settings = get_settings(db)
        configured = bool(settings.app_lock_enabled and settings.pin_hash)
        return applock.is_locked(settings, token=token), configured


async def is_locked_now(token: str = "") -> bool:
    """The middleware's question, answered as cheaply as it honestly can be."""
    if lock_cache.known() is False:
        return False

    # Taken before the read, handed back after it: if anything enabled or
    # disabled the lock while this was in flight, the result is thrown away
    # rather than written over the truth. See the note in `lock_cache`.
    at_epoch = lock_cache.epoch()
    try:
        locked, configured = await run_in_threadpool(_read_lock_state, token)
    except Exception as exc:  # noqa: BLE001
        # Fail closed when — and only when — we already know a lock exists.
        known = lock_cache.known()
        logger.warning("Could not read the lock state (known=%s): %s", known, exc)
        return bool(known)

    if not lock_cache.remember(configured, at_epoch):
        # Overtaken: do not trust our own answer either. Refusing this one
        # request is cheap, and the next one reads the settled state.
        return True
    return locked


async def lock_middleware(request: Request, call_next):
    from app.services import applock

    path = request.url.path

    if _is_allowed(path):
        return await call_next(request)

    token = request.cookies.get(applock.COOKIE_NAME, "")
    if await is_locked_now(token):
        if path.startswith("/api/"):
            return JSONResponse(
                status_code=423,
                content={"error": "error.locked", "params": {}, "fields": {}},
            )
        target = LOCK_PATH
        if request.method == "GET" and path != "/":
            from urllib.parse import quote

            # The query too: a calendar deep link is the week the user was
            # looking at, and dropping it would land them somewhere else.
            wanted = f"{path}?{request.url.query}" if request.url.query else path
            target = f"{LOCK_PATH}?next={quote(wanted, safe='/')}"
        return RedirectResponse(url=target, status_code=303)

    return await call_next(request)
