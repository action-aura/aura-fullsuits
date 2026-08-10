"""Phase 9.5A Milestone 15 -- daily activity snapshot. No foreign key into
any other table -- Reporting reads everything, nothing reads it back
(docs/owner/phase9_5a/domain-dependency-rules.md). See
docs/owner/phase9_5a/daily-reporting-contract.md."""
from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import Date, DateTime, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPKMixin

SNAPSHOT_GENERATION_STATUSES = ("PENDING", "GENERATING", "COMPLETE", "FAILED")
SNAPSHOT_GENERATED_BY = ("SCHEDULER", "MANUAL")


class DailyActivitySnapshot(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "owner_daily_activity_snapshots"

    business_date: Mapped[date] = mapped_column(Date, unique=True, nullable=False)
    timezone: Mapped[str] = mapped_column(String(32), default="Asia/Amman", nullable=False)
    generation_status: Mapped[str] = mapped_column(String(16), default="PENDING", nullable=False)
    generated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    generated_by: Mapped[str] = mapped_column(String(16), default="SCHEDULER", nullable=False)
    generated_by_staff_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    source_range_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source_range_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    metric_payload: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    failure_reason: Mapped[str | None] = mapped_column(Text)
    rerun_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
