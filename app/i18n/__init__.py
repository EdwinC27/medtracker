"""Tiny translation layer.

Catalogs are plain JSON files (`en.json`, `es.json`) with nested keys accessed
with dots: ``t("notification.medication_title", "es")``.

No interface string should be hard-coded anywhere in the Python or JavaScript
code — add it here instead. `tests/test_i18n.py` asserts both catalogs have
exactly the same key set, so a missing translation fails the test-suite.
"""

from __future__ import annotations

import json
from functools import lru_cache
from typing import Any

from app.config import DEFAULT_LANGUAGE, I18N_DIR, SUPPORTED_LANGUAGES


@lru_cache(maxsize=None)
def get_catalog(language: str) -> dict[str, Any]:
    lang = normalize_language(language)
    path = I18N_DIR / f"{lang}.json"
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def normalize_language(language: str | None) -> str:
    """Map anything ('es-MX', 'ES', 'en_GB', None) onto a supported code."""
    if not language:
        return DEFAULT_LANGUAGE
    code = str(language).strip().lower().replace("_", "-")
    if code in SUPPORTED_LANGUAGES:
        return code
    base = code.split("-", 1)[0]
    if base in SUPPORTED_LANGUAGES:
        return base
    return DEFAULT_LANGUAGE


def language_from_accept_header(header: str | None) -> str:
    """Pick the best supported language from an Accept-Language header."""
    if not header:
        return DEFAULT_LANGUAGE
    candidates: list[tuple[float, int, str]] = []
    for index, part in enumerate(header.split(",")):
        piece = part.strip()
        if not piece:
            continue
        tag, _, params = piece.partition(";")
        quality = 1.0
        if params.strip().startswith("q="):
            try:
                quality = float(params.strip()[2:])
            except ValueError:
                quality = 0.0
        # Preserve original ordering for equal q-values.
        candidates.append((-quality, index, tag.strip().lower()))
    for _, _, tag in sorted(candidates):
        base = tag.split("-", 1)[0]
        if tag in SUPPORTED_LANGUAGES:
            return tag
        if base in SUPPORTED_LANGUAGES:
            return base
    return DEFAULT_LANGUAGE


def t(key: str, language: str = DEFAULT_LANGUAGE, **params: Any) -> str:
    """Translate `key`, substituting `{placeholders}`.

    Falls back to the default language and finally to the key itself, so a
    missing translation is visible but never crashes the app.
    """
    value = _lookup(get_catalog(language), key)
    if value is None and normalize_language(language) != DEFAULT_LANGUAGE:
        value = _lookup(get_catalog(DEFAULT_LANGUAGE), key)
    if value is None:
        return key
    if not isinstance(value, str):
        return value  # arrays such as format.months
    for name, replacement in params.items():
        value = value.replace("{" + name + "}", str(replacement))
    return value


def _lookup(catalog: dict[str, Any], key: str) -> Any:
    node: Any = catalog
    for part in key.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


def available_languages() -> list[dict[str, str]]:
    return [
        {
            "code": code,
            "name": get_catalog(code)["meta"]["name"],
            "locale": get_catalog(code)["meta"]["locale"],
        }
        for code in SUPPORTED_LANGUAGES
    ]


def flatten_keys(catalog: dict[str, Any], prefix: str = "") -> set[str]:
    """Every leaf key path in a catalog — used by the i18n test."""
    keys: set[str] = set()
    for name, value in catalog.items():
        path = f"{prefix}{name}"
        if isinstance(value, dict):
            keys |= flatten_keys(value, f"{path}.")
        else:
            keys.add(path)
    return keys
