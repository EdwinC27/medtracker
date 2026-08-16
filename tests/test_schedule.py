"""Dose schedule calculation — the most important logic in the app."""

from __future__ import annotations

from datetime import date, datetime, time

import pytest

from app.services.scheduling import generate_dose_times


def test_every_8_hours_matches_the_example_in_the_spec():
    times = generate_dose_times(date(2026, 8, 15), date(2026, 8, 25), time(10, 0), 8)

    assert times[0] == datetime(2026, 8, 15, 10, 0)
    assert times[1] == datetime(2026, 8, 15, 18, 0)
    assert times[2] == datetime(2026, 8, 16, 2, 0)
    assert times[3] == datetime(2026, 8, 16, 10, 0)
    # 11 days x 3 doses per day, first day starts at 10:00 so it only has 2
    # slots before midnight but the sequence continues into the next day.
    assert all(a < b for a, b in zip(times, times[1:]))


def test_every_12_hours():
    times = generate_dose_times(date(2026, 8, 15), date(2026, 8, 17), time(10, 0), 12)
    assert times == [
        datetime(2026, 8, 15, 10, 0),
        datetime(2026, 8, 15, 22, 0),
        datetime(2026, 8, 16, 10, 0),
        datetime(2026, 8, 16, 22, 0),
        datetime(2026, 8, 17, 10, 0),
        datetime(2026, 8, 17, 22, 0),
    ]


def test_every_24_hours_is_one_dose_a_day_at_the_same_hour():
    times = generate_dose_times(date(2026, 8, 15), date(2026, 8, 19), time(10, 0), 24)
    assert len(times) == 5
    assert {moment.time() for moment in times} == {time(10, 0)}
    assert [moment.date() for moment in times] == [
        date(2026, 8, 15), date(2026, 8, 16), date(2026, 8, 17),
        date(2026, 8, 18), date(2026, 8, 19),
    ]


def test_every_4_and_6_hours():
    four = generate_dose_times(date(2026, 3, 1), date(2026, 3, 1), time(0, 0), 4)
    assert [moment.hour for moment in four] == [0, 4, 8, 12, 16, 20]

    six = generate_dose_times(date(2026, 3, 1), date(2026, 3, 2), time(6, 0), 6)
    assert [moment.strftime("%d %H:%M") for moment in six] == [
        "01 06:00", "01 12:00", "01 18:00", "02 00:00", "02 06:00", "02 12:00", "02 18:00",
    ]


def test_doses_never_continue_after_the_end_date():
    times = generate_dose_times(date(2026, 8, 15), date(2026, 8, 25), time(10, 0), 8)
    assert max(times).date() == date(2026, 8, 25)
    assert all(moment.date() <= date(2026, 8, 25) for moment in times)


def test_single_day_treatment():
    times = generate_dose_times(date(2026, 8, 15), date(2026, 8, 15), time(22, 0), 8)
    assert times == [datetime(2026, 8, 15, 22, 0)]


def test_late_first_dose_still_produces_one_dose_on_the_start_day():
    times = generate_dose_times(date(2026, 8, 15), date(2026, 8, 16), time(23, 30), 12)
    assert times[0] == datetime(2026, 8, 15, 23, 30)
    assert times[1] == datetime(2026, 8, 16, 11, 30)
    assert times[2] == datetime(2026, 8, 16, 23, 30)


def test_end_before_start_is_rejected():
    with pytest.raises(ValueError):
        generate_dose_times(date(2026, 8, 20), date(2026, 8, 15), time(10, 0), 8)


def test_zero_frequency_is_rejected():
    with pytest.raises(ValueError):
        generate_dose_times(date(2026, 8, 15), date(2026, 8, 20), time(10, 0), 0)


def test_wall_clock_is_preserved_across_a_dst_change():
    """Mexico/US style DST weekend: the 10:00 dose stays at 10:00.

    Because the app stores naive local datetimes and steps by whole hours in
    that same wall-clock space, no dose drifts by an hour.
    """
    times = generate_dose_times(date(2026, 3, 7), date(2026, 3, 10), time(10, 0), 24)
    assert {moment.time() for moment in times} == {time(10, 0)}
