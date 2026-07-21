"""Audit and operations models (Part D: AUDIT AND OPERATIONS)."""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPKMixin


class AuditLog(Base, UUIDPKMixin, TimestampMixin):
    """Append-only. The ONLY writer is app.audit.services.record() -- see ADR-8.
    No route/service updates or deletes a row here; there is deliberately no
    SQLAlchemy relationship or route exposing UPDATE/DELETE on this table."""

    __tablename__ = "owner_audit_log"

    actor_staff_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_staff_users.id")
    )
    actor_role_snapshot: Mapped[str | None] = mapped_column(String(255))
    action_code: Mapped[str] = mapped_column(String(96), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_public_id: Mapped[str | None] = mapped_column(String(64))
    correlation_id: Mapped[str | None] = mapped_column(String(128))
    ip_address: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(String(512))
    result: Mapped[str] = mapped_column(String(32), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)
    before_state_redacted: Mapped[dict | None] = mapped_column(JSONB)
    after_state_redacted: Mapped[dict | None] = mapped_column(JSONB)
    previous_hash: Mapped[str | None] = mapped_column(String(64))
    current_hash: Mapped[str] = mapped_column(String(64), nullable=False)


class SecurityEvent(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "owner_security_events"

    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    staff_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("owner_staff_users.id"))
    ip_address: Mapped[str | None] = mapped_column(String(64))
    detail: Mapped[str | None] = mapped_column(Text)


class SystemSetting(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "owner_system_settings"

    key: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    value: Mapped[dict] = mapped_column(JSONB, nullable=False)
    updated_by_staff_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_staff_users.id")
    )


class DatabaseBackupRecord(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "owner_database_backup_records"

    file_path: Mapped[str] = mapped_column(Text, nullable=False)
    owner_app_version: Mapped[str | None] = mapped_column(String(32))
    schema_revision: Mapped[str | None] = mapped_column(String(64))
    checksum_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    size_bytes: Mapped[int | None] = mapped_column()
    status: Mapped[str] = mapped_column(String(32), nullable=False)  # SUCCESS / FAILED
    initiated_by_staff_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_staff_users.id")
    )
    error_detail: Mapped[str | None] = mapped_column(Text)
