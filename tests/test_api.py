"""End-to-end checks through the HTTP API, including persistence."""

from __future__ import annotations

from datetime import timedelta

from app.utils.timeutil import now_local


def payload(**overrides):
    today = now_local().date()
    data = {
        "name": "Amoxicillin",
        "dose_amount": "500",
        "dose_unit": "mg",
        "quantity": 1,
        "form": "capsule",
        "comments": "Take with food",
        "start_date": today.isoformat(),
        "end_date": (today + timedelta(days=9)).isoformat(),
        "frequency_hours": 8,
        "first_dose_time": "10:00",
    }
    data.update(overrides)
    return data


def test_pages_render(client):
    for path in ("/", "/medications", "/doctors", "/appointments", "/history", "/settings"):
        response = client.get(path)
        assert response.status_code == 200
        assert "MedTracker" in response.text


def test_bootstrap_follows_the_browser_language(client):
    spanish = client.get("/api/bootstrap", headers={"Accept-Language": "es-MX,es;q=0.9"})
    assert spanish.json()["language"] == "es"

    english = client.get("/api/bootstrap", headers={"Accept-Language": "en-US"})
    assert english.json()["language"] == "en"


def test_saved_language_overrides_the_browser(client):
    client.put("/api/settings", json={"language": "es"})
    response = client.get("/api/bootstrap", headers={"Accept-Language": "en-US"})
    assert response.json()["language"] == "es"
    assert response.json()["catalog"]["nav"]["dashboard"] == "Inicio"


def test_medication_crud_flow(client):
    created = client.post("/api/medications", json=payload())
    assert created.status_code == 201
    medication = created.json()
    assert medication["status"] == "active"
    assert len(medication["doses"]) == 29

    listed = client.get("/api/medications?status=active").json()
    assert len(listed["items"]) == 1

    updated = client.put(
        f"/api/medications/{medication['id']}",
        json=payload(name="Amoxicillin 500", quantity=2),
    ).json()
    assert updated["name"] == "Amoxicillin 500"
    assert updated["quantity"] == 2

    assert client.post(f"/api/medications/{medication['id']}/suspend").json()["status"] == "suspended"
    assert client.get("/api/medications?status=suspended").json()["items"]
    assert client.post(f"/api/medications/{medication['id']}/resume").json()["status"] == "active"

    assert client.delete(f"/api/medications/{medication['id']}").status_code == 200
    assert client.get("/api/medications?status=all").json()["items"] == []


def test_validation_errors_are_returned_as_translation_keys(client):
    response = client.post("/api/medications", json=payload(name="", end_date="2000-01-01"))
    assert response.status_code == 422
    body = response.json()
    assert body["error"] == "error.validation"
    assert body["fields"]["name"] == "validation.name_required"
    assert body["fields"]["end_date"] == "validation.end_before_start"
    assert "Traceback" not in response.text


def test_unknown_medication_returns_a_clean_404(client):
    response = client.get("/api/medications/9999")
    assert response.status_code == 404
    assert response.json()["error"] == "medication.not_found"


def test_dose_can_be_marked_and_unmarked(client):
    # A treatment that starts tomorrow, so its first dose is one the
    # application will remind about rather than one that predates it.
    tomorrow = (now_local().date() + timedelta(days=1)).isoformat()
    medication = client.post(
        "/api/medications", json=payload(start_date=tomorrow)
    ).json()
    dose_id = medication["doses"][0]["id"]

    assert client.post(f"/api/doses/{dose_id}/status", json={"status": "taken"}).json()["status"] == "taken"
    assert client.post(f"/api/doses/{dose_id}/status", json={"status": "skipped"}).json()["status"] == "skipped"
    assert client.post(f"/api/doses/{dose_id}/status", json={"status": "scheduled"}).json()["status"] == "scheduled"


def test_dashboard_reflects_the_data(client):
    client.post("/api/medications", json=payload())
    doctor = client.post("/api/doctors", json={"name": "Dr. Smith"}).json()
    client.post(
        "/api/appointments",
        json={
            "doctor_id": doctor["id"],
            "scheduled_at": (now_local() + timedelta(days=6)).replace(microsecond=0).isoformat(),
        },
    )
    data = client.get("/api/dashboard").json()

    assert len(data["active_medications"]) == 1
    assert data["next_appointment"]["doctor_name"] == "Dr. Smith"
    assert data["todays_doses"]


def test_appointment_medication_relationship_through_the_api(client):
    medication = client.post("/api/medications", json=payload()).json()
    doctor = client.post("/api/doctors", json={"name": "Dr. Smith"}).json()
    appointment = client.post(
        "/api/appointments",
        json={
            "doctor_id": doctor["id"],
            "scheduled_at": (now_local() + timedelta(days=4)).replace(microsecond=0).isoformat(),
            "treatment": "Ear infection",
            "medication_ids": [medication["id"]],
        },
    ).json()

    assert [m["id"] for m in appointment["medications"]] == [medication["id"]]
    detail = client.get(f"/api/medications/{medication['id']}").json()
    assert [a["id"] for a in detail["appointments"]] == [appointment["id"]]


def test_settings_round_trip(client):
    response = client.put(
        "/api/settings",
        json={
            "default_first_dose_time": "08:00",
            "ending_soon_days": 5,
            "missed_after_minutes": 90,
            "windows_notifications": False,
        },
    )
    assert response.status_code == 200

    saved = client.get("/api/settings").json()
    assert saved["default_first_dose_time"] == "08:00"
    assert saved["ending_soon_days"] == 5
    assert saved["missed_after_minutes"] == 90
    assert saved["windows_notifications"] is False


def test_data_survives_an_application_restart(client):
    """The database is the only state; a new client sees everything again."""
    client.put("/api/settings", json={"default_first_dose_time": "09:15", "language": "es"})
    created = client.post("/api/medications", json=payload()).json()

    # A fresh TestClient re-runs startup against the same SQLite file.
    from app.main import app
    from fastapi.testclient import TestClient

    with TestClient(app) as restarted:
        medications = restarted.get("/api/medications?status=all").json()["items"]
        assert [m["id"] for m in medications] == [created["id"]]
        assert len(restarted.get(f"/api/medications/{created['id']}").json()["doses"]) == 29

        settings = restarted.get("/api/settings").json()
        assert settings["default_first_dose_time"] == "09:15"
        assert settings["language"] == "es"


def test_system_status_endpoint(client):
    status = client.get("/api/system/status").json()
    assert "scheduler" in status
    assert "windows_notifications_available" in status
    assert status["version"]


def test_notification_endpoints(client):
    assert client.get("/api/notifications/pending").json() == {"items": []}
    assert client.post("/api/notifications/delivered", json={"ids": []}).json()["ok"] is True
    result = client.post("/api/notifications/run-now").json()
    assert "dose_notifications" in result


def test_the_dashboard_handles_an_open_ended_treatment(client):
    """A medication with no end date must not break "ending soon"."""
    client.post("/api/medications", json=payload(end_date=None, name="Vitamin D"))
    client.post("/api/medications", json=payload(name="Amoxicillin"))

    data = client.get("/api/dashboard").json()

    assert len(data["active_medications"]) == 2
    open_ended = [m for m in data["active_medications"] if m["name"] == "Vitamin D"][0]
    assert open_ended["open_ended"] is True
    assert open_ended["days_remaining"] is None
    # Only the one with a real end date can appear in the warning list.
    assert all(m["end_date"] is not None for m in data["ending_soon"])


def test_doctor_crud_through_the_api(client):
    created = client.post(
        "/api/doctors",
        json={"name": "Dr. Smith", "occupation": "Otolaryngologist", "phone": "555-555-5555"},
    )
    assert created.status_code == 201
    doctor = created.json()

    assert client.get("/api/doctors").json()["items"][0]["name"] == "Dr. Smith"

    updated = client.put(
        f"/api/doctors/{doctor['id']}", json={"name": "Dr. J. Smith", "phone": "555-000"}
    ).json()
    assert updated["name"] == "Dr. J. Smith"

    detail = client.get(f"/api/doctors/{doctor['id']}").json()
    assert detail["appointments"] == []

    assert client.delete(f"/api/doctors/{doctor['id']}").status_code == 200
    assert client.get("/api/doctors").json()["items"] == []


def test_follow_up_options_endpoint(client):
    doctor = client.post("/api/doctors", json={"name": "Dr. Smith"}).json()
    past = (now_local() - timedelta(days=5)).replace(microsecond=0)
    future = (now_local() + timedelta(days=5)).replace(microsecond=0)

    earlier = client.post(
        "/api/appointments",
        json={"doctor_id": doctor["id"], "scheduled_at": past.isoformat()},
    ).json()
    client.post(
        "/api/appointments",
        json={"doctor_id": doctor["id"], "scheduled_at": future.isoformat()},
    )

    options = client.get(
        "/api/appointments/follow-up-options?before=" + now_local().isoformat()
    ).json()
    assert [item["id"] for item in options["items"]] == [earlier["id"]]
