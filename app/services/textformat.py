"""Human-readable text built on the server (notifications, mostly).

The browser has an equivalent set of helpers in `static/js/format.js`; both read
their month names, time format and patterns from the same JSON catalogs, so the
two never drift apart in wording.
"""

from __future__ import annotations

from datetime import date, datetime, time

from app.i18n import get_catalog, t


def format_time(value: time | datetime, language: str) -> str:
    moment = value.time() if isinstance(value, datetime) else value
    catalog = get_catalog(language)
    if catalog["meta"].get("time_24h"):
        return f"{moment.hour:02d}:{moment.minute:02d}"
    hour = moment.hour % 12 or 12
    suffix = catalog["format"]["pm"] if moment.hour >= 12 else catalog["format"]["am"]
    return f"{hour}:{moment.minute:02d} {suffix}"


def format_date(value: date | datetime, language: str) -> str:
    day = value.date() if isinstance(value, datetime) else value
    catalog = get_catalog(language)
    months = catalog["format"]["months"]
    return (
        catalog["format"]["date_long"]
        .replace("{day}", str(day.day))
        .replace("{month}", months[day.month - 1])
        .replace("{year}", str(day.year))
    )


def format_datetime(value: datetime, language: str) -> str:
    catalog = get_catalog(language)
    return (
        catalog["format"]["date_time"]
        .replace("{date}", format_date(value, language))
        .replace("{time}", format_time(value, language))
    )


def format_dose(dose_amount: str, dose_unit: str, language: str) -> str:
    """e.g. "500 mg"."""
    return f"{dose_amount} {t('unit.' + dose_unit, language)}".strip()


def format_quantity(quantity: float, form: str, language: str) -> str:
    """e.g. "1 capsule" / "2 cápsulas"."""
    number = int(quantity) if float(quantity).is_integer() else quantity
    key = f"form.{form}" if abs(float(quantity) - 1) < 1e-9 else f"form.{form}_plural"
    return f"{number} {t(key, language)}".strip()


def format_frequency(hours: int, language: str) -> str:
    specific = f"frequency.every_{hours}_hours"
    label = t(specific, language)
    if label == specific:  # no dedicated key for this frequency
        label = t("frequency.every_n_hours", language, hours=hours)
    return label
