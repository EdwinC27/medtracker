"""v3 Export (CSV / JSON / PDF) and import (JSON, full replace)."""

from __future__ import annotations

import csv
import io
import json
import sqlite3
import zipfile
from datetime import datetime, timedelta

import pytest

from app.config import EXPORT_DATASETS
from app.models.models import Appointment, Doctor, Medication, MedicationDose
from app.services import appointments as appointment_service
from app.services import backup as backup_service
from app.services import export_service, import_service
from app.services import medications as medication_service
from app.services.errors import ValidationError
from app.services.settings_service import get_settings
from tests.test_appointments import make_doctor
from tests.test_medications import make_payload


def seed(db):
    medication = medication_service.create_medication(
        db,
        make_payload(
            name="Amoxicilina",
            comments="Tomar con alimentos — atención a la garganta",
            start_date="2026-09-01",
            end_date="2026-09-03",
            frequency_hours=12,
            first_dose_time="09:00",
        ),
    )
    doctor = make_doctor(db, "Dr. Rosa Martínez")
    first = appointment_service.create_appointment(
        db,
        {
            "doctor_id": doctor.id,
            "scheduled_at": datetime(2026, 9, 5, 10, 0).isoformat(),
            "treatment": "Revisión de oído",
            "medication_ids": [medication.id],
        },
    )
    appointment_service.create_appointment(
        db,
        {
            "doctor_id": doctor.id,
            "scheduled_at": datetime(2026, 10, 5, 10, 0).isoformat(),
            "treatment": "Seguimiento",
            "follow_up_of_id": first.id,
        },
    )
    db.commit()
    return medication, doctor, first


def counts(db):
    return {
        "doctors": db.query(Doctor).count(),
        "medications": db.query(Medication).count(),
        "medication_doses": db.query(MedicationDose).count(),
        "appointments": db.query(Appointment).count(),
    }


# --------------------------------------------------------------------------- #
# JSON export
# --------------------------------------------------------------------------- #
def test_the_json_export_is_valid_json_and_says_what_it_is(db):
    seed(db)
    path = export_service.export(db, "json", None, "es")

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["format"] == "medtracker-export"
    assert payload["version"] == 1
    assert payload["exported_at"]
    assert path.suffix == ".json"


def test_the_json_export_keeps_ids_and_relationships(db):
    medication, doctor, appointment = seed(db)
    payload = export_service.build_json(db)

    assert [d["id"] for d in payload["doctors"]] == [doctor.id]
    assert [m["id"] for m in payload["medications"]] == [medication.id]
    assert all(dose["medication_id"] == medication.id for dose in payload["medication_doses"])
    assert {link["appointment_id"] for link in payload["appointment_medications"]} == {
        appointment.id
    }
    follow_ups = [a["follow_up_of_id"] for a in payload["appointments"] if a["follow_up_of_id"]]
    assert follow_ups == [appointment.id]


def test_the_json_export_never_carries_the_mail_password(db):
    settings = get_settings(db)
    settings.smtp_username = "someone@example.com"
    settings.smtp_password_protected = "encrypted-blob"
    db.flush()

    text = json.dumps(export_service.build_json(db))
    assert "encrypted-blob" not in text
    assert "smtp_password_protected" not in text
    assert "smtp_password" not in text


def test_the_json_export_survives_an_empty_database(db):
    payload = export_service.build_json(db)
    assert payload["medications"] == [] and payload["doctors"] == []


# --------------------------------------------------------------------------- #
# CSV export
# --------------------------------------------------------------------------- #
def test_one_dataset_gives_one_csv_that_excel_can_read(db):
    seed(db)
    path = export_service.export(db, "csv", ["medications"], "es")

    raw = path.read_bytes()
    assert path.suffix == ".csv"
    assert raw.startswith(b"\xef\xbb\xbf")          # UTF-8 BOM, for Excel
    rows = list(csv.reader(io.StringIO(raw.decode("utf-8-sig"))))
    assert len(rows) == 2                            # header + one medication
    assert "Amoxicilina" in rows[1]


def test_several_datasets_give_a_zip_of_csv_files(db):
    seed(db)
    path = export_service.export(db, "csv", ["medications", "doctors"], "es")

    assert path.suffix == ".zip"
    with zipfile.ZipFile(path) as archive:
        assert sorted(archive.namelist()) == ["doctors.csv", "medications.csv"]
        assert archive.read("doctors.csv").decode("utf-8-sig").count("Martínez") == 1


def test_asking_for_nothing_exports_everything(db):
    seed(db)
    path = export_service.export(db, "csv", [], "en")
    with zipfile.ZipFile(path) as archive:
        assert sorted(archive.namelist()) == sorted(f"{name}.csv" for name in EXPORT_DATASETS)


def test_an_unknown_format_is_refused(db):
    with pytest.raises(ValidationError) as exc:
        export_service.export(db, "docx", None, "en")
    assert exc.value.fields["format"] == "validation.export_format_invalid"


# --------------------------------------------------------------------------- #
# PDF export
# --------------------------------------------------------------------------- #
def test_the_pdf_is_a_real_pdf(db):
    seed(db)
    path = export_service.export(db, "pdf", None, "es")

    raw = path.read_bytes()
    assert path.suffix == ".pdf"
    assert raw.startswith(b"%PDF-")
    assert raw.rstrip().endswith(b"%%EOF")
    assert len(raw) > 1000


def test_the_pdf_is_produced_in_both_languages(db):
    seed(db)
    for language in ("en", "es"):
        assert export_service.export(db, "pdf", None, language).read_bytes().startswith(b"%PDF-")


# --------------------------------------------------------------------------- #
# Import: validation
# --------------------------------------------------------------------------- #
def test_a_file_that_is_not_json_is_refused(db):
    with pytest.raises(ValidationError) as exc:
        import_service.parse_payload(b"<html>not json</html>")
    assert exc.value.fields["file"] == "validation.import_not_json"


def test_json_from_another_application_is_refused(db):
    with pytest.raises(ValidationError) as exc:
        import_service.parse_payload(json.dumps({"format": "some-other-app", "version": 1}))
    assert exc.value.fields["file"] == "validation.import_wrong_format"


def test_a_future_export_version_is_refused(db):
    with pytest.raises(ValidationError) as exc:
        import_service.parse_payload(json.dumps({"format": "medtracker-export", "version": 99}))
    assert exc.value.fields["file"] == "validation.import_unsupported_version"


def test_a_truncated_export_is_refused(db):
    with pytest.raises(ValidationError) as exc:
        import_service.parse_payload(
            json.dumps({"format": "medtracker-export", "version": 1, "doctors": []})
        )
    assert exc.value.fields["file"] == "validation.import_incomplete"


def test_an_appointment_pointing_at_a_missing_doctor_is_refused(db):
    with pytest.raises(ValidationError) as exc:
        import_service.parse_payload(
            json.dumps(
                {
                    "format": "medtracker-export", "version": 1,
                    "doctors": [], "medications": [],
                    "appointments": [{"id": 1, "doctor_id": 7,
                                      "scheduled_at": "2026-09-01T10:00:00"}],
                }
            )
        )
    assert exc.value.fields["file"] == "validation.import_broken_reference"


def test_a_dose_pointing_at_a_missing_medication_is_refused(db):
    with pytest.raises(ValidationError) as exc:
        import_service.parse_payload(
            json.dumps(
                {
                    "format": "medtracker-export", "version": 1,
                    "doctors": [], "medications": [], "appointments": [],
                    "medication_doses": [{"id": 1, "medication_id": 4,
                                          "scheduled_at": "2026-09-01T10:00:00"}],
                }
            )
        )
    assert exc.value.fields["file"] == "validation.import_broken_reference"


def test_a_json_export_of_this_app_is_accepted(db):
    seed(db)
    payload = import_service.parse_payload(
        json.dumps(export_service.build_json(db), ensure_ascii=False)
    )
    assert payload["format"] == "medtracker-export"


def test_a_utf8_bom_at_the_front_does_not_break_the_import(db):
    seed(db)
    raw = ("﻿" + json.dumps(export_service.build_json(db))).encode("utf-8")
    assert import_service.parse_payload(raw)["format"] == "medtracker-export"


# --------------------------------------------------------------------------- #
# Import: preview and apply
# --------------------------------------------------------------------------- #
def test_the_preview_compares_the_file_with_what_is_here_and_changes_nothing(db):
    seed(db)
    payload = export_service.build_json(db)
    before = counts(db)

    result = import_service.preview(db, payload)

    assert result["incoming"] == before
    assert result["current"] == before
    assert "Amoxicilina" in result["medication_names"]
    assert counts(db) == before


def test_a_round_trip_puts_everything_back_exactly(db):
    medication, doctor, appointment = seed(db)
    payload = export_service.build_json(db)
    before = counts(db)

    result = import_service.apply_import(db, payload)
    db.commit()

    assert result == before
    assert counts(db) == before
    restored = db.get(Medication, medication.id)
    assert restored.name == "Amoxicilina"
    assert restored.comments.startswith("Tomar con alimentos")
    assert db.get(Doctor, doctor.id).name == "Dr. Rosa Martínez"
    # The relationships came back too.
    assert [m.id for m in db.get(Appointment, appointment.id).medications] == [medication.id]


def test_a_follow_up_link_survives_whatever_order_the_rows_are_in(db):
    _, _, first = seed(db)
    payload = export_service.build_json(db)
    payload["appointments"].reverse()          # the follow-up now comes first

    import_service.apply_import(db, payload)
    db.commit()

    follow_ups = db.query(Appointment).filter(Appointment.follow_up_of_id.is_not(None)).all()
    assert [a.follow_up_of_id for a in follow_ups] == [first.id]


def test_importing_replaces_rather_than_merges(db):
    """Importing the same file twice must not leave two of everything."""
    seed(db)
    payload = export_service.build_json(db)
    before = counts(db)

    import_service.apply_import(db, payload)
    db.commit()
    import_service.apply_import(db, payload)
    db.commit()

    assert counts(db) == before


def test_importing_an_empty_export_clears_the_database(db):
    seed(db)
    empty = {
        "format": "medtracker-export", "version": 1,
        "doctors": [], "medications": [], "appointments": [],
        "medication_doses": [], "appointment_medications": [],
    }
    import_service.apply_import(db, empty)
    db.commit()

    assert counts(db) == {
        "doctors": 0, "medications": 0, "medication_doses": 0, "appointments": 0
    }


def test_settings_only_travel_when_they_are_asked_for(db):
    seed(db)
    payload = export_service.build_json(db)
    payload["settings"]["language"] = "en"
    get_settings(db).language = "es"
    db.flush()

    import_service.apply_import(db, payload, import_settings=False)
    assert get_settings(db).language == "es"

    import_service.apply_import(db, payload, import_settings=True)
    assert get_settings(db).language == "en"


# --------------------------------------------------------------------------- #
# Import through the API: the safety backup
# --------------------------------------------------------------------------- #
def test_the_import_endpoint_takes_a_safety_backup_first(client, tmp_path, monkeypatch):
    live = tmp_path / "medtracker.db"
    connection = sqlite3.connect(str(live))
    connection.execute("CREATE TABLE medications (id INTEGER PRIMARY KEY)")
    connection.commit()
    connection.close()
    monkeypatch.setattr(backup_service, "DB_PATH", live)

    settings = client.put(
        "/api/settings", json={"backup_location": str(tmp_path / "backups")}
    )
    assert settings.status_code == 200

    created = client.post("/api/medications", json=make_payload(name="Ryaltris"))
    assert created.status_code == 201

    export = client.post("/api/export", json={"format": "json"}).json()
    content = client.get(export["download_url"]).content

    response = client.post(
        "/api/import", files={"file": ("export.json", content, "application/json")}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["imported"]["medications"] == 1
    assert body["safety_backup"]["kind"] == "preimport"
    assert (tmp_path / "backups" / body["safety_backup"]["name"]).exists()


def test_the_preview_endpoint_rejects_rubbish_without_touching_anything(client):
    client.post("/api/medications", json=make_payload(name="Ryaltris"))
    response = client.post(
        "/api/import/preview", files={"file": ("bad.json", b"nope", "application/json")}
    )
    assert response.status_code == 422
    assert response.json()["fields"]["file"] == "validation.import_not_json"
    assert len(client.get("/api/medications").json()["items"]) == 1


def test_the_export_endpoint_hands_back_a_downloadable_file(client):
    client.post("/api/medications", json=make_payload(name="Ryaltris"))
    for fmt in ("json", "csv", "pdf"):
        created = client.post("/api/export", json={"format": fmt}).json()
        assert created["ok"] and created["size"] > 0
        download = client.get(created["download_url"])
        assert download.status_code == 200
        assert len(download.content) == created["size"]


def test_the_export_endpoint_refuses_a_path_outside_its_folder(client):
    assert client.get("/api/export/..%2F..%2Fmedtracker.db").status_code in (404, 400)
