"""Internationalisation: both catalogs must stay complete and in sync."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from app.config import FORM_OPTIONS, FREQUENCY_OPTIONS, I18N_DIR, UNIT_OPTIONS
from app.i18n import (
    available_languages,
    flatten_keys,
    get_catalog,
    language_from_accept_header,
    normalize_language,
    t,
)
from app.services.textformat import (
    format_date,
    format_dose,
    format_frequency,
    format_quantity,
    format_time,
)

LANGUAGES = ("en", "es")


def test_both_catalogs_exist():
    for language in LANGUAGES:
        assert (I18N_DIR / f"{language}.json").exists()
    assert {entry["code"] for entry in available_languages()} == set(LANGUAGES)


def test_catalogs_have_exactly_the_same_keys():
    english = flatten_keys(get_catalog("en"))
    spanish = flatten_keys(get_catalog("es"))
    missing_in_spanish = sorted(english - spanish)
    missing_in_english = sorted(spanish - english)
    assert not missing_in_spanish, f"Missing Spanish keys: {missing_in_spanish}"
    assert not missing_in_english, f"Missing English keys: {missing_in_english}"


def test_no_translation_is_empty():
    for language in LANGUAGES:
        for key in sorted(flatten_keys(get_catalog(language))):
            value = t(key, language)
            assert value not in ("", None), f"{language}:{key} is empty"


def test_placeholders_match_between_languages():
    """{name}, {count}… must be the same in both catalogs or a message breaks."""
    pattern = re.compile(r"\{([a-z_]+)\}")
    english = get_catalog("en")
    spanish = get_catalog("es")
    for key in sorted(flatten_keys(english)):
        en_value = t(key, "en")
        es_value = t(key, "es")
        if not isinstance(en_value, str) or not isinstance(es_value, str):
            continue
        assert set(pattern.findall(en_value)) == set(pattern.findall(es_value)), key


def test_every_option_has_a_label_in_both_languages():
    for language in LANGUAGES:
        for unit in UNIT_OPTIONS:
            assert t(f"unit.{unit}", language) != f"unit.{unit}"
        for form in FORM_OPTIONS:
            assert t(f"form.{form}", language) != f"form.{form}"
            assert t(f"form.{form}_plural", language) != f"form.{form}_plural"
        for hours in FREQUENCY_OPTIONS:
            assert format_frequency(hours, language) != f"frequency.every_{hours}_hours"


def test_main_screens_are_translated():
    """A spot-check that the important surfaces really are in the catalogs."""
    for language in LANGUAGES:
        for key in (
            "nav.dashboard", "nav.medications", "nav.appointments", "nav.settings",
            "dashboard.next_dose", "dashboard.todays_doses", "dashboard.ending_soon",
            "medication.add", "medication.frequency", "medication.first_dose_time",
            "status.active", "status.completed", "status.suspended",
            "status.taken", "status.skipped", "status.missed",
            "appointment.add", "reminder.days_3", "reminder.day_1", "reminder.hours_3",
            "settings.language", "settings.default_first_dose_time",
            "validation.end_before_start", "error.generic",
            "notification.medication_title", "notification.appointment_title",
            "app.disclaimer",
        ):
            assert t(key, language) != key, f"{language}:{key} is missing"


def test_browser_language_detection():
    assert language_from_accept_header("es-MX,es;q=0.9,en;q=0.8") == "es"
    assert language_from_accept_header("en-US,en;q=0.9") == "en"
    assert language_from_accept_header("fr-FR,fr;q=0.9") == "en"  # falls back
    assert language_from_accept_header(None) == "en"
    assert language_from_accept_header("de;q=0.9,es;q=0.8") == "es"


def test_language_normalisation():
    assert normalize_language("es-MX") == "es"
    assert normalize_language("EN_GB") == "en"
    assert normalize_language("pt-BR") == "en"
    assert normalize_language(None) == "en"


def test_notification_bodies_are_localised():
    english = t(
        "notification.medication_body", "en",
        name="Amoxicillin", dose="500 mg", quantity="1 capsule",
    )
    spanish = t(
        "notification.medication_body", "es",
        name="Amoxicillin", dose="500 mg", quantity="1 cápsula",
    )
    assert "It's time to take" in english
    assert "Es hora de tomar" in spanish


def test_dose_and_quantity_formatting():
    assert format_dose("500", "mg", "en") == "500 mg"
    assert format_quantity(1, "capsule", "en") == "1 capsule"
    assert format_quantity(2, "capsule", "en") == "2 capsules"
    assert format_quantity(1, "capsule", "es") == "1 cápsula"
    assert format_quantity(2, "capsule", "es") == "2 cápsulas"


def test_time_formatting_follows_the_language():
    from datetime import datetime

    moment = datetime(2026, 8, 21, 22, 0)
    assert format_time(moment, "en") == "10:00 PM"
    assert format_time(moment, "es") == "22:00"
    assert format_date(moment, "en") == "August 21, 2026"
    assert format_date(moment, "es") == "21 de agosto de 2026"


@pytest.mark.parametrize("language", LANGUAGES)
def test_catalog_is_valid_json(language):
    with (I18N_DIR / f"{language}.json").open(encoding="utf-8") as handle:
        assert isinstance(json.load(handle), dict)


def test_no_hardcoded_ui_strings_in_the_javascript():
    """Guards the "everything must be translatable" rule.

    Any user-visible text in the frontend has to come from T.t(...), so a
    quoted sentence assigned to textContent/innerHTML is a bug.
    """
    js_dir = Path(__file__).resolve().parent.parent / "app" / "static" / "js"
    offenders = []
    pattern = re.compile(r"(textContent|innerHTML)\s*=\s*['\"]([^'\"]{4,})['\"]")
    for path in js_dir.glob("*.js"):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            match = pattern.search(line)
            if match and re.search(r"[A-Za-z]{4,}", match.group(2)):
                offenders.append(f"{path.name}:{number}: {line.strip()}")
    assert not offenders, "Hard-coded UI text found:\n" + "\n".join(offenders)


def test_no_hardcoded_ui_strings_in_the_templates():
    """Templates must use data-i18n hooks, including for accessible names."""
    templates = Path(__file__).resolve().parent.parent / "app" / "templates"
    offenders = []
    # Only literal attributes; the data-i18n-* hooks are exactly what we want.
    pattern = re.compile(r'(?<![\w-])(aria-label|title|placeholder|alt)="([^"]+)"')
    for path in templates.glob("*.html"):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            for attribute, value in pattern.findall(line):
                if "{{" in value or "{%" in value:
                    continue
                offenders.append(f"{path.name}:{number}: {attribute}=\"{value}\"")
    assert not offenders, "Hard-coded attribute text found:\n" + "\n".join(offenders)
