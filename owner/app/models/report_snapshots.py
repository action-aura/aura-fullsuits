"""Phase 9.5E -- scheduled operational report snapshots. Follows the same
architectural rule as the 9.5A DailyActivitySnapshot it complements: zero
outbound ForeignKey columns (actor/supersession references are plain UUIDs),
so Reporting stays a pure read-only consumer of every other subsystem and no
other table ever gains a dependency on it. See
docs/owner/phase9_5e/scheduled-report-snapshot-contract.md."""
from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import Date, DateTime, Index, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPKMixin

REPORT_TYPES = (
    "DAILY_OPERATIONAL_SUMMARY",
    "DAILY_CASH_CLOSING_EXCEPTIONS",
    "WEEKLY_OPERATIONAL_SUMMARY",
    "MONTHLY_OPERATIONAL_SUMMARY",
)
REPORT_SNAPSHOT_STATUSES = ("PUBLISHED", "SUPERSEDED")
REPORT_GENERATED_BY = ("SCHEDULER", "MANUAL")


class ReportSnapshot(Base, UUIDPKMixin, TimestampMixin):
    """Canonical key is (report_type, scope, period_start, period_end,
    currency, definition_version, snapshot_version) -- unique, enforced at
    the DB level so concurrent generation cannot create two snapshots for the
    same key. A correction never overwrites a PUBLISHED row; it inserts a new
    row with snapshot_version + 1 and flips the old row's status to
    SUPERSEDED (recorded via superseded_by_snapshot_id, a plain UUID, not a
    FK -- see module docstring)."""

    __tablename__ = "owner_report_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "report_type", "scope", "period_start", "period_end", "currency",
            "definition_version", "snapshot_version",
            name="uq_report_snapshot_canonical_key",
        ),
        Index("ix_report_snapshots_type_status", "report_type", "status"),
    )

    report_type: Mapped[str] = mapped_column(String(40), nullable=False)
    scope: Mapped[str] = mapped_column(String(64), default="GLOBAL", nullable=False)
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)
    currency: Mapped[str | None] = mapped_column(String(3))
    definition_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    snapshot_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="PUBLISHED", nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    generated_by: Mapped[str] = mapped_column(String(16), nullable=False)
    generated_by_staff_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    cutoff_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    request_id: Mapped[str | None] = mapped_column(String(64))
    superseded_by_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
