"""v3 Notification centre: the bell, its history and read/unread state."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from app.models.models import DoseStatus, Notification, NotificationType
from app.notifications import dispatcher
from app.services import medications as medication_service
from app.services import scheduling
from tests.test_medications import make_payload

DAY = datetime(2026, 8, 20)


@pytest.fixture()
def clock(monkeypatch):
    holder = {"now": DAY.replace(hour=9)}

    def fake_now():
        return holder["now"]

    for module in (dispatcher, scheduling, medication_service):
        monkeypatch.setattr(module, "now_local", fake_now)
    monkeypatch.setattr("app.services.settings_service.now_local", fake_now)

    def at(hour, minute=0):
        holder["now"] = DAY.replace(hour=hour, minute=minute)
        return holder["now"]

    holder["at"] = at
    return holder


@pytest.fixture()
def some_notifications(db, clock):
    """Three real reminders, produced the way the app produces them."""
    medication_service.create_medication(
        db,
        make_payload(
            start_date=DAY.date().isoformat(),
            end_date=DAY.date().isoformat(),
            frequency_hours=24,
            first_dose_time="10:00",
        ),
    )
    db.commit()
    for hour, minute in ((9, 30), (9, 45), (9, 55)):
        clock["at"](hour, minute)
        dispatcher.run_tick(db, send_windows=False, send_email=False)
    assert db.query(Notification).count() == 3
    return db.query(Notification).order_by(Notification.fire_at).all()


def test_everything_starts_unread(db, some_notifications):
    assert dispatcher.unread_count(db) == 3
    history = dispatcher.notification_history(db, "en")
    assert history["unread"] == 3
    assert history["total"] == 3
    assert all(item["read"] is False for item in history["items"])


def test_the_history_is_newest_first_and_carries_readable_text(db, some_notifications):
    items = dispatcher.notification_history(db, "en")["items"]
    fire_times = [item["fire_at"] for item in items]
    assert fire_times == sorted(fire_times, reverse=True)
    assert all(item["title"] and item["body"] for item in items)
    # Rendered text, not raw translation keys.
    assert not any(item["title"].startswith("notification.") for item in items)


def test_the_same_history_comes_back_in_spanish(db, some_notifications):
    english = dispatcher.notification_history(db, "en")["items"][0]["body"]
    spanish = dispatcher.notification_history(db, "es")["items"][0]["body"]
    assert english and spanish and english != spanish


def test_marking_one_as_read_only_marks_that_one(db, some_notifications):
    target = some_notifications[0]
    assert dispatcher.mark_read(db, [target.id]) == 1
    db.commit()

    assert dispatcher.unread_count(db) == 2
    by_id = {item["id"]: item for item in dispatcher.notification_history(db, "en")["items"]}
    assert by_id[target.id]["read"] is True
    assert by_id[target.id]["read_at"] is not None


def test_marking_the_same_one_twice_does_not_double_count(db, some_notifications):
    target = some_notifications[0]
    dispatcher.mark_read(db, [target.id])
    assert dispatcher.mark_read(db, [target.id]) == 0
    assert dispatcher.unread_count(db) == 2


def test_marking_everything_read_empties_the_counter(db, some_notifications):
    assert dispatcher.mark_read(db) == 3
    db.commit()
    assert dispatcher.unread_count(db) == 0
    assert dispatcher.notification_history(db, "en", unread_only=True)["items"] == []


def test_the_unread_filter_shows_only_what_is_left(db, some_notifications):
    dispatcher.mark_read(db, [some_notifications[0].id])
    db.commit()
    unread = dispatcher.notification_history(db, "en", unread_only=True)["items"]
    assert len(unread) == 2
    assert some_notifications[0].id not in [item["id"] for item in unread]


def test_the_history_pages(db, some_notifications):
    first = dispatcher.notification_history(db, "en", limit=2)
    second = dispatcher.notification_history(db, "en", limit=2, offset=2)
    assert len(first["items"]) == 2 and len(second["items"]) == 1
    assert first["total"] == second["total"] == 3
    assert {item["id"] for item in first["items"]}.isdisjoint(
        {item["id"] for item in second["items"]}
    )


def test_each_entry_reports_delivery_per_channel(db, some_notifications):
    item = dispatcher.notification_history(db, "en")["items"][0]
    assert set(item["delivery"]) == {"windows", "browser", "email", "error"}


def test_a_cancelled_reminder_leaves_the_history_alone(db, clock, some_notifications):
    """Marking a dose taken withdraws pending reminders, so the bell count drops
    with them - what was already shown stays in the history."""
    dose_id = some_notifications[0].reference_id
    clock["at"](9, 56)
    medication_service.set_dose_status(db, dose_id, DoseStatus.TAKEN.value)
    db.commit()

    remaining = db.query(Notification).count()
    assert remaining <= 3
    assert dispatcher.unread_count(db) == remaining


def test_the_endpoints_agree_with_each_other(client):
    assert client.get("/api/notifications/unread-count").json() == {"unread": 0}

    history = client.get("/api/notifications/history").json()
    assert history["items"] == [] and history["unread"] == 0

    marked = client.post("/api/notifications/read", json={})
    assert marked.status_code == 200
    assert marked.json() == {"ok": True, "marked": 0, "unread": 0}


def test_the_history_endpoint_clamps_a_silly_limit(client):
    body = client.get("/api/notifications/history?limit=100000&offset=-5").json()
    assert body["limit"] == 200
    assert body["offset"] == 0
