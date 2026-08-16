"""Shared FastAPI dependencies."""

from __future__ import annotations

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from app.database.db import get_db
from app.i18n import language_from_accept_header, normalize_language
from app.services.settings_service import get_settings


def get_language(request: Request, db: Session = Depends(get_db)) -> str:
    """Resolve the active language.

    Priority: explicit `?lang=` (used by the language switcher preview) ->
    the saved preference in Settings -> the browser's Accept-Language header ->
    English.
    """
    explicit = request.query_params.get("lang")
    if explicit:
        return normalize_language(explicit)
    settings = get_settings(db)
    if settings.language:
        return normalize_language(settings.language)
    return language_from_accept_header(request.headers.get("accept-language"))
