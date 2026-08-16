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
    # A dose whose time had already passed when the medication was added to the
    # application. It is history, not a failure: the user could not have marked
    # it, so it is never reminded about, never counted as missed, and never
    # changes on its own afterwards.
    BEFORE_REGISTRATION = "before_registration"


class ReminderKind(str, enum.Enum):
    DAYS_3 = "days_3"
    DAY_1 = "day_1"
    HOURS_3 = "hours_3"


class NotificationType(str, enum.Enum):
    DOSE = "dose"
    APPOINTMENT = "appointment"


class DoseNotificationKind(str, enum.Enum):
    """The six reminders around a dose, plus the one when it goes overdue."""

    BEFORE_30 = "before_30"
    BEFORE_15 = "before_15"
    BEFORE_5 = "before_5"
    AT_TIME = "at_time"
    AFTER_15 = "after_15"
    AFTER_30 = "after_30"
    OVERDUE = "overdue"
    SNOOZE = "snooze"


class Doctor(Base):
    __tablename__ = "doctors"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    occupation: Mapped[str | None] = mapped_column(String(160))
    phone: Mapped[str | None] = mapped_column(String(60))
    notes: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_local)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=now_local, onupdate=now_local
    )

    appointments: Mapped[list["Appointment"]] = relationship(
        back_populates="doctor",
        order_by="Appointment.scheduled_at.desc()",
        passive_deletes=True,
    )


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

    # Optional since v2: only name, frequency and start date are required.
    # "500" + "mg" -> displayed as "500 mg"
    # No column defaults on purpose: "not specified" has to survive as NULL.
    dose_amount: Mapped[str | None] = mapped_column(String(40))
    dose_unit: Mapped[str | None] = mapped_column(String(20))
    # 1 + "capsule" -> displayed as "1 capsule" / "1 cápsula" (translated key)
    quantity: Mapped[float | None] = mapped_column(Float)
    form: Mapped[str | None] = mapped_column(String(30))

    comments: Mapped[str | None] = mapped_column(Text)

    start_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    # NULL = open-ended treatment. Doses are then generated on a rolling
    # horizon (see config.DOSE_HORIZON_DAYS) and topped up by the scheduler.
    end_date: Mapped[date | None] = mapped_column(Date, index=True)
    frequency_hours: Mapped[int] = mapped_column(Integer, nullable=False)
    # Own copy of the first-dose time so a medication always knows its schedule
    # even if the global default changes later.
    first_dose_time: Mapped[time] = mapped_column(Time, nullable=False)

    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=MedicationStatus.ACTIVE.value, index=True
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
    # Set by "remind me later". It moves the REMINDER only: `scheduled_at`
    # above is the historical record and is never touched by a snooze.
    snoozed_until: Mapped[datetime | None] = mapped_column(DateTime, index=True)
    # When the user actually pressed Taken / Skipped (never set automatically).
    marked_at: Mapped[datetime | None] = mapped_column(DateTime)
    # When the status last changed, whoever changed it — including the
    # automatic move to "missed", which `marked_at` deliberately does not cover.
    status_changed_at: Mapped[datetime | None] = mapped_column(DateTime)
    notified_at: Mapped[datetime | None] = mapped_column(DateTime)

    medication: Mapped["Medication"] = relationship(back_populates="doses")


class Appointment(Base):
    __tablename__ = "appointments"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Since v2 an appointment belongs to a Doctor record; the doctor's name and
    # phone are stored once, on the doctor, and never copied here.
    doctor_id: Mapped[int] = mapped_column(
        ForeignKey("doctors.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    scheduled_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    location: Mapped[str | None] = mapped_column(String(200))
    treatment: Mapped[str | None] = mapped_column(String(300))
    notes: Mapped[str | None] = mapped_column(Text)
    # Informational follow-up date given by the doctor. Creating a real
    # appointment from it is a one-click action in the UI.
    next_appointment_at: Mapped[datetime | None] = mapped_column(DateTime)
    # Set when the user says "this is a follow-up of ..." while creating the
    # appointment. Only an appointment scheduled earlier can be chosen.
    follow_up_of_id: Mapped[int | None] = mapped_column(
        ForeignKey("appointments.id", ondelete="SET NULL"), index=True
    )

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
    doctor: Mapped["Doctor"] = relationship(back_populates="appointments")

    # The earlier appointment this one follows up on, and the later ones that
    # follow up on this one.
    follow_up_of: Mapped["Appointment | None"] = relationship(
        back_populates="follow_ups", remote_side="Appointment.id"
    )
    follow_ups: Mapped[list["Appointment"]] = relationship(
        back_populates="follow_up_of",
        order_by="Appointment.scheduled_at",
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
    # Which of the six dose offsets (or which appointment reminder) this is.
    kind: Mapped[str | None] = mapped_column(String(20))
    # "dose:41:before_30" — unique, so a scheduler restart can never queue the
    # same reminder twice (INSERT is guarded by this constraint).
    dedupe_key: Mapped[str | None] = mapped_column(String(80), unique=True, index=True)
    fire_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_local)

    # Language-independent payload; the text is rendered at delivery time using
    # the language that is active then.
    title_key: Mapped[str] = mapped_column(String(80), nullable=False)
    body_key: Mapped[str] = mapped_column(String(80), nullable=False)
    payload: Mapped[str | None] = mapped_column(Text)  # JSON

    windows_sent_at: Mapped[datetime | None] = mapped_column(DateTime)
    browser_delivered_at: Mapped[datetime | None] = mapped_column(DateTime)
    email_sent_at: Mapped[datetime | None] = mapped_column(DateTime)
    error: Mapped[str | None] = mapped_column(Text)
    # v3: the in-app notification centre keeps its own read state, separate
    # from whether a channel managed to deliver the alert.
    read_at: Mapped[datetime | None] = mapped_column(DateTime, index=True)
    # v4.1: the RFC 5322 Message-ID this notification was e-mailed under. Every
    # reminder for one dose quotes the earlier ones in In-Reply-To/References,
    # which is what makes a mail client group them into a single conversation —
    # one thread per dose, never one per medication.
    email_message_id: Mapped[str | None] = mapped_column(String(200), index=True)


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

    # --- channels (independent of each other) ---
    windows_notifications: Mapped[bool] = mapped_column(Boolean, default=True)
    browser_notifications: Mapped[bool] = mapped_column(Boolean, default=True)
    email_notifications: Mapped[bool] = mapped_column(Boolean, default=False)

    # --- what produces reminders ---
    medication_reminders: Mapped[bool] = mapped_column(Boolean, default=True)
    appointment_reminders: Mapped[bool] = mapped_column(Boolean, default=True)

    appt_reminder_days_3: Mapped[bool] = mapped_column(Boolean, default=True)
    appt_reminder_day_1: Mapped[bool] = mapped_column(Boolean, default=True)
    appt_reminder_hours_3: Mapped[bool] = mapped_column(Boolean, default=True)

    # --- the six reminders around each dose, individually switchable ---
    dose_before_30: Mapped[bool] = mapped_column(Boolean, default=True)
    dose_before_15: Mapped[bool] = mapped_column(Boolean, default=True)
    dose_before_5: Mapped[bool] = mapped_column(Boolean, default=True)
    dose_at_time: Mapped[bool] = mapped_column(Boolean, default=True)
    dose_after_15: Mapped[bool] = mapped_column(Boolean, default=True)
    dose_after_30: Mapped[bool] = mapped_column(Boolean, default=True)
    dose_overdue: Mapped[bool] = mapped_column(Boolean, default=True)

    # --- SMTP ---
    # The password is NEVER stored here in clear text. On Windows it is
    # encrypted with DPAPI (only this Windows account can read it back); see
    # app/utils/secretstore.py.
    email_recipient: Mapped[str | None] = mapped_column(String(320))
    email_sender: Mapped[str | None] = mapped_column(String(320))
    smtp_host: Mapped[str | None] = mapped_column(String(200))
    smtp_port: Mapped[int] = mapped_column(Integer, nullable=False, default=587)
    smtp_username: Mapped[str | None] = mapped_column(String(320))
    smtp_password_protected: Mapped[str | None] = mapped_column(Text)
    smtp_security: Mapped[str] = mapped_column(
        String(10), nullable=False, default="starttls"
    )  # "starttls" | "ssl" | "none"

    # --- appearance (v3) ---
    theme: Mapped[str] = mapped_column(
        String(10), nullable=False, default="system"
    )  # "system" | "light" | "dark"

    # --- notification centre (v3) ---
    notification_history_days: Mapped[int] = mapped_column(
        Integer, nullable=False, default=90
    )

    # --- backups (v3) ---
    backup_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    backup_frequency: Mapped[str] = mapped_column(
        String(10), nullable=False, default="daily"
    )  # "daily" | "weekly"
    backup_time: Mapped[time] = mapped_column(Time, nullable=False, default=time(1, 0))
    backup_keep: Mapped[int] = mapped_column(Integer, nullable=False, default=7)
    # NULL = the default folder, data/backups.
    backup_location: Mapped[str | None] = mapped_column(Text)
    last_backup_at: Mapped[datetime | None] = mapped_column(DateTime)

    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=now_local, onupdate=now_local
    )
