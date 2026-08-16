"""Global search.

Read-only by construction: this module issues SELECTs and nothing else, so a
search can never change data or trigger an action.

Plain SQL `LIKE` over a handful of columns is enough here — the whole database
is one person's medication history, so there is no scale that would justify
FTS5 or an index server.
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.config import SEARCH_LIMIT
from app.models.models import Appointment, Doctor, Medication
from app.utils.timeutil import iso, now_local

MIN_QUERY_LENGTH = 2


def search(db: Session, query: str) -> dict:
    """Results grouped by type. An empty or too-short query returns nothing."""
    raw = (query or "").strip()
    if len(raw) < MIN_QUERY_LENGTH:
        return _empty(raw)

    needle = f"%{raw.lower()}%"
    result = {
        "query": raw,
        "medications": _medications(db, needle),
        "doctors": _doctors(db, needle),
        "appointments": _appointments(db, needle, raw),
    }
    result["total"] = sum(len(result[key]) for key in ("medications", "doctors", "appointments"))
    return result


def _empty(raw: str) -> dict:
    return {"query": raw, "medications": [], "doctors": [], "appointments": [], "total": 0}


def _lower(column):
    return func.lower(func.coalesce(column, ""))


def _medications(db: Session, needle: str) -> list[dict]:
    rows = (
        db.execute(
            select(Medication)
            .where(
                or_(
                    _lower(Medication.name).like(needle),
                    _lower(Medication.comments).like(needle),
                    _lower(Medication.dose_amount).like(needle),
                )
            )
            .order_by(Medication.status, Medication.name)
            .limit(SEARCH_LIMIT)
        )
        .scalars()
        .unique()
        .all()
    )
    return [
        {
            "id": item.id,
            "name": item.name,
            "status": item.status,
            "dose_amount": item.dose_amount,
            "dose_unit": item.dose_unit,
            "quantity": item.quantity,
            "form": item.form,
            "frequency_hours": item.frequency_hours,
            "comments": item.comments,
            "href": f"/medications/{item.id}",
        }
        for item in rows
    ]


def _doctors(db: Session, needle: str) -> list[dict]:
    rows = (
        db.execute(
            select(Doctor)
            .options(selectinload(Doctor.appointments))
            .where(
                or_(
                    _lower(Doctor.name).like(needle),
                    _lower(Doctor.occupation).like(needle),
                    _lower(Doctor.phone).like(needle),
                    _lower(Doctor.notes).like(needle),
                )
            )
            .order_by(Doctor.name)
            .limit(SEARCH_LIMIT)
        )
        .scalars()
        .unique()
        .all()
    )
    return [
        {
            "id": item.id,
            "name": item.name,
            "occupation": item.occupation,
            "phone": item.phone,
            "appointment_count": len(item.appointments),
            "href": f"/doctors/{item.id}",
        }
        for item in rows
    ]


def _parse_date_query(raw: str) -> date | None:
    """Understand "2026-08-15" and "15/08/2026" so a date can be searched for.

    Month names are handled by the caller's text search instead, because they
    are language-dependent and the doctor/treatment text usually matches first.
    """
    text = raw.strip()
    for pattern in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(text, pattern).date()
        except ValueError:
            continue
    return None


def _appointments(db: Session, needle: str, raw: str) -> list[dict]:
    conditions = [
        _lower(Appointment.treatment).like(needle),
        _lower(Appointment.notes).like(needle),
        _lower(Appointment.location).like(needle),
        Appointment.doctor.has(_lower(Doctor.name).like(needle)),
        Appointment.doctor.has(_lower(Doctor.occupation).like(needle)),
    ]

    exact_date = _parse_date_query(raw)
    if exact_date is not None:
        conditions.append(func.date(Appointment.scheduled_at) == exact_date.isoformat())

    rows = (
        db.execute(
            select(Appointment)
            .options(selectinload(Appointment.doctor), selectinload(Appointment.medications))
            .where(or_(*conditions))
            .order_by(Appointment.scheduled_at.desc())
            .limit(SEARCH_LIMIT)
        )
        .scalars()
        .unique()
        .all()
    )
    now = now_local()
    return [
        {
            "id": item.id,
            "scheduled_at": iso(item.scheduled_at),
            "doctor_id": item.doctor_id,
            "doctor_name": item.doctor.name if item.doctor else None,
            "treatment": item.treatment,
            "is_past": item.scheduled_at < now,
            "medications": [{"id": m.id, "name": m.name} for m in item.medications],
            "href": f"/appointments/{item.id}",
        }
        for item in rows
    ]
