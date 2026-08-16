"""One e-mail thread per dose.

Every reminder for a single dose has to land in one conversation, and the next
dose of the same medication has to start a new one. That is done with the real
RFC 5322 headers — `Message-ID`, `In-Reply-To`, `References` — not by hoping a
mail client groups messages by subject, which it must not do here because the
subject deliberately changes from "in 30 minutes" to "time to take it".

Nothing is sent to a real server: `smtplib` is replaced by the recording double
from `tests/test_email.py`, so these tests assert the exact headers an inbox
would receive.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta

import pytest

from app.models.models import DoseStatus, Notification, NotificationType
from app.notifications import dispatcher
from app.services import medications as medication_service
from app.services import scheduling
from app.services.settings_service import update_settings
from tests.test_email import SMTP_SETTINGS, smtp  # noqa: F401  (fixture)
from tests.test_medications import make_payload

# The specification's own example: Ryaltris every 8 hours, first dose 23:58.
DAY = datetime(2026, 8, 15)
FIRST = DAY.replace(hour=23, minute=58)


@pytest.fixture()
def clock(monkeypatch):
    """A movable clock shared by every module that asks the time."""
    holder = {"now": FIRST - timedelta(hours=2)}

    def fake_now():
        return holder["now"]

    for module in (dispatcher, scheduling, medication_service):
        monkeypatch.setattr(module, "now_local", fake_now)
    for target in (
        "app.services.settings_service.now_local",
        "app.models.models.now_local",
    ):
        monkeypatch.setattr(target, fake_now)

    def at(moment):
        holder["now"] = moment
        return moment

    holder["at"] = at
    return holder


@pytest.fixture()
def outbox(db, clock, smtp):  # noqa: F811
    """E-mail on, SMTP faked, and the language pinned so subjects are stable.

    Spanish by default because that is the language the specification's examples
    are written in; the tests about English set it themselves.
    """
    update_settings(db, dict(SMTP_SETTINGS))
    update_settings(db, {"language": "es"})
    db.commit()
    return smtp


def make_medication(db, name="Ryaltris", first_dose_time="23:58", **overrides):
    payload = make_payload(
        name=name,
        dose_amount="1",
        dose_unit="unit",
        quantity=2,
        form="spray",
        comments=None,
        start_date=DAY.date().isoformat(),
        end_date=(DAY.date() + timedelta(days=1)).isoformat(),
        frequency_hours=8,
        first_dose_time=first_dose_time,
    )
    payload.update(overrides)
    return medication_service.create_medication(db, payload)


def tick(db, clock, moment):
    clock["at"](moment)
    return dispatcher.run_tick(db, send_windows=False)


def walk_the_thread(db, clock, dose_at=FIRST):
    """Run the whole sequence of a dose, -30 min through +2 h."""
    for offset in (-30, -15, -5, 0, 15, 30, 120):
        tick(db, clock, dose_at + timedelta(minutes=offset))


def headers(message):
    return (
        message["Subject"],
        message["Message-ID"],
        message["In-Reply-To"],
        message["References"],
    )


# --------------------------------------------------------------------------- #
# 1-7. The seven messages of one dose form one conversation
# --------------------------------------------------------------------------- #
def test_the_whole_sequence_is_a_single_thread(db, clock, outbox):
    """The specification's example, message by message."""
    make_medication(db)
    db.commit()
    walk_the_thread(db, clock)

    assert len(outbox.sent) == 7
    root, *replies = outbox.sent

    # The -30 minute message opens the thread: its own id, nothing quoted.
    assert root["Subject"] == "💊 Ryaltris — Toma #1 — en 30 minutos"
    assert root["Message-ID"]
    assert root["In-Reply-To"] is None
    assert root["References"] is None

    # Every later message replies to the one before it and carries the whole
    # chain so far, oldest first.
    chain = [root["Message-ID"]]
    for message in replies:
        assert message["In-Reply-To"] == chain[-1]
        assert message["References"].split() == chain
        chain.append(message["Message-ID"])

    assert [message["Subject"] for message in outbox.sent] == [
        "💊 Ryaltris — Toma #1 — en 30 minutos",
        "💊 Ryaltris — Toma #1 — en 15 minutos",
        "💊 Ryaltris — Toma #1 — en 5 minutos",
        "🔔 Ryaltris — Toma #1 — es hora de tomarla",
        "⚠️ Ryaltris — Toma #1 — dosis pendiente",
        "⚠️ Ryaltris — Toma #1 — pendiente desde hace 30 min",
        "🔴 Ryaltris — Toma #1 — dosis atrasada",
    ]


def test_every_message_id_is_unique(db, clock, outbox):
    make_medication(db)
    db.commit()
    walk_the_thread(db, clock)

    ids = [message["Message-ID"] for message in outbox.sent]
    assert len(ids) == len(set(ids))
    assert all(value.startswith("<") and value.endswith(">") for value in ids)
    # The domain is the sender's, which is what a receiving server expects.
    assert all(value.endswith("@example.com>") for value in ids)


def test_references_grows_by_exactly_one_each_time(db, clock, outbox):
    make_medication(db)
    db.commit()
    walk_the_thread(db, clock)

    lengths = [
        len((message["References"] or "").split()) for message in outbox.sent
    ]
    assert lengths == [0, 1, 2, 3, 4, 5, 6]


def test_the_thread_survives_the_subject_changing(db, clock, outbox):
    """Three different subjects, one conversation — which is the whole reason
    the headers are used instead of matching on the subject line."""
    make_medication(db)
    db.commit()
    walk_the_thread(db, clock)

    subjects = {message["Subject"] for message in outbox.sent}
    roots = {
        (message["References"] or message["Message-ID"]).split()[0]
        for message in outbox.sent
    }
    assert len(subjects) == 7      # every message says something different
    assert len(roots) == 1         # and they all hang off the same first one


# --------------------------------------------------------------------------- #
# 8, 9. A different dose, and a different medication, are different threads
# --------------------------------------------------------------------------- #
def test_the_next_dose_of_the_same_medication_starts_a_new_thread(db, clock, outbox):
    make_medication(db)
    db.commit()

    walk_the_thread(db, clock, FIRST)                       # dose #1 at 23:58
    first_thread = list(outbox.sent)
    outbox.sent = []
    walk_the_thread(db, clock, FIRST + timedelta(hours=8))  # dose #2 at 07:58

    second_thread = outbox.sent
    assert second_thread
    assert second_thread[0]["Subject"] == "💊 Ryaltris — Toma #2 — en 30 minutos"
    assert second_thread[0]["In-Reply-To"] is None
    assert second_thread[0]["References"] is None

    ids_one = {m["Message-ID"] for m in first_thread}
    ids_two = {m["Message-ID"] for m in second_thread}
    assert ids_one.isdisjoint(ids_two)
    # Nothing from the first conversation is quoted in the second.
    quoted = set()
    for message in second_thread:
        quoted |= set((message["References"] or "").split())
    assert quoted.isdisjoint(ids_one)


def test_another_medication_starts_a_new_thread(db, clock, outbox):
    make_medication(db, name="Ryaltris")
    make_medication(db, name="Amoxicillin")
    db.commit()

    walk_the_thread(db, clock)

    by_medication = {}
    for message in outbox.sent:
        name = message["Subject"].split("—")[0].split(" ", 1)[1].strip()
        by_medication.setdefault(name, []).append(message)

    assert set(by_medication) == {"Ryaltris", "Amoxicillin"}
    for messages in by_medication.values():
        assert messages[0]["In-Reply-To"] is None      # each opens its own
    quoted_ryaltris = {
        ref
        for message in by_medication["Ryaltris"]
        for ref in (message["References"] or "").split()
    }
    ids_amoxicillin = {m["Message-ID"] for m in by_medication["Amoxicillin"]}
    assert quoted_ryaltris.isdisjoint(ids_amoxicillin)


def test_two_medications_at_the_same_minute_do_not_share_a_thread(db, clock, outbox):
    """The worst case for threading: same time, same tick, same inbox."""
    make_medication(db, name="Ryaltris")
    make_medication(db, name="Amoxicillin")
    db.commit()
    tick(db, clock, FIRST - timedelta(minutes=30))

    assert len(outbox.sent) == 2
    first, second = outbox.sent
    assert first["Message-ID"] != second["Message-ID"]
    assert first["In-Reply-To"] is None and second["In-Reply-To"] is None


# --------------------------------------------------------------------------- #
# 13-16. Marking the dose stops the rest of the conversation
# --------------------------------------------------------------------------- #
def test_marking_it_taken_cancels_every_later_email(db, clock, outbox):
    """The specification's example: two sent, taken at 23:50, nothing after."""
    medication = make_medication(db)
    db.commit()
    dose = medication.doses[0]

    tick(db, clock, FIRST - timedelta(minutes=30))
    tick(db, clock, FIRST - timedelta(minutes=15))
    assert len(outbox.sent) == 2

    clock["at"](FIRST - timedelta(minutes=8))
    medication_service.set_dose_status(db, dose.id, DoseStatus.TAKEN.value)
    db.commit()

    for offset in (-5, 0, 15, 30, 120):
        tick(db, clock, FIRST + timedelta(minutes=offset))

    assert len(outbox.sent) == 2, "no e-mail may follow a dose the user resolved"
    assert dose.status == DoseStatus.TAKEN.value


def test_marking_it_skipped_cancels_every_later_email(db, clock, outbox):
    medication = make_medication(db)
    db.commit()
    dose = medication.doses[0]

    tick(db, clock, FIRST - timedelta(minutes=30))
    clock["at"](FIRST - timedelta(minutes=20))
    medication_service.set_dose_status(db, dose.id, DoseStatus.SKIPPED.value)
    db.commit()

    walk_the_thread(db, clock)
    assert len(outbox.sent) == 1


def test_a_taken_dose_never_receives_the_overdue_email(db, clock, outbox):
    medication = make_medication(db)
    db.commit()
    dose = medication.doses[0]

    clock["at"](FIRST)
    medication_service.set_dose_status(db, dose.id, DoseStatus.TAKEN.value)
    db.commit()

    tick(db, clock, FIRST + timedelta(hours=2))
    tick(db, clock, FIRST + timedelta(hours=5))

    assert not any("atrasada" in m["Subject"] for m in outbox.sent)
    assert dose.status == DoseStatus.TAKEN.value


def test_a_skipped_dose_never_receives_the_overdue_email_and_stays_skipped(
    db, clock, outbox
):
    medication = make_medication(db)
    db.commit()
    dose = medication.doses[0]

    clock["at"](FIRST)
    medication_service.set_dose_status(db, dose.id, DoseStatus.SKIPPED.value)
    db.commit()

    tick(db, clock, FIRST + timedelta(hours=3))

    assert not any("atrasada" in m["Subject"] for m in outbox.sent)
    assert dose.status == DoseStatus.SKIPPED.value      # never becomes overdue


def test_the_dose_status_is_checked_at_the_moment_of_sending(db, clock, outbox):
    """Not when the reminder was queued: the queue can be minutes old.

    Here the reminder is queued and committed, and only then is the dose
    resolved behind the sender's back — the state at send time is the one that
    decides.
    """
    medication = make_medication(db)
    db.commit()
    dose = medication.doses[0]

    clock["at"](FIRST)
    dispatcher.run_tick(db, send_windows=False, send_email=False)   # queue only
    queued = db.query(Notification).filter(
        Notification.reference_id == dose.id,
        Notification.email_sent_at.is_(None),
    ).count()
    assert queued

    dose.status = DoseStatus.TAKEN.value      # resolved directly, no cancelling
    db.commit()

    dispatcher.run_tick(db, send_windows=False)
    assert outbox.sent == []


def test_the_taken_time_is_kept_alongside_the_scheduled_time(db, clock, outbox):
    medication = make_medication(db)
    db.commit()
    dose = medication.doses[0]

    clock["at"](FIRST + timedelta(minutes=9))
    medication_service.set_dose_status(db, dose.id, DoseStatus.TAKEN.value)
    db.commit()

    assert dose.scheduled_at == FIRST                       # 23:58, unchanged
    assert dose.marked_at == FIRST + timedelta(minutes=9)   # 00:07


# --------------------------------------------------------------------------- #
# 17, 18. Language and localisation
# --------------------------------------------------------------------------- #
def test_the_whole_email_follows_the_chosen_language(db, clock, outbox):
    update_settings(db, {"language": "en"})
    make_medication(db)
    db.commit()

    walk_the_thread(db, clock)
    subjects = [m["Subject"] for m in outbox.sent]
    bodies = [m.get_content() for m in outbox.sent]

    assert subjects[0] == "💊 Ryaltris — Dose #1 — in 30 minutes"
    assert subjects[-1] == "🔴 Ryaltris — Dose #1 — dose overdue"
    assert "Medication reminder" in bodies[0]
    assert "Status:" in bodies[0] and "Upcoming" in bodies[0]
    assert "Dose overdue" in bodies[-1] and "Overdue" in bodies[-1]
    assert all("Personal reminder tool — not medical advice." in b for b in bodies)
    # Not one word of the other language leaked through.
    assert not any("Dosis" in b or "Toma" in b for b in bodies)


def test_the_spanish_email_is_entirely_in_spanish(db, clock, outbox):
    update_settings(db, {"language": "es"})
    make_medication(db)
    db.commit()

    walk_the_thread(db, clock)
    bodies = [m.get_content() for m in outbox.sent]

    assert "Recordatorio de medicamento" in bodies[0]
    assert "Estado:" in bodies[0] and "Próxima" in bodies[0]
    assert "Dosis atrasada" in bodies[-1] and "Atrasada" in bodies[-1]
    assert "2 horas" in bodies[-1]      # the real gap, read off the row
    assert all("Herramienta personal de recordatorios" in b for b in bodies)
    assert not any("Status:" in b or "Dose #" in b for b in bodies)


def test_dates_and_times_use_the_locale_of_the_language(db, clock, outbox):
    make_medication(db, first_dose_time="23:58")
    db.commit()

    update_settings(db, {"language": "es"})
    db.commit()
    tick(db, clock, FIRST - timedelta(minutes=30))
    spanish = outbox.sent[-1].get_content()
    assert "15 de agosto de 2026" in spanish
    assert "23:58" in spanish            # the Spanish locale is a 24-hour one

    outbox.sent = []
    update_settings(db, {"language": "en"})
    db.commit()
    tick(db, clock, FIRST - timedelta(minutes=15))
    english = outbox.sent[-1].get_content()
    assert "August 15, 2026" in english
    assert "11:58 PM" in english         # ...and the English one is not


def test_the_elapsed_time_is_spelled_out_for_the_late_reminders(db, clock, outbox):
    update_settings(db, {"language": "en"})
    make_medication(db)
    db.commit()
    walk_the_thread(db, clock)

    bodies = {m["Subject"]: m.get_content() for m in outbox.sent}
    after_15 = bodies["⚠️ Ryaltris — Dose #1 — dose pending"]
    after_30 = bodies["⚠️ Ryaltris — Dose #1 — pending for 30 min"]
    overdue = bodies["🔴 Ryaltris — Dose #1 — dose overdue"]

    assert "Time elapsed:" in after_15 and "15 minutes" in after_15
    assert "30 minutes" in after_30
    assert "2 hours" in overdue


# --------------------------------------------------------------------------- #
# 18-20. The rest of the application is untouched
# --------------------------------------------------------------------------- #
def test_a_failing_mail_server_does_not_stop_the_scheduler(db, clock, outbox, monkeypatch):
    def explode(*_args, **_kwargs):
        raise OSError("the network is down")

    monkeypatch.setattr("smtplib.SMTP", explode)

    medication = make_medication(db)
    db.commit()
    summary = tick(db, clock, FIRST)

    # The tick completed: the dose was still processed and the queue advanced.
    assert summary["emails_sent"] == 0
    assert summary["dose_notifications"] >= 1
    errors = [n.error for n in db.query(Notification).all() if n.error]
    assert errors and "network is down" in errors[0]

    # ...and the next tick still runs.
    assert tick(db, clock, FIRST + timedelta(hours=3))["missed_doses"] >= 1


def test_windows_and_browser_notifications_are_unaffected(db, clock, outbox):
    """All three channels drain the same queue but keep their own bookkeeping.
    Sending the e-mails must not touch either of the others."""
    make_medication(db)
    db.commit()
    tick(db, clock, FIRST)          # e-mail only; Windows is off in this tick

    rows = db.query(Notification).filter(
        Notification.type == NotificationType.DOSE.value
    ).all()
    assert rows and outbox.sent

    # The e-mail pass claimed only its own column.
    assert all(row.email_sent_at is not None for row in rows)
    assert all(row.windows_sent_at is None for row in rows)
    assert all(row.browser_delivered_at is None for row in rows)

    # The browser still sees everything waiting for it, in both languages, and
    # in the short form — not the e-mail's.
    for language in ("en", "es"):
        queued = dispatcher.pending_for_browser(db, language)
        assert len(queued) == len(rows)
        for item in queued:
            assert item["title"] and item["body"]
            assert "Toma #" not in item["title"] and "Dose #" not in item["title"]

    # ...and delivering them is still that channel's own decision.
    dispatcher.mark_browser_delivered(db, [row.id for row in rows])
    db.commit()
    assert dispatcher.pending_for_browser(db, "es") == []
    assert all(row.windows_sent_at is None for row in rows)


def test_the_email_channel_can_be_off_while_the_others_work(db, clock, outbox):
    update_settings(db, {"email_notifications": False})
    make_medication(db)
    db.commit()

    summary = tick(db, clock, FIRST)

    assert outbox.sent == []
    assert summary["dose_notifications"] >= 1
    rows = db.query(Notification).all()
    assert rows and all(row.email_message_id is None for row in rows)


def test_an_appointment_email_is_not_threaded_as_a_dose(db, clock, outbox):
    """Appointments keep their own, unchanged shape."""
    from tests.test_appointments import make_appointment

    make_appointment(db, when=FIRST + timedelta(days=1))
    db.commit()
    tick(db, clock, FIRST + timedelta(hours=21))

    appointment_mail = [m for m in outbox.sent if "Toma #" not in m["Subject"]]
    assert appointment_mail
    assert appointment_mail[0]["In-Reply-To"] is None


# --------------------------------------------------------------------------- #
# The thread is per dose, recorded on the notification row
# --------------------------------------------------------------------------- #
def test_each_reminder_records_the_id_it_was_sent_under(db, clock, outbox):
    make_medication(db)
    db.commit()
    walk_the_thread(db, clock)

    rows = (
        db.query(Notification)
        .filter(Notification.email_message_id.is_not(None))
        .order_by(Notification.fire_at)
        .all()
    )
    assert len(rows) == 7
    assert [row.email_message_id for row in rows] == [
        m["Message-ID"] for m in outbox.sent
    ]


def test_the_thread_is_rebuilt_from_the_database_after_a_restart(db, clock, outbox):
    """The chain lives in the database, not in memory, so stopping the
    application halfway through a dose does not split its conversation."""
    make_medication(db)
    db.commit()

    tick(db, clock, FIRST - timedelta(minutes=30))
    root_id = outbox.sent[0]["Message-ID"]

    # A restart: nothing in memory survives, only the rows.
    db.expire_all()

    tick(db, clock, FIRST)
    latest = outbox.sent[-1]
    # It replies to whatever came immediately before it, and the chain it
    # carries still starts at the message sent before the "restart".
    assert latest["References"].split()[0] == root_id
    assert latest["In-Reply-To"] == outbox.sent[-2]["Message-ID"]


def test_a_snoozed_reminder_stays_in_the_same_conversation(db, clock, outbox):
    medication = make_medication(db)
    db.commit()
    dose = medication.doses[0]

    tick(db, clock, FIRST - timedelta(minutes=30))
    root_id = outbox.sent[0]["Message-ID"]

    clock["at"](FIRST)
    medication_service.snooze_dose(db, dose.id, 10)
    db.commit()

    tick(db, clock, FIRST + timedelta(minutes=10))
    snoozed = [m for m in outbox.sent if "pospuesto" in m["Subject"]]
    assert snoozed
    assert root_id in snoozed[0]["References"].split()


def test_the_dose_number_counts_within_its_own_medication(db, clock, outbox):
    ryaltris = make_medication(db, name="Ryaltris")
    amoxicillin = make_medication(db, name="Amoxicillin")
    db.commit()

    numbers = {
        (dispatcher.dose_number(db, dose), dose.medication.name)
        for dose in list(ryaltris.doses)[:3] + list(amoxicillin.doses)[:3]
    }
    assert numbers == {
        (1, "Ryaltris"), (2, "Ryaltris"), (3, "Ryaltris"),
        (1, "Amoxicillin"), (2, "Amoxicillin"), (3, "Amoxicillin"),
    }


def test_the_overdue_email_states_the_real_delay_not_a_constant(db, clock, outbox):
    """The overdue moment is a setting, so "more than 2 hours" would be a lie
    for anyone who moved it."""
    update_settings(db, {"language": "en", "missed_after_minutes": 300})
    make_medication(db)
    db.commit()

    tick(db, clock, FIRST)
    outbox.sent = []
    tick(db, clock, FIRST + timedelta(minutes=300))

    overdue = [m for m in outbox.sent if "overdue" in m["Subject"]]
    assert overdue
    body = overdue[0].get_content()
    assert "5 hours" in body
    assert "More than 2 hours" not in body


def test_a_line_break_in_a_name_cannot_break_the_tick_or_the_headers(db, clock, outbox):
    """A header cannot hold a line break, and an unhandled ValueError inside the
    send would abort the whole scheduler pass."""
    medication = make_medication(db, name="Ryaltris")
    # Past every form: straight into the column, the way a bad import could.
    medication.name = "Ryaltris\nBcc: someone@example.com"
    db.commit()

    summary = tick(db, clock, FIRST)      # must not raise

    assert summary["dose_notifications"] >= 1
    for message in outbox.sent:
        assert "\n" not in message["Subject"]
        assert message["Bcc"] is None
        assert "Bcc: someone@example.com" in message["Subject"]  # inert text


def test_the_form_will_not_store_a_line_break_in_a_name(db, clock):
    medication = medication_service.create_medication(
        db,
        make_payload(
            name="Ryaltris\nBcc: someone@example.com",
            start_date=DAY.date().isoformat(),
            end_date=DAY.date().isoformat(),
        ),
    )
    assert "\n" not in medication.name
    assert medication.name == "Ryaltris Bcc: someone@example.com"


def test_a_deleted_dose_never_hands_its_thread_to_the_next_one(db, clock, outbox):
    """SQLite reuses row ids, so a notification left pointing at a dead dose id
    would drag an unrelated treatment into its conversation - and its dedupe key
    would suppress that treatment's own reminders."""
    ryaltris = make_medication(db, name="Ryaltris")
    db.commit()
    tick(db, clock, FIRST - timedelta(minutes=30))
    tick(db, clock, FIRST - timedelta(minutes=15))
    assert len(outbox.sent) == 2
    old_ids = {m["Message-ID"] for m in outbox.sent}

    medication_service.delete_medication(db, ryaltris.id)
    db.commit()
    assert db.query(Notification).count() == 0      # they went with the doses

    outbox.sent = []
    amoxicillin = make_medication(db, name="Amoxicillin")
    db.commit()
    # The recycled id is the point of the test.
    assert amoxicillin.doses[0].id == 1

    walk_the_thread(db, clock)

    assert len(outbox.sent) == 7, "the stale dedupe keys must not suppress these"
    assert outbox.sent[0]["In-Reply-To"] is None
    assert outbox.sent[0]["References"] is None
    quoted = {ref for m in outbox.sent for ref in (m["References"] or "").split()}
    assert quoted.isdisjoint(old_ids)
    assert all("Amoxicillin" in m["Subject"] for m in outbox.sent)


def test_a_reminder_queued_before_this_feature_still_gets_its_number(db, clock, outbox):
    """An upgrade mid-dose must not put "Dose #1" in a thread about dose #3."""
    medication = make_medication(db)
    db.commit()
    third = medication.doses[2]

    clock["at"](third.scheduled_at)
    dispatcher.run_tick(db, send_windows=False, send_email=False)      # queue only

    # Strip the number the way a row written by the previous version would be.
    for row in db.query(Notification).filter(Notification.reference_id == third.id):
        payload = json.loads(row.payload)
        payload.pop("dose_number", None)
        row.payload = json.dumps(payload)
    db.commit()

    dispatcher.run_tick(db, send_windows=False)
    assert outbox.sent
    assert all("Toma #3" in m["Subject"] for m in outbox.sent)


def test_a_row_is_only_ever_claimed_once(db, clock, outbox):
    """Two passes racing over the same queue must not both send it."""
    make_medication(db)
    db.commit()
    clock["at"](FIRST)
    dispatcher.run_tick(db, send_windows=False, send_email=False)

    row = db.query(Notification).filter(Notification.email_sent_at.is_(None)).first()
    assert row is not None

    from app.notifications.email import EmailThread, config_from_settings
    from app.services.settings_service import get_settings

    config = config_from_settings(get_settings(db))
    first = dispatcher._claim_for_email(
        db, row, EmailThread(message_id=dispatcher.new_message_id_for(config, "a"))
    )
    second = dispatcher._claim_for_email(
        db, row, EmailThread(message_id=dispatcher.new_message_id_for(config, "b"))
    )
    assert first is True
    assert second is False, "the second pass must not send it again"


def test_a_newer_database_stamp_is_never_lowered(tmp_path):
    """An old build launched once after an upgrade must not invite the new one
    to re-run a migration it has already applied."""
    import sqlite3

    from app.database.migrations import CURRENT_VERSION, run_migrations
    from tests.test_v3_regressions import build_v2_database

    path = build_v2_database(tmp_path / "future.db")
    run_migrations(path)
    connection = sqlite3.connect(str(path))
    connection.execute(f"PRAGMA user_version = {CURRENT_VERSION + 2}")
    connection.commit()
    connection.close()

    report = run_migrations(path)

    assert report["applied"] == []
    connection = sqlite3.connect(str(path))
    try:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == CURRENT_VERSION + 2
    finally:
        connection.close()
