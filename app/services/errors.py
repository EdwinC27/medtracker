"""Domain errors.

Services raise these; the API layer turns them into a JSON body carrying
*translation keys* (never a stack trace and never a hard-coded sentence), and
the frontend renders them in the active language.
"""

from __future__ import annotations


class AppError(Exception):
    """Base class. `message_key` is a key of the i18n catalogs."""

    status_code = 400
    message_key = "error.generic"

    def __init__(self, message_key: str | None = None, **params):
        self.message_key = message_key or self.message_key
        self.params = params
        super().__init__(self.message_key)

    def to_dict(self) -> dict:
        return {"error": self.message_key, "params": self.params, "fields": {}}


class NotFoundError(AppError):
    status_code = 404
    message_key = "error.not_found"


class ValidationError(AppError):
    """One or more form fields are invalid.

    `fields` maps a field name to a translation key, e.g.
    ``{"end_date": "validation.end_before_start"}``.
    """

    status_code = 422
    message_key = "error.validation"

    def __init__(self, fields: dict[str, str] | None = None, message_key: str | None = None):
        self.fields = fields or {}
        super().__init__(message_key)

    def to_dict(self) -> dict:
        return {"error": self.message_key, "params": {}, "fields": self.fields}


class DatabaseError(AppError):
    status_code = 500
    message_key = "error.database"
