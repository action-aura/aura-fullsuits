"""Phase 9.5A Milestone 14 -- shared management notes. Genuinely separate
from CustomerNote (owner/app/models/customers.py), which stays
customer-scoped. See docs/owner/phase9_5a/management-notes-design.md."""
from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPKMixin

MANAGEMENT_NOTE_STATUSES = ("OPEN", "IN_PROGRESS", "DONE", "ARCHIVED")
MANAGEMENT_NOTE_VISIBILITIES = ("MANAGEMENT_ONLY", "SPECIFIC_EMPLOYEES", "ALL_STAFF")


class SharedManagementNote(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "owner_shared_management_notes"

    title: Mapped[str] = mapped_column(String(200), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str | None] = mapped_column(String(64))
    priority: Mapped[str] = mapped_column(String(8), default="MEDIUM", nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="OPEN", nullable=False, index=True)
    pinned: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_by_staff_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_staff_users.id"), nullable=False, index=True
    )
    updated_by_staff_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_staff_users.id")
    )
    assigned_employee_profile_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_employee_profiles.id"), index=True
    )
    visibility: Mapped[str] = mapped_column(String(24), default="MANAGEMENT_ONLY", nullable=False, index=True)
    due_date: Mapped[date | None] = mapped_column(Date)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ManagementNoteVisibilityGrant(Base, UUIDPKMixin, TimestampMixin):
    """Only populated when SharedManagementNote.visibility == SPECIFIC_EMPLOYEES."""

    __tablename__ = "owner_management_note_visibility_grants"

    management_note_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_shared_management_notes.id"), nullable=False, index=True
    )
    employee_profile_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_employee_profiles.id"), nullable=False, index=True
    )


class ManagementNoteComment(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "owner_management_note_comments"

    management_note_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_shared_management_notes.id"), nullable=False, index=True
    )
    author_staff_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_staff_users.id"), nullable=False
    )
    body: Mapped[str] = mapped_column(Text, nullable=False)
