"""Phase 9.5A Milestone 4/5 -- employee profile and presence.

StaffUser (app/models/staff.py) remains the one login table (auth-only:
credentials, MFA, sessions, roles). EmployeeProfile is an additive
extension -- one profile per account (UNIQUE staff_user_id) -- carrying
employment/HR-shaped fields StaffUser deliberately never had. See
docs/owner/phase9_5a/employee-domain-model.md.

EmployeePresenceSession is a real-time operational signal only (see
docs/owner/phase9_5a/employee-presence-contract.md) -- presence STATE
(ONLINE/RECENTLY_ACTIVE/OFFLINE) is derived at query time from
last_seen_at, never stored here.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import Date, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPKMixin

EMPLOYMENT_STATUSES = ("PENDING", "ACTIVE", "SUSPENDED", "TERMINATED", "ARCHIVED")
PRESENCE_PLATFORMS = ("WEB", "ANDROID", "IOS")


class EmployeeProfile(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "owner_employee_profiles"

    staff_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_staff_users.id"), unique=True, nullable=False
    )
    employee_number: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    full_name: Mapped[str] = mapped_column(String(200), nullable=False)
    phone: Mapped[str | None] = mapped_column(String(32))
    job_title: Mapped[str | None] = mapped_column(String(120))
    department: Mapped[str | None] = mapped_column(String(120))
    employment_start_date: Mapped[date] = mapped_column(Date, nullable=False)
    employment_end_date: Mapped[date | None] = mapped_column(Date)
    employment_status: Mapped[str] = mapped_column(String(16), default="PENDING", nullable=False)
    manager_employee_profile_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_employee_profiles.id")
    )
    commission_plan_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_commission_plans.id")
    )
    profile_image_reference: Mapped[str | None] = mapped_column(String(512))
    # Management-only field -- serializers must omit this for a non-management
    # caller (docs/owner/phase9_5a/data-isolation-threat-model.md), the same
    # discipline as CustomerNote's own management-only note flag elsewhere.
    notes: Mapped[str | None] = mapped_column(Text)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    created_by_staff_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_staff_users.id")
    )
    updated_by_staff_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_staff_users.id")
    )

    manager: Mapped["EmployeeProfile | None"] = relationship(remote_side="EmployeeProfile.id")


class EmployeePresenceSession(Base, UUIDPKMixin, TimestampMixin):
    """No location field, no continuous telemetry -- see the module docstring
    and employee-presence-contract.md's own explicit exclusion list."""

    __tablename__ = "owner_employee_presence_sessions"

    employee_profile_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_employee_profiles.id"), nullable=False
    )
    staff_session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_staff_sessions.id"), nullable=False
    )
    app_instance_id: Mapped[str] = mapped_column(String(128), nullable=False)
    platform: Mapped[str] = mapped_column(String(16), nullable=False)
    app_version: Mapped[str | None] = mapped_column(String(32))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_activity_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    device_label: Mapped[str | None] = mapped_column(String(120))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
