"""Medication CRUD, status transitions and dose marking."""

from __future__ import annotations

from datetime import date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.config import (
    DOSE_NOTIFICATION_OFFSETS,
    FORM_OPTIONS,
    FREQUENCY_OPTIONS,
    MAX_TREATMENT_DAYS,
    SNOOZE_OPTIONS,
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
    registered_at,
    requires_complete_confirmation,
    taken_confirmation_threshold,
)
from app.services.settings_service import get_settings
from app.utils.timeutil import iso, now_local, parse_date, parse_time

SCHEDULE_FIELDS = ("start_date", "end_date", "frequency_hours", "first_dose_time")

# How far ahead of a dose its first reminder appears, and therefore the earliest
# moment at which there is anything to snooze.
EARLIEST_DOSE_REMINDER_MINUTES = -min(minutes for _kind, minutes in DOSE_NOTIFICATION_OFFSETS)


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
    """Normalise and validate a medication payload. Raises ValidationError.

    Since v2 only three things are required: name, frequency and start date.
    Dose, unit, quantity, form, comments, image and end date are all optional,
    and this check is enforced here in the backend, not only by the browser.
    """
    fields: dict[str, str] = {}
    clean: dict = {}

    # Collapse any interior whitespace, line breaks included: the name travels
    # into e-mail subjects and Windows toasts, where a line break is either
    # rejected or is an injection vector.
    name = " ".join((data.get("name") or "").split())
    if not name:
        fields["name"] = "validation.name_required"
    clean["name"] = name[:160]

    dose_amount = str(data.get("dose_amount") or "").strip()
    clean["dose_amount"] = dose_amount[:40] or None

    dose_unit = (data.get("dose_unit") or "").strip().lower()
    clean["dose_unit"] = dose_unit if dose_unit in UNIT_OPTIONS else ("mg" if dose_amount else None)

    raw_quantity = data.get("quantity")
    if raw_quantity in (None, ""):
        clean["quantity"] = None
    else:
        try:
            quantity = float(str(raw_quantity).replace(",", "."))
            if quantity <= 0:
                raise ValueError
            clean["quantity"] = quantity
        except (TypeError, ValueError):
            fields["quantity"] = "validation.quantity_positive"
            clean["quantity"] = None

    form = (data.get("form") or "").strip().lower()
    if form:
        clean["form"] = form if form in FORM_OPTIONS else "other"
    else:
        clean["form"] = "other" if clean["quantity"] else None

    comments = data.get("comments")
    clean["comments"] = (comments or "").strip()[:2000] or None

    start_date = _safe_date(
        data.get("start_date"), fields, "start_date", "validation.start_date_required"
    )
    # Optional since v2: no end date means an open-ended treatment.
    try:
        end_date = parse_date(data.get("end_date"))
    except (TypeError, ValueError):
        end_date = None
        fields["end_date"] = "validation.date_invalid"

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
        # Set explicitly rather than left to the column default: this instant is
        # domain data, not bookkeeping. It is what separates the doses the
        # application could remind about from the ones that had already passed
        # when the treatment was written down.
        created_at=now_local(),
    )
    db.add(medication)
    db.flush()

    # Build the full schedule first, then decide the status: a treatment that is
    # entered after it ended still gets its dose history.
    rebuild_doses(db, medication, from_time=None)
    if medication.end_date is not None and medication.end_date < now_local().date():
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
        and (clean["end_date"] is None or clean["end_date"] >= now_local().date())
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
    # The dose rows are about to be deleted and SQLite recycles their ids, so
    # their notifications have to go too - otherwise the next medication would
    # inherit their dedupe keys and their e-mail conversation.
    _purge_notifications(db, [dose.id for dose in medication.doses])
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
    _cancel_notifications(db, [dose.id for dose in medication.doses])
    clear_upcoming_doses(db, medication)
    db.flush()
    return medication


def resume_medication(db: Session, medication_id: int) -> Medication:
    medication = get_medication(db, medication_id)
    medication.suspended_at = None
    if medication.end_date is not None and medication.end_date < now_local().date():
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
    _cancel_notifications(db, [dose.id for dose in medication.doses])
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
    now = now_local()

    # A dose that predates its medication's registration can be recorded as
    # taken or skipped — it is the user's own history to write down — but it can
    # never go back to "scheduled" or forward to "missed". Either would hand it
    # to the overdue sweep or brand it a failure, which is exactly what this
    # status exists to prevent.
    if (
        status in (DoseStatus.SCHEDULED.value, DoseStatus.MISSED.value)
        and dose.scheduled_at < registered_at(dose.medication)
    ):
        status = DoseStatus.BEFORE_REGISTRATION.value

    dose.status = status
    # `marked_at` records a human decision; `status_changed_at` records any
    # change, including the automatic one to "missed".
    dose.marked_at = (
        None
        if status in (DoseStatus.SCHEDULED.value, DoseStatus.BEFORE_REGISTRATION.value)
        else now
    )
    dose.status_changed_at = now
    if status != DoseStatus.SCHEDULED.value:
        # Handling the dose also withdraws any reminder that was queued but not
        # yet shown, so nothing arrives after you have already acted.
        _cancel_notifications(db, [dose.id])
    db.flush()
    return dose


def _cancel_notifications(db: Session, dose_ids: list[int]) -> None:
    from app.notifications.dispatcher import cancel_pending_dose_notifications

    cancel_pending_dose_notifications(db, dose_ids)


def _purge_notifications(db: Session, dose_ids: list[int]) -> None:
    """For doses whose rows are being deleted, not merely resolved."""
    from app.notifications.dispatcher import purge_dose_notifications

    purge_dose_notifications(db, dose_ids)


def snooze_dose(db: Session, dose_id: int, minutes: int) -> MedicationDose:
    """Push the REMINDER back, never the dose.

    `scheduled_at` is the historical record of when the dose was due and is
    deliberately untouched; only `snoozed_until` moves. The dose also stays
    pending - snoozing is not a way of saying "taken".
    """
    if minutes not in SNOOZE_OPTIONS:
        raise ValidationError({"minutes": "validation.snooze_invalid"})
    dose = db.get(MedicationDose, dose_id)
    if dose is None:
        raise NotFoundError()
    if dose.status != DoseStatus.SCHEDULED.value:
        raise ValidationError({"status": "validation.snooze_not_pending"})

    now = now_local()
    # "Remind me later" only makes sense once the reminders have started. A dose
    # three days out has nothing to postpone, and snoozing it would produce a
    # "time for your medication" alert days before the dose is due.
    if not can_snooze(dose, now):
        raise ValidationError({"status": "validation.snooze_not_due_yet"})

    dose.snoozed_until = now + timedelta(minutes=minutes)
    # Withdraw whatever was already queued for it, so the snooze is honoured
    # immediately rather than after one more reminder slips out.
    _cancel_notifications(db, [dose.id])
    db.flush()
    return dose


def dose_counts(medication: Medication) -> dict[str, int]:
    counts = {status.value: 0 for status in DoseStatus}
    for dose in medication.doses:
        counts[dose.status] = counts.get(dose.status, 0) + 1
    counts["total"] = len(medication.doses)
    # Everything the application could actually ask about. Split by *when the
    # dose was due* rather than by its current status: recording that you did
    # take one of the historical doses must not quietly move it into the
    # denominator of your adherence.
    registered = registered_at(medication)
    counts["manageable"] = sum(
        1 for dose in medication.doses if dose.scheduled_at >= registered
    )
    return counts


def compliance(medication: Medication) -> dict | None:
    """How many of the doses the application could manage were taken.

    Bookkeeping, not a medical judgement: it counts what you told the
    application, over the doses the application was in a position to remind you
    about. Doses due before the medication was registered are excluded from both
    sides — they are history the app never saw, and that stays true even after
    you record what actually happened on one of them. Doses still in the future
    are excluded too, because they have not happened yet.

    The split is by *when the dose was due*, not by its current status, so
    writing down a historical dose cannot change the number.

    Returns None while there is nothing resolved to measure.
    """
    registered = registered_at(medication)
    resolvable = {
        DoseStatus.TAKEN.value,
        DoseStatus.SKIPPED.value,
        DoseStatus.MISSED.value,
    }

    managed = [dose for dose in medication.doses if dose.scheduled_at >= registered]
    taken = sum(1 for dose in managed if dose.status == DoseStatus.TAKEN.value)
    resolved = sum(1 for dose in managed if dose.status in resolvable)
    if resolved == 0:
        return None
    return {
        "taken": taken,
        "resolved": resolved,
        "percent": round(taken / resolved * 100),
        "before_registration": sum(
            1 for dose in medication.doses if dose.scheduled_at < registered
        ),
    }


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
        "compliance": compliance(medication),
        "days_remaining": (
            None
            if medication.end_date is None
            else (medication.end_date - reference.date()).days
        ),
        "open_ended": medication.end_date is None,
        "progress": treatment_progress(medication, reference.date()),
        # The frontend shows the "finish early?" confirmation from this flag
        # instead of re-deriving the rule in JavaScript.
        "needs_complete_confirmation": requires_complete_confirmation(
            medication, reference.date()
        ),
        "appointments": [
            {
                "id": appointment.id,
                "doctor_id": appointment.doctor_id,
                "doctor_name": appointment.doctor.name if appointment.doctor else None,
                "scheduled_at": iso(appointment.scheduled_at),
            }
            for appointment in medication.appointments
        ],
    }
    if include_doses:
        data["doses"] = [serialize_dose(dose) for dose in sorted(medication.doses, key=lambda d: d.scheduled_at)]
    return data


def treatment_progress(medication: Medication, today: date | None = None) -> dict | None:
    """How far through the configured treatment period today is.

    Calendar time only. This says nothing about whether the treatment is
    working - it is the elapsed portion of the dates the user entered, and
    open-ended treatments have no percentage at all.
    """
    if medication.end_date is None:
        return None
    today = today or now_local().date()
    total_days = (medication.end_date - medication.start_date).days + 1
    if total_days <= 0:
        return None
    elapsed = (today - medication.start_date).days + 1
    current = min(max(elapsed, 0), total_days)
    return {
        "current_day": current,
        "total_days": total_days,
        "days_remaining": max((medication.end_date - today).days, 0),
        "percent": round(current / total_days * 100),
        "started": medication.start_date.isoformat(),
        "ends": medication.end_date.isoformat(),
        "not_started": today < medication.start_date,
        "finished": today > medication.end_date,
    }


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
        "status_changed_at": iso(dose.status_changed_at),
        # A snooze moves the reminder only; scheduled_at above is unchanged.
        "snoozed_until": iso(dose.snoozed_until),
        # From this instant onwards, "Taken" needs no confirmation. The UI
        # compares the current clock against it; the rule itself lives in
        # app/services/scheduling.py and is covered by the tests.
        "confirm_taken_before": iso(taken_confirmation_threshold(dose.scheduled_at)),
        # Whether "remind me later" is available at all. The same rule the
        # service enforces, so the button is never offered for a request the
        # backend would refuse.
        "can_snooze": can_snooze(dose),
    }


def can_snooze(dose: MedicationDose, reference: datetime | None = None) -> bool:
    """A pending dose whose reminders have already started."""
    if dose.status != DoseStatus.SCHEDULED.value:
        return False
    now = reference or now_local()
    return dose.scheduled_at <= now + timedelta(minutes=EARLIEST_DOSE_REMINDER_MINUTES)
