"""Export: CSV, JSON and PDF.

Each format has a different job:

* **CSV**  — one file per dataset, for a spreadsheet. UTF-8 with a BOM so Excel
  on Windows opens `á é í ó ú ñ` correctly instead of mojibake.
* **JSON** — the relational shape of the database, ids intact, meant to be read
  back by the import in `import_service.py`.
* **PDF**  — a printable medical history for a human, built with ReportLab.

Nothing here interprets anything medically: it is the user's own data, laid out.
"""

from __future__ import annotations

import csv
import io
import json
import zipfile
from datetime import datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.config import APP_VERSION, EXPORT_DATASETS, EXPORT_DIR
from app.i18n import t
from app.models.models import (
    Appointment,
    AppointmentReminder,
    Doctor,
    Medication,
    MedicationDose,
)
from app.services.errors import AppError, ValidationError
from app.services.settings_service import get_settings, settings_to_dict
from app.services.textformat import format_date, format_datetime, format_time
from app.utils.timeutil import iso, now_local

FORMATS = ("csv", "json", "pdf")
# Excel on Windows needs the BOM to detect UTF-8 in a .csv.
UTF8_BOM = "﻿"


def _stamp() -> str:
    return now_local().strftime("%Y%m%d-%H%M%S")


def _validate(export_format: str, datasets: list[str] | None) -> tuple[str, list[str]]:
    if export_format not in FORMATS:
        raise ValidationError({"format": "validation.export_format_invalid"})
    chosen = [d for d in (datasets or []) if d in EXPORT_DATASETS] or list(EXPORT_DATASETS)
    return export_format, chosen


# --------------------------------------------------------------------------- #
# Row builders — shared by CSV and PDF
# --------------------------------------------------------------------------- #
def _load(db: Session) -> dict:
    doctors = list(
        db.execute(select(Doctor).order_by(Doctor.name)).scalars().unique().all()
    )
    medications = list(
        db.execute(
            select(Medication)
            .options(selectinload(Medication.appointments), selectinload(Medication.doses))
            .order_by(Medication.start_date, Medication.name)
        )
        .scalars()
        .unique()
        .all()
    )
    appointments = list(
        db.execute(
            select(Appointment)
            .options(
                selectinload(Appointment.doctor),
                selectinload(Appointment.medications),
                selectinload(Appointment.reminders),
                selectinload(Appointment.follow_up_of),
            )
            .order_by(Appointment.scheduled_at)
        )
        .scalars()
        .unique()
        .all()
    )
    return {"doctors": doctors, "medications": medications, "appointments": appointments}


def _dataset_rows(db: Session, name: str, language: str, data: dict) -> tuple[list[str], list[list]]:
    """`(header, rows)` for one CSV dataset, with translated column titles."""
    if name == "doctors":
        header = [t("doctor.name", language), t("doctor.occupation", language),
                  t("doctor.phone", language), t("doctor.notes", language),
                  t("doctor.appointments", language)]
        rows = [
            [d.name, d.occupation or "", d.phone or "", d.notes or "", len(d.appointments)]
            for d in data["doctors"]
        ]
        return header, rows

    if name == "medications":
        header = [t("medication.name", language), t("medication.dose", language),
                  t("medication.unit", language), t("medication.quantity", language),
                  t("medication.form", language), t("medication.frequency", language),
                  t("medication.first_dose_time", language),
                  t("medication.start_date", language), t("medication.end_date", language),
                  t("medication.status", language), t("medication.comments", language)]
        rows = []
        for m in data["medications"]:
            rows.append([
                m.name, m.dose_amount or "", m.dose_unit or "",
                "" if m.quantity is None else m.quantity, m.form or "",
                m.frequency_hours, m.first_dose_time.strftime("%H:%M"),
                m.start_date.isoformat(),
                m.end_date.isoformat() if m.end_date else t("medication.no_end_date", language),
                t(f"status.{m.status}", language), m.comments or "",
            ])
        return header, rows

    if name == "doses":
        header = [t("medication.singular", language), t("dose.scheduled_for", language),
                  t("medication.status", language), t("dose.marked_at", language)]
        rows = []
        for dose in db.execute(
            select(MedicationDose)
            .options(selectinload(MedicationDose.medication))
            .order_by(MedicationDose.scheduled_at)
        ).scalars().all():
            rows.append([
                dose.medication.name if dose.medication else "",
                dose.scheduled_at.isoformat(sep=" "),
                t(f"status.{dose.status}", language),
                dose.marked_at.isoformat(sep=" ") if dose.marked_at else "",
            ])
        return header, rows

    if name == "appointments":
        header = [t("appointment.date", language), t("doctor.singular", language),
                  t("doctor.occupation", language), t("appointment.location", language),
                  t("appointment.treatment", language), t("appointment.notes", language),
                  t("appointment.medications", language), t("appointment.follow_up_of", language)]
        rows = []
        for a in data["appointments"]:
            rows.append([
                a.scheduled_at.isoformat(sep=" "),
                a.doctor.name if a.doctor else "",
                a.doctor.occupation if a.doctor and a.doctor.occupation else "",
                a.location or "", a.treatment or "", a.notes or "",
                ", ".join(m.name for m in a.medications),
                a.follow_up_of.scheduled_at.date().isoformat() if a.follow_up_of else "",
            ])
        return header, rows

    # timeline
    header = [t("appointment.date", language), t("doctor.singular", language),
              t("appointment.treatment", language), t("appointment.medications", language),
              t("appointment.notes", language)]
    rows = []
    for a in data["appointments"]:
        rows.append([
            a.scheduled_at.isoformat(sep=" "),
            a.doctor.name if a.doctor else "",
            a.treatment or "",
            ", ".join(m.name for m in a.medications),
            a.notes or "",
        ])
    return header, rows


# --------------------------------------------------------------------------- #
# CSV
# --------------------------------------------------------------------------- #
def _csv_bytes(header: list[str], rows: list[list]) -> bytes:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow(header)
    writer.writerows(rows)
    return (UTF8_BOM + buffer.getvalue()).encode("utf-8")


def export_csv(db: Session, datasets: list[str], language: str) -> Path:
    """One dataset -> one .csv; several -> a .zip of .csv files."""
    data = _load(db)
    stamp = _stamp()

    if len(datasets) == 1:
        name = datasets[0]
        header, rows = _dataset_rows(db, name, language, data)
        target = EXPORT_DIR / f"medtracker-{name}-{stamp}.csv"
        target.write_bytes(_csv_bytes(header, rows))
        return target

    target = EXPORT_DIR / f"medtracker-export-{stamp}.zip"
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        for name in datasets:
            header, rows = _dataset_rows(db, name, language, data)
            archive.writestr(f"{name}.csv", _csv_bytes(header, rows))
    return target


# --------------------------------------------------------------------------- #
# JSON
# --------------------------------------------------------------------------- #
def build_json(db: Session, include_settings: bool = True) -> dict:
    """The relational export. Ids are preserved so relationships survive."""
    data = _load(db)

    doctors = [
        {
            "id": d.id, "name": d.name, "occupation": d.occupation,
            "phone": d.phone, "notes": d.notes, "created_at": iso(d.created_at),
        }
        for d in data["doctors"]
    ]

    medications = []
    for m in data["medications"]:
        medications.append({
            "id": m.id, "name": m.name, "image_path": m.image_path,
            "dose_amount": m.dose_amount, "dose_unit": m.dose_unit,
            "quantity": m.quantity, "form": m.form, "comments": m.comments,
            "start_date": iso(m.start_date), "end_date": iso(m.end_date),
            "frequency_hours": m.frequency_hours,
            "first_dose_time": m.first_dose_time.strftime("%H:%M"),
            "status": m.status, "suspended_at": iso(m.suspended_at),
            "completed_at": iso(m.completed_at), "created_at": iso(m.created_at),
        })

    doses = [
        {
            "id": dose.id, "medication_id": dose.medication_id,
            "scheduled_at": iso(dose.scheduled_at), "status": dose.status,
            "marked_at": iso(dose.marked_at),
            "status_changed_at": iso(dose.status_changed_at),
            "snoozed_until": iso(dose.snoozed_until),
        }
        for dose in db.execute(
            select(MedicationDose).order_by(MedicationDose.id)
        ).scalars().all()
    ]

    appointments = [
        {
            "id": a.id, "doctor_id": a.doctor_id, "scheduled_at": iso(a.scheduled_at),
            "location": a.location, "treatment": a.treatment, "notes": a.notes,
            "next_appointment_at": iso(a.next_appointment_at),
            "follow_up_of_id": a.follow_up_of_id,
            "reminder_days_3": a.reminder_days_3,
            "reminder_day_1": a.reminder_day_1,
            "reminder_hours_3": a.reminder_hours_3,
            "created_at": iso(a.created_at),
        }
        for a in data["appointments"]
    ]

    reminders = [
        {
            "appointment_id": r.appointment_id, "kind": r.kind,
            "remind_at": iso(r.remind_at), "sent_at": iso(r.sent_at),
        }
        for r in db.execute(select(AppointmentReminder).order_by(AppointmentReminder.id))
        .scalars()
        .all()
    ]

    links = [
        {"appointment_id": a.id, "medication_id": m.id}
        for a in data["appointments"]
        for m in a.medications
    ]

    payload = {
        "format": "medtracker-export",
        "version": 1,
        "app_version": APP_VERSION,
        "exported_at": now_local().isoformat(),
        "doctors": doctors,
        "medications": medications,
        "medication_doses": doses,
        "appointments": appointments,
        "appointment_reminders": reminders,
        "appointment_medications": links,
    }

    if include_settings:
        exported = settings_to_dict(get_settings(db))
        # Never export the SMTP credentials: the protected blob is tied to this
        # Windows account and would be useless (and a liability) elsewhere.
        for key in ("smtp_password_set", "secret_backend", "available_languages",
                    "frequency_options", "dose_offsets", "database_path", "version",
                    "smtp_username", "email_recipient", "email_sender", "smtp_host"):
            exported.pop(key, None)
        payload["settings"] = exported

    return payload


def export_json(db: Session) -> Path:
    target = EXPORT_DIR / f"medtracker-export-{_stamp()}.json"
    target.write_text(
        json.dumps(build_json(db), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return target


# --------------------------------------------------------------------------- #
# PDF
# --------------------------------------------------------------------------- #
def export_pdf(db: Session, datasets: list[str], language: str) -> Path:
    # ReportLab is the only dependency v3 added. If an existing installation was
    # never updated, say so in a sentence the user can act on rather than
    # failing with a stack trace: CSV and JSON still work.
    try:
        from reportlab.lib.enums import TA_LEFT
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
        from reportlab.lib.units import mm
        from reportlab.platypus import (
            PageBreak,
            Paragraph,
            SimpleDocTemplate,
            Spacer,
            Table,
            TableStyle,
        )
        from reportlab.lib import colors
    except ImportError:
        raise AppError("error.pdf_unavailable") from None

    data = _load(db)
    target = EXPORT_DIR / f"medtracker-history-{_stamp()}.pdf"

    styles = getSampleStyleSheet()
    # Helvetica covers Latin-1, which is everything Spanish needs.
    body = ParagraphStyle("body", parent=styles["BodyText"], fontName="Helvetica",
                          fontSize=9.5, leading=13, alignment=TA_LEFT)
    h1 = ParagraphStyle("h1", parent=styles["Title"], fontName="Helvetica-Bold", fontSize=18)
    h2 = ParagraphStyle("h2", parent=styles["Heading2"], fontName="Helvetica-Bold",
                        fontSize=13, spaceBefore=14, spaceAfter=6)
    muted = ParagraphStyle("muted", parent=body, textColor=colors.HexColor("#5b6472"))

    def esc(value) -> str:
        text = "" if value is None else str(value)
        return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    story = [
        Paragraph(esc(t("export.pdf_title", language)), h1),
        Paragraph(
            esc(t("export.generated_on", language,
                  date=format_datetime(now_local(), language))),
            muted,
        ),
        Spacer(1, 4 * mm),
    ]

    def table(header: list[str], rows: list[list], widths=None):
        cells = [[Paragraph(f"<b>{esc(c)}</b>", body) for c in header]]
        cells += [[Paragraph(esc(c), body) for c in row] for row in rows]
        block = Table(cells, colWidths=widths, repeatRows=1)
        block.setStyle(TableStyle([
            ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#c9d1dc")),
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eef2f8")),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 5),
            ("RIGHTPADDING", (0, 0), (-1, -1), 5),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ]))
        return block

    if "doctors" in datasets and data["doctors"]:
        story.append(Paragraph(esc(t("doctor.title", language)), h2))
        story.append(table(
            [t("doctor.name", language), t("doctor.occupation", language),
             t("doctor.phone", language)],
            [[d.name, d.occupation or "—", d.phone or "—"] for d in data["doctors"]],
            widths=[70 * mm, 60 * mm, 40 * mm],
        ))

    if "medications" in datasets and data["medications"]:
        story.append(Paragraph(esc(t("medication.title", language)), h2))
        rows = []
        for m in data["medications"]:
            dose = " ".join(filter(None, [m.dose_amount, m.dose_unit or ""])).strip() or "—"
            end = format_date(m.end_date, language) if m.end_date else t("medication.no_end_date", language)
            rows.append([
                m.name, dose,
                t(f"frequency.every_{m.frequency_hours}_hours", language),
                f"{format_date(m.start_date, language)} → {end}",
                t(f"status.{m.status}", language),
            ])
        story.append(table(
            [t("medication.name", language), t("medication.dose", language),
             t("medication.frequency", language), t("medication.treatment", language),
             t("medication.status", language)],
            rows, widths=[38 * mm, 26 * mm, 30 * mm, 55 * mm, 25 * mm],
        ))

    if ("appointments" in datasets or "timeline" in datasets) and data["appointments"]:
        story.append(PageBreak())
        story.append(Paragraph(esc(t("nav.history", language)), h2))
        for a in data["appointments"]:
            story.append(Paragraph(
                f"<b>{esc(format_date(a.scheduled_at, language))} — "
                f"{esc(format_time(a.scheduled_at, language))}</b>", body))
            story.append(Paragraph(
                esc(f"{a.doctor.name if a.doctor else ''}"
                    f"{' — ' + a.doctor.occupation if a.doctor and a.doctor.occupation else ''}"),
                body))
            if a.follow_up_of:
                story.append(Paragraph(
                    esc(f"{t('appointment.follow_up_of', language)}: "
                        f"{format_date(a.follow_up_of.scheduled_at, language)}"), muted))
            if a.treatment:
                story.append(Paragraph(
                    f"<b>{esc(t('appointment.treatment', language))}:</b> {esc(a.treatment)}", body))
            if a.medications:
                story.append(Paragraph(
                    f"<b>{esc(t('appointment.medications', language))}:</b> "
                    f"{esc(', '.join(m.name for m in a.medications))}", body))
            if a.notes:
                story.append(Paragraph(
                    f"<b>{esc(t('appointment.notes', language))}:</b> {esc(a.notes)}", body))
            story.append(Spacer(1, 4 * mm))

    if "doses" in datasets:
        counts: dict[str, int] = {}
        for (status, count) in db.execute(
            select(MedicationDose.status, __import__("sqlalchemy").func.count())
            .group_by(MedicationDose.status)
        ).all():
            counts[status] = count
        if counts:
            story.append(Paragraph(esc(t("dose.history", language)), h2))
            story.append(table(
                [t("medication.status", language), t("dose.title", language)],
                [[t(f"status.{k}", language), str(v)] for k, v in sorted(counts.items())],
                widths=[60 * mm, 40 * mm],
            ))

    story.append(Spacer(1, 8 * mm))
    story.append(Paragraph(esc(t("app.disclaimer", language)), muted))

    SimpleDocTemplate(
        str(target), pagesize=A4,
        leftMargin=18 * mm, rightMargin=18 * mm, topMargin=16 * mm, bottomMargin=16 * mm,
        title=t("export.pdf_title", language), author="MedTracker",
    ).build(story)
    return target


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #
def export(db: Session, export_format: str, datasets: list[str] | None, language: str) -> Path:
    export_format, chosen = _validate(export_format, datasets)
    if export_format == "json":
        return export_json(db)
    if export_format == "pdf":
        return export_pdf(db, chosen, language)
    return export_csv(db, chosen, language)


def cleanup_exports(keep_hours: int = 24) -> int:
    """Generated files are temporary; drop yesterday's on the way past."""
    cutoff = now_local() - __import__("datetime").timedelta(hours=keep_hours)
    removed = 0
    for path in EXPORT_DIR.glob("medtracker-*"):
        try:
            if datetime.fromtimestamp(path.stat().st_mtime) < cutoff:
                path.unlink()
                removed += 1
        except OSError:  # pragma: no cover
            continue
    return removed
