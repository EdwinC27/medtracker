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
    for path in ("/", "/medications", "/appointments", "/history", "/settings"):
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
    medication = client.post("/api/medications", json=payload()).json()
    dose_id = medication["doses"][0]["id"]

    assert client.post(f"/api/doses/{dose_id}/status", json={"status": "taken"}).json()["status"] == "taken"
    assert client.post(f"/api/doses/{dose_id}/status", json={"status": "skipped"}).json()["status"] == "skipped"
    assert client.post(f"/api/doses/{dose_id}/status", json={"status": "scheduled"}).json()["status"] == "scheduled"


def test_dashboard_reflects_the_data(client):
    client.post("/api/medications", json=payload())
    client.post(
        "/api/appointments",
        json={
            "doctor_name": "Dr. Smith",
            "scheduled_at": (now_local() + timedelta(days=6)).replace(microsecond=0).isoformat(),
        },
    )
    data = client.get("/api/dashboard").json()

    assert len(data["active_medications"]) == 1
    assert data["next_appointment"]["doctor_name"] == "Dr. Smith"
    assert data["todays_doses"]


def test_appointment_medication_relationship_through_the_api(client):
    medication = client.post("/api/medications", json=payload()).json()
    appointment = client.post(
        "/api/appointments",
        json={
            "doctor_name": "Dr. Smith",
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
