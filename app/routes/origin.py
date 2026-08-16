"""Refusing requests that another website made on the user's behalf.

The application answers on `http://127.0.0.1:8000` with no login, because the
person at the keyboard is the only user. That is reasonable — and it is exactly
what makes it worth guarding, because *any* page the user has open in another
tab can also send it requests. A form post or a `fetch` from `evil.example`
reaches `http://127.0.0.1:8000/api/import` with the user's own browser, and
`/api/import` replaces the entire database.

Browsers do not stop this on their own: a POST with a simple content type is
sent without asking the server's permission first, and while the response is
unreadable cross-origin, the *effect* has already happened. So:

* Every state-changing request (anything that is not GET or HEAD) must either
  come from us — an `Origin` or `Referer` header pointing at this application —
  or carry `X-Requested-With`, which a browser will not send cross-origin
  without first asking us and being refused.
* The `Host` header must be a loopback name, which closes DNS rebinding: a
  hostile domain that resolves to 127.0.0.1 arrives with its own name in `Host`.

Nothing here is a login. It is the browser's own rules, used the way a local
application has to use them.
"""

from __future__ import annotations

import logging
from urllib.parse import urlsplit

from fastapi import Request
from fastapi.responses import HTMLResponse, JSONResponse

logger = logging.getLogger(__name__)

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "[::1]", "::1", "0.0.0.0"})
REQUESTED_WITH = "x-requested-with"


def _hostname(value: str) -> str:
    """The host part of a `Host` header or an origin, without the port.

    Parsing this by hand is where guards like this one usually go wrong, so the
    two traps are handled explicitly: `user@host` — a value that *ends* in a
    name we trust but is really addressed elsewhere — is refused outright, and a
    bare IPv6 literal is not mistaken for a `host:port` pair.
    """
    if not value:
        return ""
    if "//" in value:
        value = urlsplit(value).netloc
    if "@" in value:
        return ""                             # never a header a browser sends
    if value.startswith("["):                 # bracketed IPv6, possibly :port
        return value.partition("]")[0] + "]"
    if value.count(":") > 1:                  # bare IPv6 literal, no port
        return value
    return value.rsplit(":", 1)[0] if ":" in value else value


def is_local(value: str) -> bool:
    return _hostname(value).lower() in LOOPBACK_HOSTS


def is_acceptable_host(value: str) -> bool:
    """Is this `Host` header one we are willing to answer to?

    Loopback, obviously. The address the server was actually told to bind to,
    because someone who sets `MEDTRACKER_HOST` to their machine's LAN address
    means to reach it by that address. And any bare IP literal — DNS rebinding
    needs a *name* to re-point, so an address cannot be that attack.

    What is refused is a domain name we have never heard of, which is exactly
    the shape rebinding takes.
    """
    import ipaddress

    host = _hostname(value).lower()
    if not host:
        return False
    if host in LOOPBACK_HOSTS:
        return True

    from app.config import HOST as BOUND_HOST

    if host == _hostname(BOUND_HOST).lower():
        return True
    try:
        ipaddress.ip_address(host.strip("[]"))
        return True
    except ValueError:
        return False


def _is_us(value: str, request: Request) -> bool:
    """Does this origin or referer name this very application?

    Loopback, or the same host the request itself arrived on — which is what
    makes the check work when the server has been opened up deliberately
    (`MEDTRACKER_HOST=0.0.0.0`) and the phone on the sofa addresses it by the
    PC's LAN address. Comparing against the bound address alone would compare
    against `0.0.0.0`, which no browser ever sends.
    """
    if not value or value == "null":
        # "null" is a sandboxed iframe or a file:// page — not us.
        return False
    host = _hostname(value).lower()
    if not host:
        return False
    if host in LOOPBACK_HOSTS:
        return True
    return host == _hostname(request.headers.get("host", "")).lower()


def _same_origin(request: Request) -> bool:
    origin = request.headers.get("origin")
    if origin:
        return _is_us(origin, request)
    referer = request.headers.get("referer")
    if referer:
        return _is_us(referer, request)
    return False


def is_acceptable(request: Request) -> bool:
    if not is_acceptable_host(request.headers.get("host", "")):
        return False
    if request.method in SAFE_METHODS:
        return True
    if _same_origin(request):
        return True
    # No Origin and no Referer at all is a non-browser client — curl, a script,
    # the tray — which cannot be a cross-site attack. Requiring the header keeps
    # that honest without demanding a token nobody can obtain.
    if REQUESTED_WITH in request.headers:
        return True
    return not (request.headers.get("origin") or request.headers.get("referer"))


async def origin_middleware(request: Request, call_next):
    if is_acceptable(request):
        return await call_next(request)

    logger.warning(
        "Refused a cross-site %s %s (origin=%r host=%r)",
        request.method, request.url.path,
        request.headers.get("origin"), request.headers.get("host"),
    )
    if "text/html" in request.headers.get("accept", ""):
        # Somebody typed an address we do not answer to — a machine name, a
        # `hosts` alias — into their browser. A page of JSON would be a baffling
        # answer to that; a sentence and the address that does work is not.
        return HTMLResponse(status_code=403, content=_refusal_page())
    return JSONResponse(
        status_code=403,
        content={"error": "error.cross_site", "params": {}, "fields": {}},
    )


def _refusal_page() -> str:
    """A minimal page, in both languages, with no dependency on the app.

    Deliberately self-contained: this is served to a browser that reached us by
    a name we do not recognise, so it cannot be assumed that any asset,
    stylesheet or translation fetch from here would be answered either.
    """
    from html import escape

    from app.config import DEFAULT_LANGUAGE, PORT
    from app.i18n import t

    address = f"http://127.0.0.1:{PORT}"
    language = DEFAULT_LANGUAGE
    try:
        from app.database.db import session_scope
        from app.services.settings_service import get_settings

        with session_scope() as db:
            language = get_settings(db).language or DEFAULT_LANGUAGE
    except Exception:  # noqa: BLE001 - a refusal page is never worth failing over
        pass

    message = escape(t("error.cross_site", language))
    return (
        f'<!doctype html><html lang="{escape(language)}"><head>'
        '<meta charset="utf-8"><title>MedTracker</title>'
        '<style>body{font-family:system-ui,sans-serif;margin:4rem auto;max-width:32rem;'
        'line-height:1.6;color:#222}a{color:#2563eb}</style></head><body>'
        f"<h1>MedTracker</h1><p>{message}</p>"
        f'<p><a href="{address}">{address}</a></p></body></html>'
    )
