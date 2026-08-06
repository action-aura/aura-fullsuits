import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, String, BigInteger, Index
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.extensions import Base
from app.models.base import TimestampMixin


class SyncEvent(Base, TimestampMixin):
    """Append-only log of business-data mutations shared across every
    device activated against the same license. `seq` (not `created_at`,
    which is client-supplied and untrusted for ordering) is the sole
    ordering authority every client cursors against."""

    __tablename__ = "owner_sync_events"

    # Client-generated (not server default) -- this IS the idempotency key
    # for POST /api/sync/v1/push: a retried push with the same id must be
    # a no-op, never a second row with a new seq.
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    seq: Mapped[int] = mapped_column(BigInteger, autoincrement=True, unique=True, nullable=False)
    license_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("owner_licenses.id"), nullable=False)
    device_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    event_type: Mapped[str] = mapped_column(String(16), nullable=False)  # create|update|delete
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    client_created_at: Mapped[datetime] = mapped_column(nullable=False)

    __table_args__ = (
        Index("ix_owner_sync_events_license_seq", "license_id", "seq"),
    )
