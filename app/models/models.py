"""Database model.

TIME POLICY (see README section "Time and timezone")
----------------------------------------------------
Every date/time stored in this database is *naive local wall-clock time* of the
machine that runs the application. Nothing is ever converted to UTC. A dose
scheduled for 10:00 PM is stored as `22:00` and is shown, notified and compared
as `22:00`. This is deliberate: the app is a single-user local tool, and
wall-clock semantics are what a person means when they say "every 8 hours
starting at 10:00 AM".
"""

from __future__ import annotations

import enum
from datetime import date, datetime, time

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    Time,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from app.utils.timeutil import now_local


class Base(DeclarativeBase):
    pass


class MedicationStatus(str, enum.Enum):
    ACTIVE = "active"
    COMPLETED = "completed"
    SUSPENDED = "suspended"


class DoseStatus(str, enum.Enum):
    SCHEDULED = "scheduled"
    TAKEN = "taken"
    SKIPPED = "skipped"
    MISSED = "missed"


class ReminderKind(str, enum.Enum):
    DAYS_3 = "days_3"
    DAY_1 = "day_1"
    HOURS_3 = "hours_3"


class NotificationType(str, enum.Enum):
    DOSE = "dose"
    APPOINTMENT = "appointment"


class AppointmentMedication(Base):
    """Join table: which medications were prescribed at which appointment."""

    __tablename__ = "appointment_medications"

    appointment_id: Mapped[int] = mapped_column(
        ForeignKey("appointments.id", ondelete="CASCADE"), primary_key=True
    )
    medication_id: Mapped[int] = mapped_column(
        ForeignKey("medications.id", ondelete="CASCADE"), primary_key=True
    )


class Medication(Base):
    __tablename__ = "medications"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    image_path: Mapped[str | None] = mapped_column(String(300))

    # "500" + "mg" -> displayed as "500 mg"
    dose_amount: Mapped[str] = mapped_column(String(40), nullable=False)
    dose_unit: Mapped[str] = mapped_column(String(20), nullable=False, default="mg")
    # 1 + "capsule" -> displayed as "1 capsule" / "1 cápsula" (translated key)
    quantity: Mapped[float] = mapped_column(Float, nullable=False, default=1)
    form: Mapped[str] = mapped_column(String(30), nullable=False, default="tablet")

    comments: Mapped[str | None] = mapped_column(Text)

    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)
    frequency_hours: Mapped[int] = mapped_column(Integer, nullable=False)
    # Own copy of the first-dose time so a medication always knows its schedule
    # even if the global default changes later.
    first_dose_time: Mapped[time] = mapped_column(Time, nullable=False)

    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=MedicationStatus.ACTIVE.value
    )
    suspended_at: Mapped[datetime | None] = mapped_column(DateTime)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_local)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=now_local, onupdate=now_local
    )

    doses: Mapped[list["MedicationDose"]] = relationship(
        back_populates="medication",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="MedicationDose.scheduled_at",
    )
    appointments: Mapped[list["Appointment"]] = relationship(
        secondary="appointment_medications",
        back_populates="medications",
    )


class MedicationDose(Base):
    __tablename__ = "medication_doses"
    __table_args__ = (
        UniqueConstraint("medication_id", "scheduled_at", name="uq_dose_slot"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    medication_id: Mapped[int] = mapped_column(
        ForeignKey("medications.id", ondelete="CASCADE"), nullable=False, index=True
    )
    scheduled_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=DoseStatus.SCHEDULED.value
    )
    # When the user actually pressed Taken / Skipped (never set automatically).
    marked_at: Mapped[datetime | None] = mapped_column(DateTime)
    notified_at: Mapped[datetime | None] = mapped_column(DateTime)

    medication: Mapped["Medication"] = relationship(back_populates="doses")


class Appointment(Base):
    __tablename__ = "appointments"

    id: Mapped[int] = mapped_column(primary_key=True)
    doctor_name: Mapped[str] = mapped_column(String(160), nullable=False)
    scheduled_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    location: Mapped[str | None] = mapped_column(String(200))
    treatment: Mapped[str | None] = mapped_column(String(300))
    notes: Mapped[str | None] = mapped_column(Text)
    # Informational follow-up date given by the doctor. Creating a real
    # appointment from it is a one-click action in the UI.
    next_appointment_at: Mapped[datetime | None] = mapped_column(DateTime)

    reminder_days_3: Mapped[bool] = mapped_column(Boolean, default=True)
    reminder_day_1: Mapped[bool] = mapped_column(Boolean, default=True)
    reminder_hours_3: Mapped[bool] = mapped_column(Boolean, default=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_local)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=now_local, onupdate=now_local
    )

    reminders: Mapped[list["AppointmentReminder"]] = relationship(
        back_populates="appointment",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="AppointmentReminder.remind_at",
    )
    medications: Mapped[list["Medication"]] = relationship(
        secondary="appointment_medications",
        back_populates="appointments",
    )


class AppointmentReminder(Base):
    __tablename__ = "appointment_reminders"
    __table_args__ = (
        UniqueConstraint("appointment_id", "kind", name="uq_appointment_reminder"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    appointment_id: Mapped[int] = mapped_column(
        ForeignKey("appointments.id", ondelete="CASCADE"), nullable=False, index=True
    )
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    remind_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime)

    appointment: Mapped["Appointment"] = relationship(back_populates="reminders")


class Notification(Base):
    """Queue of notifications produced by the background scheduler.

    Rows survive restarts, so the browser can pick up anything it missed while
    it was closed, and the Windows toast is fired exactly once.
    """

    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(primary_key=True)
    type: Mapped[str] = mapped_column(String(20), nullable=False)
    reference_id: Mapped[int | None] = mapped_column(Integer)
    fire_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_local)

    # Language-independent payload; the text is rendered at delivery time using
    # the language that is active then.
    title_key: Mapped[str] = mapped_column(String(80), nullable=False)
    body_key: Mapped[str] = mapped_column(String(80), nullable=False)
    payload: Mapped[str | None] = mapped_column(Text)  # JSON

    windows_sent_at: Mapped[datetime | None] = mapped_column(DateTime)
    browser_delivered_at: Mapped[datetime | None] = mapped_column(DateTime)
    error: Mapped[str | None] = mapped_column(Text)


class Settings(Base):
    """Single-row table (id == 1) holding all user preferences."""

    __tablename__ = "settings"

    id: Mapped[int] = mapped_column(primary_key=True, default=1)

    # NULL means "follow the browser language".
    language: Mapped[str | None] = mapped_column(String(5))
    default_first_dose_time: Mapped[time] = mapped_column(
        Time, nullable=False, default=time(10, 0)
    )
    ending_soon_days: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    # Minutes after a scheduled time before an unmarked dose becomes "missed".
    missed_after_minutes: Mapped[int] = mapped_column(
        Integer, nullable=False, default=120
    )

    windows_notifications: Mapped[bool] = mapped_column(Boolean, default=True)
    browser_notifications: Mapped[bool] = mapped_column(Boolean, default=True)
    medication_reminders: Mapped[bool] = mapped_column(Boolean, default=True)
    appointment_reminders: Mapped[bool] = mapped_column(Boolean, default=True)

    appt_reminder_days_3: Mapped[bool] = mapped_column(Boolean, default=True)
    appt_reminder_day_1: Mapped[bool] = mapped_column(Boolean, default=True)
    appt_reminder_hours_3: Mapped[bool] = mapped_column(Boolean, default=True)

    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=now_local, onupdate=now_local
    )
