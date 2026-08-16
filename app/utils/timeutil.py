"""Local-time helpers.

The whole application works with *naive local datetimes*. Centralising "what
time is it" in one function keeps the code honest and makes tests able to freeze
time by monkeypatching `now_local`.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta


def now_local() -> datetime:
    """Current wall-clock time of this machine, without tzinfo, second-truncated."""
    return datetime.now().replace(microsecond=0)


def today_local() -> date:
    return now_local().date()


def start_of_day(day: date) -> datetime:
    return datetime.combine(day, time.min)


def end_of_day(day: date) -> datetime:
    return datetime.combine(day, time.max).replace(microsecond=0)


def combine(day: date, moment: time) -> datetime:
    return datetime.combine(day, moment).replace(microsecond=0)


def parse_date(value: str | date | None) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value).strip()[:10])


def parse_time(value: str | time | None) -> time | None:
    """Accept 'HH:MM' or 'HH:MM:SS'."""
    if value is None or value == "":
        return None
    if isinstance(value, time):
        return value.replace(second=0, microsecond=0)
    raw = str(value).strip()
    parts = raw.split(":")
    if len(parts) < 2:
        raise ValueError(f"Invalid time: {raw}")
    return time(int(parts[0]), int(parts[1]))


def parse_datetime(value: str | datetime | None) -> datetime | None:
    """Accept ISO strings, including the 'YYYY-MM-DDTHH:MM' produced by
    <input type="datetime-local">. Any trailing timezone marker is dropped on
    purpose: the value is treated as local wall-clock time."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=None, microsecond=0)
    raw = str(value).strip().replace(" ", "T")
    if raw.endswith("Z"):
        raw = raw[:-1]
    if "+" in raw[10:]:
        raw = raw[: 10 + raw[10:].index("+")]
    parsed = datetime.fromisoformat(raw)
    return parsed.replace(tzinfo=None, microsecond=0)


def humanize_delta(delta: timedelta) -> tuple[int, int, int]:
    """Split a timedelta into (days, hours, minutes), clamped at zero."""
    total = max(int(delta.total_seconds()), 0)
    days, rest = divmod(total, 86400)
    hours, rest = divmod(rest, 3600)
    minutes = rest // 60
    return days, hours, minutes


def iso(value: datetime | date | time | None) -> str | None:
    return None if value is None else value.isoformat()
