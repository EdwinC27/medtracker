"""Medication CRUD, status transitions and dose marking."""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.config import (
    FORM_OPTIONS,
    FREQUENCY_OPTIONS,
    MAX_TREATMENT_DAYS,
    UNIT_OPTIONS,
)
from app.models.models import (
    Appointment,
    DoseStatus,
    Medication,
    MedicationDose,
    MedicationStatus,
)
from app.services.errors import NotFoundError, ValidationError
from app.services.scheduling import (
    clear_upcoming_doses,
    next_dose_for,
    rebuild_doses,
)
from app.services.settings_service import get_settings
from app.utils.timeutil import iso, now_local, parse_date, parse_time

SCHEDULE_FIELDS = ("start_date", "end_date", "frequency_hours", "first_dose_time")


# --------------------------------------------------------------------------- #
# Queries
# --------------------------------------------------------------------------- #
def list_medications(db: Session, status: str | None = None) -> list[Medication]:
    stmt = select(Medication).options(
        selectinload(Medication.doses), selectinload(Medication.appointments)
    )
    if status and status != "all":
        stmt = stmt.where(Medication.status == status)
    stmt = stmt.order_by(Medication.status, Medication.start_date.desc(), Medication.name)
    return list(db.execute(stmt).scalars().unique().all())


def get_medication(db: Session, medication_id: int) -> Medication:
    medication = db.get(Medication, medication_id)
    if medication is None:
        raise NotFoundError("medication.not_found")
    return medication


# --------------------------------------------------------------------------- #
# Validation
# --------------------------------------------------------------------------- #
def _validate(data: dict, current: Medication | None = None) -> dict:
    """Normalise and validate a medication payload. Raises ValidationError."""
    fields: dict[str, str] = {}
    clean: dict = {}

    name = (data.get("name") or "").strip()
    if not name:
        fields["name"] = "validation.name_required"
    clean["name"] = name[:160]

    dose_amount = str(data.get("dose_amount") or "").strip()
    if not dose_amount:
        fields["dose_amount"] = "validation.dose_required"
    clean["dose_amount"] = dose_amount[:40]

    dose_unit = (data.get("dose_unit") or "mg").strip().lower()
    clean["dose_unit"] = dose_unit if dose_unit in UNIT_OPTIONS else "mg"

    raw_quantity = data.get("quantity")
    if raw_quantity in (None, ""):
        raw_quantity = 1
    try:
        quantity = float(str(raw_quantity).replace(",", "."))
        if quantity <= 0:
            raise ValueError
        clean["quantity"] = quantity
    except (TypeError, ValueError):
        fields["quantity"] = "validation.quantity_positive"
        clean["quantity"] = 1.0

    form = (data.get("form") or "tablet").strip().lower()
    clean["form"] = form if form in FORM_OPTIONS else "other"

    comments = data.get("comments")
    clean["comments"] = (comments or "").strip()[:2000] or None

    start_date = _safe_date(data.get("start_date"), fields, "start_date", "validation.start_date_required")
    end_date = _safe_date(data.get("end_date"), fields, "end_date", "validation.end_date_required")
    if start_date and end_date:
        if end_date < start_date:
            fields["end_date"] = "validation.end_before_start"
        elif (end_date - start_date).days > MAX_TREATMENT_DAYS:
            fields["end_date"] = "validation.treatment_too_long"
    clean["start_date"] = start_date
    clean["end_date"] = end_date

    try:
        frequency = int(data.get("frequency_hours"))
        if frequency not in FREQUENCY_OPTIONS:
            fields["frequency_hours"] = "validation.frequency_invalid"
        clean["frequency_hours"] = frequency
    except (TypeError, ValueError):
        fields["frequency_hours"] = "validation.frequency_required"
        clean["frequency_hours"] = 8

    raw_time = data.get("first_dose_time")
    try:
        first_dose_time = parse_time(raw_time)
    except (TypeError, ValueError):
        first_dose_time = None
        fields["first_dose_time"] = "validation.time_invalid"
    if first_dose_time is None and "first_dose_time" not in fields:
        if current is not None:
            first_dose_time = current.first_dose_time
        else:
            fields["first_dose_time"] = "validation.first_dose_required"
    clean["first_dose_time"] = first_dose_time

    if fields:
        raise ValidationError(fields)
    return clean


def parse_id_list(values, field: str) -> list[int]:
    """Turn a list of ids from a request body into ints, or fail cleanly."""
    try:
        return [int(value) for value in values if str(value).strip()]
    except (TypeError, ValueError):
        raise ValidationError({field: "error.validation"}) from None


def _safe_date(value, fields: dict, key: str, required_message: str) -> date | None:
    try:
        parsed = parse_date(value)
    except (TypeError, ValueError):
        fields[key] = "validation.date_invalid"
        return None
    if parsed is None:
        fields[key] = required_message
    return parsed


# --------------------------------------------------------------------------- #
# Commands
# --------------------------------------------------------------------------- #
def create_medication(db: Session, data: dict) -> Medication:
    settings = get_settings(db)
    payload = dict(data)
    if not payload.get("first_dose_time"):
        payload["first_dose_time"] = settings.default_first_dose_time.strftime("%H:%M")

    clean = _validate(payload)
    medication = Medication(
        name=clean["name"],
        image_path=data.get("image_path"),
        dose_amount=clean["dose_amount"],
        dose_unit=clean["dose_unit"],
        quantity=clean["quantity"],
        form=clean["form"],
        comments=clean["comments"],
        start_date=clean["start_date"],
        end_date=clean["end_date"],
        frequency_hours=clean["frequency_hours"],
        first_dose_time=clean["first_dose_time"],
        status=MedicationStatus.ACTIVE.value,
    )
    db.add(medication)
    db.flush()

    # Build the full schedule first, then decide the status: a treatment that is
    # entered after it ended still gets its dose history.
    rebuild_doses(db, medication, from_time=None)
    if medication.end_date < now_local().date():
        medication.status = MedicationStatus.COMPLETED.value
        medication.completed_at = now_local()

    _sync_appointment_links(db, medication, data.get("appointment_ids"))
    db.flush()
    return medication


def update_medication(db: Session, medication_id: int, data: dict) -> Medication:
    medication = get_medication(db, medication_id)
    payload = dict(data)
    if not payload.get("first_dose_time"):
        payload["first_dose_time"] = medication.first_dose_time.strftime("%H:%M")

    clean = _validate(payload, current=medication)

    end_date_changed = clean["end_date"] != medication.end_date
    schedule_changed = (
        clean["start_date"] != medication.start_date
        or end_date_changed
        or clean["frequency_hours"] != medication.frequency_hours
        or clean["first_dose_time"] != medication.first_dose_time
    )

    medication.name = clean["name"]
    medication.dose_amount = clean["dose_amount"]
    medication.dose_unit = clean["dose_unit"]
    medication.quantity = clean["quantity"]
    medication.form = clean["form"]
    medication.comments = clean["comments"]
    medication.start_date = clean["start_date"]
    medication.end_date = clean["end_date"]
    medication.frequency_hours = clean["frequency_hours"]
    medication.first_dose_time = clean["first_dose_time"]
    if "image_path" in data and data.get("image_path") != medication.image_path:
        _remove_image(medication.image_path)
        medication.image_path = data.get("image_path")

    # Moving the end date of a finished treatment into the future revives it.
    # Editing anything else (a typo in the name, a comment) leaves a treatment
    # the user finished on purpose exactly as it was.
    if (
        medication.status == MedicationStatus.COMPLETED.value
        and end_date_changed
        and clean["end_date"] >= now_local().date()
    ):
        medication.status = MedicationStatus.ACTIVE.value
        medication.completed_at = None
        schedule_changed = True

    if schedule_changed:
        rebuild_doses(db, medication, from_time=now_local())

    if "appointment_ids" in data:
        _sync_appointment_links(db, medication, data.get("appointment_ids"))

    medication.updated_at = now_local()
    db.flush()
    return medication


def delete_medication(db: Session, medication_id: int) -> None:
    """Hard delete. Doses cascade; appointments themselves are kept."""
    medication = get_medication(db, medication_id)
    image_path = medication.image_path
    medication.appointments.clear()
    db.delete(medication)
    db.flush()
    _remove_image(image_path)


def _remove_image(image_path: str | None) -> None:
    """Best-effort cleanup of an uploaded picture (never fails the request)."""
    if not image_path:
        return
    from app.config import UPLOAD_DIR

    try:
        candidate = (UPLOAD_DIR / image_path).resolve()
        if candidate.parent == UPLOAD_DIR.resolve() and candidate.is_file():
            candidate.unlink()
    except OSError:
        pass


def suspend_medication(db: Session, medication_id: int) -> Medication:
    medication = get_medication(db, medication_id)
    medication.status = MedicationStatus.SUSPENDED.value
    medication.suspended_at = now_local()
    clear_upcoming_doses(db, medication)
    db.flush()
    return medication


def resume_medication(db: Session, medication_id: int) -> Medication:
    medication = get_medication(db, medication_id)
    medication.suspended_at = None
    if medication.end_date < now_local().date():
        medication.status = MedicationStatus.COMPLETED.value
        medication.completed_at = now_local()
    else:
        medication.status = MedicationStatus.ACTIVE.value
        medication.completed_at = None
        rebuild_doses(db, medication, from_time=now_local())
    db.flush()
    return medication


def complete_medication(db: Session, medication_id: int) -> Medication:
    medication = get_medication(db, medication_id)
    medication.status = MedicationStatus.COMPLETED.value
    medication.completed_at = now_local()
    clear_upcoming_doses(db, medication)
    db.flush()
    return medication


def _sync_appointment_links(db: Session, medication: Medication, appointment_ids) -> None:
    if appointment_ids is None:
        return
    ids = parse_id_list(appointment_ids, "appointment_ids")
    appointments = (
        list(db.execute(select(Appointment).where(Appointment.id.in_(ids))).scalars().all())
        if ids
        else []
    )
    medication.appointments = appointments


# --------------------------------------------------------------------------- #
# Doses
# --------------------------------------------------------------------------- #
def set_dose_status(db: Session, dose_id: int, status: str) -> MedicationDose:
    allowed = {
        DoseStatus.TAKEN.value,
        DoseStatus.SKIPPED.value,
        DoseStatus.SCHEDULED.value,
        DoseStatus.MISSED.value,
    }
    if status not in allowed:
        raise ValidationError({"status": "error.validation"})
    dose = db.get(MedicationDose, dose_id)
    if dose is None:
        raise NotFoundError()
    dose.status = status
    dose.marked_at = None if status == DoseStatus.SCHEDULED.value else now_local()
    db.flush()
    return dose


def dose_counts(medication: Medication) -> dict[str, int]:
    counts = {status.value: 0 for status in DoseStatus}
    for dose in medication.doses:
        counts[dose.status] = counts.get(dose.status, 0) + 1
    counts["total"] = len(medication.doses)
    return counts


# --------------------------------------------------------------------------- #
# Serialization (language-neutral: the frontend renders the labels)
# --------------------------------------------------------------------------- #
def serialize_medication(
    medication: Medication,
    *,
    include_doses: bool = False,
    reference: datetime | None = None,
) -> dict:
    reference = reference or now_local()
    upcoming = next_dose_for(medication, reference)
    data = {
        "id": medication.id,
        "name": medication.name,
        "image_url": f"/static/uploads/{medication.image_path}" if medication.image_path else None,
        "dose_amount": medication.dose_amount,
        "dose_unit": medication.dose_unit,
        "quantity": medication.quantity,
        "form": medication.form,
        "comments": medication.comments,
        "start_date": iso(medication.start_date),
        "end_date": iso(medication.end_date),
        "frequency_hours": medication.frequency_hours,
        "first_dose_time": medication.first_dose_time.strftime("%H:%M"),
        "status": medication.status,
        "suspended_at": iso(medication.suspended_at),
        "completed_at": iso(medication.completed_at),
        "created_at": iso(medication.created_at),
        "next_dose": serialize_dose(upcoming) if upcoming else None,
        "counts": dose_counts(medication),
        "days_remaining": (medication.end_date - reference.date()).days,
        "appointments": [
            {
                "id": appointment.id,
                "doctor_name": appointment.doctor_name,
                "scheduled_at": iso(appointment.scheduled_at),
            }
            for appointment in medication.appointments
        ],
    }
    if include_doses:
        data["doses"] = [serialize_dose(dose) for dose in sorted(medication.doses, key=lambda d: d.scheduled_at)]
    return data


def serialize_dose(dose: MedicationDose, medication: Medication | None = None) -> dict:
    medication = medication or dose.medication
    return {
        "id": dose.id,
        "medication_id": dose.medication_id,
        "medication_name": medication.name if medication else None,
        "dose_amount": medication.dose_amount if medication else None,
        "dose_unit": medication.dose_unit if medication else None,
        "quantity": medication.quantity if medication else None,
        "form": medication.form if medication else None,
        "image_url": (
            f"/static/uploads/{medication.image_path}"
            if medication and medication.image_path
            else None
        ),
        "scheduled_at": iso(dose.scheduled_at),
        "status": dose.status,
        "marked_at": iso(dose.marked_at),
    }
