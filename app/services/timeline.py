"""Medical timeline.

A chronological reading of what already exists — appointments with their
doctor, the medications prescribed at them and the follow-up links, plus the
day each treatment began — with no new storage of its own. Nothing here
duplicates data; it only joins it.

Two kinds of entry share the list and are distinguished by `type`:

* `appointment` — a visit, exactly as before
* `treatment`   — the day a medication's treatment started

A treatment entry carries how many of its doses predate the day the medication
was added to the application, because that is the part of the history the
application itself never saw and the user would otherwise have no explanation
for.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.models.models import Appointment, DoseStatus, Medication, MedicationDose
from app.utils.timeutil import combine, iso, now_local

ORDERS = ("newest", "oldest")
SCOPES = ("all", "upcoming", "past")
# What kinds of entry to include; the frontend exposes this as a filter.
KINDS = ("all", "appointments", "treatments")

# Like every other list in the app, the timeline is read one page at a time
# rather than loading a whole medical history into one response.
DEFAULT_LIMIT = 100
MAX_LIMIT = 500

# How many of a treatment's pre-registration doses to name in the entry itself.
# The rest are one click away on the medication's own screen.
SAMPLE_DOSES = 3


def build_timeline(
    db: Session,
    order: str = "newest",
    scope: str = "all",
    doctor_id: int | None = None,
    medication_id: int | None = None,
    limit: int = DEFAULT_LIMIT,
    offset: int = 0,
    kind: str = "all",
) -> dict:
    now = now_local()
    order = order if order in ORDERS else "newest"
    scope = scope if scope in SCOPES else "all"
    kind = kind if kind in KINDS else "all"
    limit = min(max(int(limit or DEFAULT_LIMIT), 1), MAX_LIMIT)
    offset = max(int(offset or 0), 0)

    # A doctor filter is a question about visits, so it excludes treatments,
    # which belong to no doctor directly.
    want_appointments = kind in ("all", "appointments")
    want_treatments = kind in ("all", "treatments") and not doctor_id

    entries: list[dict] = []
    total = 0
    # Each side reads at most one page's worth of rows, so merging two ordered
    # sources stays bounded no matter how long the history is.
    window = offset + limit

    if want_appointments:
        count, rows = _appointments(db, now, order, scope, doctor_id, medication_id, window)
        total += count
        entries.extend(rows)

    if want_treatments:
        count, rows = _treatments(db, now, order, scope, medication_id, window)
        total += count
        entries.extend(rows)

    # The merge key must be a *total* order, and the same one each source used
    # in SQL. Sorting by the instant alone would let two entries that share it
    # be truncated by one rule and merged by another, which silently drops
    # entries from every page.
    entries.sort(
        key=lambda item: (item["sort_at"], item["type"], item["id"]),
        reverse=(order == "newest"),
    )
    page = entries[offset : offset + limit]
    _attach_history(db, page)
    for entry in page:
        entry.pop("sort_at", None)

    return {
        "order": order,
        "scope": scope,
        "kind": kind,
        "entries": page,
        "count": len(page),
        "total": total,
        "limit": limit,
        "offset": offset,
        "has_more": offset + len(page) < total,
    }


# --------------------------------------------------------------------------- #
# Appointments
# --------------------------------------------------------------------------- #
def _appointments(
    db: Session,
    now: datetime,
    order: str,
    scope: str,
    doctor_id: int | None,
    medication_id: int | None,
    window: int,
) -> tuple[int, list[dict]]:
    stmt = select(Appointment).options(
        selectinload(Appointment.doctor),
        selectinload(Appointment.medications),
        selectinload(Appointment.follow_up_of).selectinload(Appointment.doctor),
        selectinload(Appointment.follow_ups).selectinload(Appointment.doctor),
    )
    if doctor_id:
        stmt = stmt.where(Appointment.doctor_id == doctor_id)
    if medication_id:
        stmt = stmt.where(Appointment.medications.any(id=medication_id))
    if scope == "upcoming":
        stmt = stmt.where(Appointment.scheduled_at >= now)
    elif scope == "past":
        stmt = stmt.where(Appointment.scheduled_at < now)

    total = db.execute(
        select(func.count()).select_from(stmt.order_by(None).subquery())
    ).scalar_one()

    # Ordered by the same total key the merge uses, so the top `window` rows of
    # this source really are the top `window` of it.
    keys = (Appointment.scheduled_at, Appointment.id)
    stmt = stmt.order_by(
        *[key.desc() for key in keys] if order == "newest" else keys
    ).limit(window)

    entries = []
    for appointment in db.execute(stmt).scalars().unique().all():
        entries.append(
            {
                "type": "appointment",
                "sort_at": appointment.scheduled_at,
                "id": appointment.id,
                "date": appointment.scheduled_at.date().isoformat(),
                "datetime": iso(appointment.scheduled_at),
                "is_past": appointment.scheduled_at < now,
                "doctor": {
                    "id": appointment.doctor_id,
                    "name": appointment.doctor.name if appointment.doctor else None,
                    "occupation": appointment.doctor.occupation if appointment.doctor else None,
                },
                "treatment": appointment.treatment,
                "notes": appointment.notes,
                "location": appointment.location,
                "medications": [
                    {"id": m.id, "name": m.name, "status": m.status}
                    for m in appointment.medications
                ],
                "follow_up_of": _brief(appointment.follow_up_of),
                "follow_ups": [_brief(item) for item in appointment.follow_ups],
            }
        )
    return total, entries


# --------------------------------------------------------------------------- #
# Treatments
# --------------------------------------------------------------------------- #
def _treatments(
    db: Session,
    now: datetime,
    order: str,
    scope: str,
    medication_id: int | None,
    window: int,
) -> tuple[int, list[dict]]:
    """One entry per treatment, on the day it started."""
    stmt = select(Medication)
    if medication_id:
        stmt = stmt.where(Medication.id == medication_id)
    # A treatment's place in time is the moment of its first dose, which is what
    # the entry reports as `is_past`. Comparing only the date would put a
    # treatment that began at 09:00 this morning in "upcoming" at midday.
    started = func.datetime(Medication.start_date, Medication.first_dose_time)
    if scope == "upcoming":
        stmt = stmt.where(started >= now.strftime("%Y-%m-%d %H:%M:%S"))
    elif scope == "past":
        stmt = stmt.where(started < now.strftime("%Y-%m-%d %H:%M:%S"))

    total = db.execute(
        select(func.count()).select_from(stmt.order_by(None).subquery())
    ).scalar_one()

    # start_date alone is a *date*: two treatments that begin the same day would
    # be cut in an order the merge does not agree with. Order by the whole key.
    keys = (Medication.start_date, Medication.first_dose_time, Medication.id)
    stmt = stmt.order_by(
        *[key.desc() for key in keys] if order == "newest" else keys
    ).limit(window)

    entries = []
    for medication in db.execute(stmt).scalars().unique().all():
        started_at = combine(medication.start_date, medication.first_dose_time)
        entries.append(
            {
                "type": "treatment",
                "sort_at": started_at,
                "id": medication.id,
                "date": medication.start_date.isoformat(),
                "datetime": iso(started_at),
                "is_past": started_at < now,
                "name": medication.name,
                "status": medication.status,
                "start_date": iso(medication.start_date),
                "end_date": iso(medication.end_date),
                "frequency_hours": medication.frequency_hours,
                # When the treatment was actually entered into the application.
                # Different from start_date whenever it was recorded late.
                "registered_at": iso(medication.created_at),
                "started_before_registration": bool(
                    medication.created_at
                    and started_at < medication.created_at
                ),
                # Filled in by _attach_history for the entries that survive
                # paging, so a deep offset does not count doses nobody will see.
                "before_registration": {"count": 0, "first": []},
                "href": f"/medications/{medication.id}",
            }
        )
    return total, entries


def _attach_history(db: Session, page: list[dict]) -> None:
    """How much of each treatment on this page predates its registration.

    Done after paging and with one grouped count for the whole page, rather than
    two queries per medication in the window - most of which would have been
    thrown away by the slice.
    """
    ids = [entry["id"] for entry in page if entry["type"] == "treatment"]
    if not ids:
        return

    counts = dict(
        db.execute(
            select(MedicationDose.medication_id, func.count())
            .where(
                MedicationDose.medication_id.in_(ids),
                MedicationDose.status == DoseStatus.BEFORE_REGISTRATION.value,
            )
            .group_by(MedicationDose.medication_id)
        ).all()
    )
    if not counts:
        return

    for entry in page:
        count = counts.get(entry["id"], 0) if entry["type"] == "treatment" else 0
        if not count:
            continue
        sample = (
            db.execute(
                select(MedicationDose.scheduled_at)
                .where(
                    MedicationDose.medication_id == entry["id"],
                    MedicationDose.status == DoseStatus.BEFORE_REGISTRATION.value,
                )
                .order_by(MedicationDose.scheduled_at)
                .limit(SAMPLE_DOSES)
            )
            .scalars()
            .all()
        )
        entry["before_registration"] = {
            "count": count,
            "first": [iso(value) for value in sample],
        }


def _brief(appointment: Appointment | None) -> dict | None:
    if appointment is None:
        return None
    return {
        "id": appointment.id,
        "date": appointment.scheduled_at.date().isoformat(),
        "datetime": iso(appointment.scheduled_at),
        "doctor_name": appointment.doctor.name if appointment.doctor else None,
    }
