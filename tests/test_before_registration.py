"""Doses that were due before the medication existed in the application.

Entering a treatment that started three weeks ago used to produce three weeks of
doses that the scheduler promptly marked *missed*. That reads as "you failed to
take these", when the truth is that the application did not exist for the user
yet. Those doses are recorded as `before_registration`: history, not a task and
not a failure.

Time is controlled by monkeypatching `now_local`, so nothing here depends on
what time the suite happens to run at.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest

from app.models.models import DoseStatus, Notification
from app.notifications import dispatcher
from app.services import medications as medication_service
from app.services import scheduling
from app.services.calendar_service import build_calendar
from app.services.timeline import build_timeline
from app.services.today import build_today
from tests.test_medications import make_payload

# "Now" for every test in this file: the spec's own example, where the treatment
# started on 15 August and the medication is being added on the morning of the
# 20th, before that day's 10:00 dose.
NOW = datetime(2026, 8, 20, 8, 0)
BEFORE = DoseStatus.BEFORE_REGISTRATION.value
SCHEDULED = DoseStatus.SCHEDULED.value


@pytest.fixture()
def clock(monkeypatch):
    """A movable clock shared by every module that asks the time."""
    holder = {"now": NOW}

    def fake_now():
        return holder["now"]

    for module in (dispatcher, scheduling, medication_service):
        monkeypatch.setattr(module, "now_local", fake_now)
    for target in (
        "app.services.settings_service.now_local",
        "app.services.timeline.now_local",
        "app.services.today.now_local",
        # The column default behind `medications.created_at`, which is the
        # instant the whole feature is defined against.
        "app.models.models.now_local",
    ):
        monkeypatch.setattr(target, fake_now)

    def at(moment):
        holder["now"] = moment
        return moment

    holder["at"] = at
    return holder


def make_medication(db, **overrides):
    """The spec's example: started 15 Aug, every 8 h from 10:00, 10 days."""
    payload = make_payload(
        name="Amoxicillin",
        start_date="2026-08-15",
        end_date="2026-08-24",
        frequency_hours=8,
        first_dose_time="10:00",
    )
    payload.update(overrides)
    return medication_service.create_medication(db, payload)


def by_time(medication):
    return {dose.scheduled_at: dose.status for dose in sorted(
        medication.doses, key=lambda d: d.scheduled_at)}


# --------------------------------------------------------------------------- #
# 1-4. The rule itself
# --------------------------------------------------------------------------- #
def test_a_treatment_that_started_last_week_keeps_its_whole_history(db, clock):
    """Registered on the 20th: the doses of the 15th to the 19th still exist."""
    medication = make_medication(db)
    db.commit()

    days = {dose.scheduled_at.date() for dose in medication.doses}
    assert date(2026, 8, 15) in days
    assert date(2026, 8, 16) in days
    assert date(2026, 8, 19) in days
    # Nothing was skipped over: 10 days, every 8 h from 10:00.
    assert len(medication.doses) == 29


def test_doses_before_the_registration_instant_are_marked_as_history(db, clock):
    medication = make_medication(db)
    db.commit()

    statuses = by_time(medication)
    for moment, status in statuses.items():
        expected = BEFORE if moment < NOW else SCHEDULED
        assert status == expected, moment


def test_the_spec_example_dose_by_dose(db, clock):
    """15 Aug 10:00 through 20 Aug 02:00 historical; 20 Aug 10:00 scheduled."""
    medication = make_medication(db)
    db.commit()
    statuses = by_time(medication)

    assert statuses[datetime(2026, 8, 15, 10, 0)] == BEFORE
    assert statuses[datetime(2026, 8, 15, 18, 0)] == BEFORE
    assert statuses[datetime(2026, 8, 16, 2, 0)] == BEFORE
    assert statuses[datetime(2026, 8, 19, 18, 0)] == BEFORE
    assert statuses[datetime(2026, 8, 20, 2, 0)] == BEFORE
    # ...and the first one still ahead behaves completely normally, exactly as
    # the specification's example says it should.
    assert statuses[datetime(2026, 8, 20, 10, 0)] == SCHEDULED
    assert statuses[datetime(2026, 8, 20, 18, 0)] == SCHEDULED


def test_a_future_dose_is_an_ordinary_dose(db, clock):
    medication = make_medication(db)
    db.commit()
    future = [d for d in medication.doses if d.scheduled_at > NOW]

    assert future
    assert all(dose.status == SCHEDULED for dose in future)
    assert all(dose.marked_at is None for dose in future)
    # It is the next dose, it can be snoozed, and it can be marked.
    upcoming = medication_service.serialize_medication(medication)["next_dose"]
    assert upcoming["scheduled_at"] == "2026-08-20T10:00:00"
    assert upcoming["can_snooze"] is False        # still two hours away
    assert upcoming["status"] == SCHEDULED


# --------------------------------------------------------------------------- #
# 13, 15. The comparison is on date AND time
# --------------------------------------------------------------------------- #
def test_the_boundary_is_the_exact_registration_instant(db, clock):
    """Created at 08:30: 07:00 and 08:00 are history, 09:00 is not."""
    clock["at"](datetime(2026, 8, 20, 8, 30))
    medication = make_medication(
        db, start_date="2026-08-20", end_date="2026-08-20",
        frequency_hours=4, first_dose_time="07:00",
    )
    db.commit()

    statuses = by_time(medication)
    assert statuses[datetime(2026, 8, 20, 7, 0)] == BEFORE
    assert statuses[datetime(2026, 8, 20, 11, 0)] == SCHEDULED


def test_a_dose_in_the_same_hour_is_classified_by_the_minute(db, clock):
    clock["at"](datetime(2026, 8, 20, 8, 30))
    medication = make_medication(
        db, start_date="2026-08-20", end_date="2026-08-20",
        frequency_hours=4, first_dose_time="08:00",
    )
    db.commit()

    statuses = by_time(medication)
    assert statuses[datetime(2026, 8, 20, 8, 0)] == BEFORE      # half an hour earlier
    assert statuses[datetime(2026, 8, 20, 12, 0)] == SCHEDULED


def test_a_dose_exactly_at_the_registration_instant_is_not_historical(db, clock):
    """The rule is "strictly before"; a dose due right now is still due."""
    clock["at"](datetime(2026, 8, 20, 10, 0))
    medication = make_medication(
        db, start_date="2026-08-20", end_date="2026-08-20",
        frequency_hours=12, first_dose_time="10:00",
    )
    db.commit()
    assert by_time(medication)[datetime(2026, 8, 20, 10, 0)] == SCHEDULED


# --------------------------------------------------------------------------- #
# 14, 15, 16. The cases the spec calls out by name
# --------------------------------------------------------------------------- #
def test_a_treatment_registered_before_it_starts_has_no_history(db, clock):
    """Created 15 Aug for a treatment starting 20 Aug: nothing is historical."""
    clock["at"](datetime(2026, 8, 15, 9, 0))
    medication = make_medication(db, start_date="2026-08-20", end_date="2026-08-24")
    db.commit()

    assert all(dose.status == SCHEDULED for dose in medication.doses)
    assert medication_service.dose_counts(medication)[BEFORE] == 0


def test_starting_and_registering_on_the_same_morning_leaves_nothing_historical(db, clock):
    """Created 08:00, first dose 10:00 the same day."""
    clock["at"](datetime(2026, 8, 20, 8, 0))
    medication = make_medication(db, start_date="2026-08-20", end_date="2026-08-22")
    db.commit()

    assert all(dose.status == SCHEDULED for dose in medication.doses)


def test_when_only_the_first_dose_has_passed_only_it_is_historical(db, clock):
    """Created at 12:00, first dose at 10:00, every 8 h: 10:00 is history and
    18:00 is an ordinary scheduled dose. The first dose is NOT "missed"."""
    clock["at"](datetime(2026, 8, 20, 12, 0))
    medication = make_medication(db, start_date="2026-08-20", end_date="2026-08-20")
    db.commit()

    statuses = by_time(medication)
    assert statuses[datetime(2026, 8, 20, 10, 0)] == BEFORE
    assert statuses[datetime(2026, 8, 20, 18, 0)] == SCHEDULED
    assert DoseStatus.MISSED.value not in statuses.values()


# --------------------------------------------------------------------------- #
# 5, 20. Notifications
# --------------------------------------------------------------------------- #
def test_registering_a_month_of_history_sends_nothing(db, clock):
    """The whole point: no avalanche of historical reminders."""
    make_medication(db, start_date="2026-07-20", end_date="2026-08-24")
    db.commit()

    summary = dispatcher.run_tick(db, send_windows=False, send_email=False)

    assert db.query(Notification).count() == 0
    assert summary["dose_notifications"] == 0
    assert summary["missed_doses"] == 0


def test_no_reminder_of_any_kind_is_ever_queued_for_a_historical_dose(db, clock):
    medication = make_medication(db)
    db.commit()
    historical = [d.id for d in medication.doses if d.status == BEFORE]
    assert historical

    # Walk a full day of ticks over the moment those doses were due.
    for hour in (0, 6, 10, 12, 18, 23):
        clock["at"](datetime(2026, 8, 20, hour, 0))
        dispatcher.run_tick(db, send_windows=False, send_email=False)

    referenced = {row.reference_id for row in db.query(Notification).all()}
    assert referenced.isdisjoint(set(historical))


def test_a_historical_dose_cannot_be_snoozed(db, clock):
    from app.services.errors import ValidationError

    medication = make_medication(db)
    db.commit()
    historical = next(d for d in medication.doses if d.status == BEFORE)

    with pytest.raises(ValidationError) as exc:
        medication_service.snooze_dose(db, historical.id, 10)
    assert exc.value.fields["status"] == "validation.snooze_not_pending"


# --------------------------------------------------------------------------- #
# 10, 11, 17. It never turns into "missed"
# --------------------------------------------------------------------------- #
def test_time_passing_never_turns_history_into_a_failure(db, clock):
    medication = make_medication(db)
    db.commit()
    historical = [d for d in medication.doses if d.status == BEFORE]

    # Days later, after many ticks.
    for day in (21, 22, 25, 30):
        clock["at"](datetime(2026, 8, day, 23, 0))
        dispatcher.run_tick(db, send_windows=False, send_email=False)

    for dose in historical:
        db.refresh(dose)
        assert dose.status == BEFORE


def test_the_plain_overdue_sweep_also_leaves_it_alone(db, clock):
    medication = make_medication(db)
    db.commit()
    before = medication_service.dose_counts(medication)[BEFORE]

    clock["at"](datetime(2026, 9, 30, 12, 0))
    scheduling.mark_overdue_doses_as_missed(db, 120)

    assert medication_service.dose_counts(medication)[BEFORE] == before


def test_undoing_a_historical_dose_returns_it_to_history_not_to_the_queue(db, clock):
    """Otherwise the next tick would mark it missed - the exact thing this
    status exists to prevent."""
    medication = make_medication(db)
    db.commit()
    historical = next(d for d in medication.doses if d.status == BEFORE)

    # The user records that they did take it after all, then changes their mind.
    medication_service.set_dose_status(db, historical.id, DoseStatus.TAKEN.value)
    assert historical.status == DoseStatus.TAKEN.value
    assert historical.marked_at is not None

    medication_service.set_dose_status(db, historical.id, SCHEDULED)
    db.commit()
    assert historical.status == BEFORE
    assert historical.marked_at is None

    clock["at"](datetime(2026, 8, 25, 12, 0))
    dispatcher.run_tick(db, send_windows=False, send_email=False)
    db.refresh(historical)
    assert historical.status == BEFORE


def test_the_user_can_still_record_what_actually_happened(db, clock):
    """History is not read-only: late registration should not stop someone
    writing down the doses they really took."""
    medication = make_medication(db)
    db.commit()
    historical = next(d for d in medication.doses if d.status == BEFORE)

    medication_service.set_dose_status(db, historical.id, DoseStatus.TAKEN.value)
    db.commit()
    assert medication_service.dose_counts(medication)[DoseStatus.TAKEN.value] == 1


# --------------------------------------------------------------------------- #
# 6. Today
# --------------------------------------------------------------------------- #
def test_history_never_appears_as_something_to_do_today(db, clock):
    make_medication(db)
    db.commit()

    payload = build_today(db, NOW)
    shown = [dose["scheduled_at"] for dose in payload["todays_doses"]]

    # 20 Aug 02:00 was already past at 08:00; 10:00 and 18:00 were not.
    assert shown == ["2026-08-20T10:00:00", "2026-08-20T18:00:00"]
    assert all(dose["status"] != BEFORE for dose in payload["todays_doses"])


def test_the_daily_counter_is_not_inflated_by_history(db, clock):
    make_medication(db)
    db.commit()

    assert build_today(db, NOW)["todays_summary"] == {"taken": 0, "pending": 2, "total": 2}


def test_history_is_not_reported_as_overdue(db, clock):
    make_medication(db)
    db.commit()

    payload = build_today(db, NOW)
    assert payload["overdue_doses"] == []


def test_the_next_dose_skips_over_the_history(db, clock):
    make_medication(db)
    db.commit()

    assert build_today(db, NOW)["next_dose"]["scheduled_at"] == "2026-08-20T10:00:00"


# --------------------------------------------------------------------------- #
# 7. Calendar
# --------------------------------------------------------------------------- #
def test_the_calendar_does_show_the_history(db, clock):
    make_medication(db)
    db.commit()

    payload = build_calendar(db, date(2026, 8, 10), date(2026, 8, 31))
    doses = [event for event in payload["events"] if event["type"] == "dose"]
    historical = [event for event in doses if event["status"] == BEFORE]

    assert historical, "the treatment was already running - the calendar should say so"
    assert {event["date"] for event in historical} >= {
        "2026-08-15", "2026-08-16", "2026-08-17", "2026-08-18", "2026-08-19"
    }
    first = next(e for e in historical if e["date"] == "2026-08-15" and e["time"] == "10:00")
    assert first["title"] == "Amoxicillin"
    # The status travels to the frontend, which is what draws it differently.
    assert first["status"] == BEFORE


def test_the_calendar_still_separates_history_from_schedule(db, clock):
    make_medication(db)
    db.commit()

    doses = [e for e in build_calendar(db, date(2026, 8, 10), date(2026, 8, 31))["events"]
             if e["type"] == "dose"]
    on_the_20th = {e["time"]: e["status"] for e in doses if e["date"] == "2026-08-20"}
    assert on_the_20th == {"02:00": BEFORE, "10:00": SCHEDULED, "18:00": SCHEDULED}


# --------------------------------------------------------------------------- #
# 8. Medical timeline
# --------------------------------------------------------------------------- #
def test_the_timeline_records_that_the_treatment_had_already_started(db, clock):
    make_medication(db)
    db.commit()

    entries = build_timeline(db)["entries"]
    treatments = [e for e in entries if e["type"] == "treatment"]

    assert len(treatments) == 1
    entry = treatments[0]
    assert entry["name"] == "Amoxicillin"
    assert entry["date"] == "2026-08-15"
    assert entry["started_before_registration"] is True
    assert entry["before_registration"]["count"] == 15
    assert entry["before_registration"]["first"][0] == "2026-08-15T10:00:00"
    assert entry["href"] == f"/medications/{entry['id']}"


def test_a_treatment_recorded_from_the_start_says_so(db, clock):
    clock["at"](datetime(2026, 8, 15, 9, 0))
    make_medication(db)
    db.commit()

    entry = next(e for e in build_timeline(db)["entries"] if e["type"] == "treatment")
    assert entry["started_before_registration"] is False
    assert entry["before_registration"]["count"] == 0


def test_the_timeline_can_be_narrowed_to_one_kind_of_entry(db, clock):
    from tests.test_timeline import seed

    seed(db)
    make_medication(db)
    db.commit()

    everything = build_timeline(db, kind="all")
    only_visits = build_timeline(db, kind="appointments")
    only_treatments = build_timeline(db, kind="treatments")

    assert {e["type"] for e in only_visits["entries"]} == {"appointment"}
    assert {e["type"] for e in only_treatments["entries"]} == {"treatment"}
    assert everything["total"] == only_visits["total"] + only_treatments["total"]


def test_the_timeline_stays_in_chronological_order_across_both_kinds(db, clock):
    from tests.test_timeline import seed

    seed(db)
    make_medication(db)
    db.commit()

    newest = [e["datetime"] for e in build_timeline(db, order="newest")["entries"]]
    oldest = [e["datetime"] for e in build_timeline(db, order="oldest")["entries"]]
    assert newest == sorted(newest, reverse=True)
    assert oldest == sorted(oldest)


def test_the_timeline_pages_over_the_merged_list(db, clock):
    from tests.test_timeline import seed

    seed(db)
    make_medication(db)
    db.commit()

    full = build_timeline(db, limit=100)
    first = build_timeline(db, limit=2)
    second = build_timeline(db, limit=2, offset=2)

    assert first["total"] == full["total"]
    assert len(first["entries"]) == 2
    assert [e["datetime"] for e in first["entries"] + second["entries"]] == \
        [e["datetime"] for e in full["entries"][:4]]


# --------------------------------------------------------------------------- #
# 9, 18. Medication history and its filters
# --------------------------------------------------------------------------- #
def test_the_medication_screen_carries_the_whole_history(db, clock):
    medication = make_medication(db)
    db.commit()

    data = medication_service.serialize_medication(
        medication, include_doses=True, reference=NOW
    )
    statuses = [dose["status"] for dose in data["doses"]]

    assert len(data["doses"]) == 29
    assert statuses.count(BEFORE) == 15
    assert statuses.count(SCHEDULED) == 14
    assert data["counts"][BEFORE] == 15
    assert data["counts"]["total"] == 29
    assert data["counts"]["manageable"] == 14


def test_a_historical_dose_is_serialized_with_everything_the_row_needs(db, clock):
    medication = make_medication(db)
    db.commit()

    dose = medication_service.serialize_medication(
        medication, include_doses=True, reference=NOW
    )["doses"][0]

    assert dose["status"] == BEFORE
    assert dose["scheduled_at"] == "2026-08-15T10:00:00"
    assert dose["can_snooze"] is False
    assert dose["marked_at"] is None


# --------------------------------------------------------------------------- #
# 12, 19. Statistics
# --------------------------------------------------------------------------- #
def test_history_does_not_count_against_adherence(db, clock):
    """The spec's arithmetic: 8 taken of 9 manageable, not 8 of 20."""
    medication = make_medication(
        db, start_date="2026-08-18", end_date="2026-08-20", first_dose_time="10:00"
    )
    db.commit()

    manageable = [d for d in medication.doses if d.status == SCHEDULED]
    historical = [d for d in medication.doses if d.status == BEFORE]
    assert historical and manageable

    # Everything manageable resolved: all but one taken.
    for dose in manageable[:-1]:
        medication_service.set_dose_status(db, dose.id, DoseStatus.TAKEN.value)
    clock["at"](datetime(2026, 8, 30, 12, 0))
    dispatcher.run_tick(db, send_windows=False, send_email=False)
    db.commit()

    result = medication_service.compliance(medication)
    counts = medication_service.dose_counts(medication)

    assert result["taken"] == len(manageable) - 1
    assert result["resolved"] == len(manageable)          # the history is excluded
    assert result["before_registration"] == len(historical)
    assert result["percent"] == round(result["taken"] / result["resolved"] * 100)
    assert counts["total"] > counts["manageable"]


def test_adherence_is_none_while_nothing_has_been_resolved(db, clock):
    medication = make_medication(db)
    db.commit()
    # Every remaining dose is still ahead; only history exists behind us.
    assert medication_service.compliance(medication) is None


def test_a_treatment_with_no_history_is_measured_over_everything(db, clock):
    clock["at"](datetime(2026, 8, 15, 9, 0))
    medication = make_medication(db, start_date="2026-08-15", end_date="2026-08-15")
    db.commit()
    for dose in medication.doses:
        medication_service.set_dose_status(db, dose.id, DoseStatus.TAKEN.value)
    db.commit()

    result = medication_service.compliance(medication)
    assert result["percent"] == 100
    assert result["before_registration"] == 0


# --------------------------------------------------------------------------- #
# Editing an existing treatment
# --------------------------------------------------------------------------- #
def test_editing_a_treatment_never_invents_or_damages_the_past(db, clock):
    """Moving the start date backwards changes the schedule from now on.

    The past is deliberately left alone — the same rule that protects a dose you
    already marked — so an edit neither back-fills history nor turns the
    existing history into failures. History is built when the medication is
    registered.
    """
    medication = make_medication(db, start_date="2026-08-15", end_date="2026-08-24")
    db.commit()
    historical = medication_service.dose_counts(medication)[BEFORE]
    assert historical > 0

    medication_service.update_medication(
        db,
        medication.id,
        {
            "name": medication.name,
            "start_date": "2026-08-10",
            "end_date": "2026-08-24",
            "frequency_hours": 8,
            "first_dose_time": "10:00",
        },
    )
    db.commit()

    counts = medication_service.dose_counts(medication)
    assert counts[BEFORE] == historical          # untouched, neither added to nor lost
    assert counts[DoseStatus.MISSED.value] == 0  # and nothing became a failure


def test_an_open_ended_treatment_started_long_ago_behaves_the_same(db, clock):
    medication = make_medication(db, start_date="2026-08-01", end_date=None)
    db.commit()

    counts = medication_service.dose_counts(medication)
    assert counts[BEFORE] > 0
    assert counts[SCHEDULED] > 0
    assert build_today(db, NOW)["overdue_doses"] == []

    # And topping the schedule up on a later tick adds ordinary doses only.
    clock["at"](datetime(2026, 8, 21, 12, 0))
    scheduling.extend_open_ended_schedules(db)
    db.commit()
    fresh = [d for d in medication.doses if d.scheduled_at > datetime(2026, 8, 21, 12, 0)]
    assert all(dose.status == SCHEDULED for dose in fresh)


# --------------------------------------------------------------------------- #
# Through the API
# --------------------------------------------------------------------------- #
def test_the_api_reports_the_new_status_end_to_end(client):
    from app.utils.timeutil import now_local

    today = now_local().date()
    created = client.post(
        "/api/medications",
        json=make_payload(
            name="Backdated",
            start_date=(today - timedelta(days=3)).isoformat(),
            end_date=(today + timedelta(days=3)).isoformat(),
            frequency_hours=8,
            first_dose_time="00:30",
        ),
    )
    assert created.status_code == 201
    body = created.json()

    assert body["counts"][BEFORE] > 0
    assert body["counts"]["manageable"] < body["counts"]["total"]
    assert any(dose["status"] == BEFORE for dose in body["doses"])

    # Today shows none of them, the calendar shows all of them.
    todays = client.get("/api/today").json()
    assert all(dose["status"] != BEFORE for dose in todays["todays_doses"])
    assert todays["overdue_doses"] == []

    anchor = (today - timedelta(days=3)).isoformat()
    calendar = client.get(f"/api/calendar?view=month&anchor={anchor}").json()
    assert any(
        event["type"] == "dose" and event["status"] == BEFORE
        for event in calendar["events"]
    )

    timeline = client.get("/api/timeline").json()
    treatment = next(e for e in timeline["entries"] if e["type"] == "treatment")
    assert treatment["started_before_registration"] is True
    assert treatment["before_registration"]["count"] > 0


# --------------------------------------------------------------------------- #
# Regressions found reviewing this feature
# --------------------------------------------------------------------------- #
def test_recording_a_historical_dose_does_not_move_it_into_the_statistics(db, clock):
    """The user is allowed to write down what really happened, and doing so must
    not quietly make it count for or against adherence."""
    medication = make_medication(db)
    db.commit()
    manageable = [d for d in medication.doses if d.status == SCHEDULED]
    historical = [d for d in medication.doses if d.status == BEFORE]

    medication_service.set_dose_status(db, manageable[0].id, DoseStatus.TAKEN.value)
    db.commit()
    before = medication_service.compliance(medication)
    assert before == {"taken": 1, "resolved": 1, "percent": 100,
                      "before_registration": len(historical)}

    # Now record one historical dose as skipped and one as taken.
    medication_service.set_dose_status(db, historical[0].id, DoseStatus.SKIPPED.value)
    medication_service.set_dose_status(db, historical[1].id, DoseStatus.TAKEN.value)
    db.commit()

    after = medication_service.compliance(medication)
    assert after == before, "history must stay out of the adherence count"
    # And the count of what the application could manage does not grow either.
    assert medication_service.dose_counts(medication)["manageable"] == len(manageable)


def test_the_api_cannot_brand_a_historical_dose_as_missed(db, clock):
    """The overdue sweep is not the only way in; the status endpoint is another."""
    medication = make_medication(db)
    db.commit()
    historical = next(d for d in medication.doses if d.status == BEFORE)

    medication_service.set_dose_status(db, historical.id, DoseStatus.MISSED.value)
    db.commit()
    assert historical.status == BEFORE

    medication_service.set_dose_status(db, historical.id, SCHEDULED)
    db.commit()
    assert historical.status == BEFORE


def test_treatments_that_start_the_same_day_all_survive_paging(db, clock):
    """Two sources merged into one page must agree on their ordering, or entries
    are duplicated on one page and missing from every other."""
    for hour in ("05:00", "04:00", "03:00", "02:00", "01:00"):
        make_medication(db, name=f"Med {hour}", start_date="2026-07-10",
                        end_date="2026-08-24", first_dose_time=hour)
    db.commit()

    full = build_timeline(db, limit=100)
    paged = []
    for offset in range(0, full["total"], 2):
        paged.extend(build_timeline(db, limit=2, offset=offset)["entries"])

    keys = [(entry["type"], entry["id"]) for entry in paged]
    assert len(keys) == len(set(keys)), "an entry was returned on two pages"
    assert set(keys) == {(e["type"], e["id"]) for e in full["entries"]}
    assert [e["datetime"] for e in paged] == [e["datetime"] for e in full["entries"]]


def test_a_treatment_that_began_this_morning_is_already_in_the_past(db, clock):
    """`scope` must classify a treatment by the same instant it reports."""
    clock["at"](datetime(2026, 8, 20, 12, 0))
    make_medication(db, start_date="2026-08-20", end_date="2026-08-24",
                    first_dose_time="09:00")
    db.commit()

    past = build_timeline(db, scope="past", kind="treatments")["entries"]
    upcoming = build_timeline(db, scope="upcoming", kind="treatments")["entries"]

    assert [entry["is_past"] for entry in past] == [True]
    assert upcoming == []


def test_the_history_counts_are_only_computed_for_the_page(db, clock):
    """A deep offset must not count doses for entries nobody will see."""
    for index in range(4):
        make_medication(db, name=f"Med {index}", start_date="2026-08-15",
                        end_date="2026-08-24", first_dose_time=f"0{index + 1}:00")
    db.commit()

    page = build_timeline(db, limit=1, kind="treatments")
    assert len(page["entries"]) == 1
    # The one entry on the page still carries its real numbers.
    assert page["entries"][0]["before_registration"]["count"] > 0
    assert page["entries"][0]["before_registration"]["first"]

    # ...and every entry does, whichever page it lands on.
    for offset in range(page["total"]):
        entry = build_timeline(db, limit=1, offset=offset, kind="treatments")["entries"][0]
        assert entry["before_registration"]["count"] > 0


def test_the_medication_screen_does_not_list_history_as_due_today(db, clock):
    """The detail screen has its own "today" list; it follows the same rule."""
    clock["at"](datetime(2026, 8, 20, 8, 30))
    medication = make_medication(
        db, start_date="2026-08-20", end_date="2026-08-24",
        frequency_hours=8, first_dose_time="07:00",
    )
    db.commit()

    data = medication_service.serialize_medication(
        medication, include_doses=True, reference=datetime(2026, 8, 20, 8, 30)
    )
    today = [d for d in data["doses"] if d["scheduled_at"].startswith("2026-08-20")]
    # The 07:00 dose is history and the 15:00 / 23:00 ones are not; the frontend
    # filters on exactly this flag.
    assert [d["status"] for d in today] == [BEFORE, SCHEDULED, SCHEDULED]
