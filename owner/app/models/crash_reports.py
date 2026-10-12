"""Crash reports received from installed products.

One row per (installation, fingerprint, app_version): the same bug on the same
build of the same till is a COUNT, not a new row. The product sends its running
total for that bug, so a retried batch cannot double-count -- intake takes
count = max(existing, incoming), never existing + incoming.

Everything here was scrubbed on the till before it was sent
(commercial_runtime/crash_reports/scrub.py): no customer data, no amounts, no
licence keys, no home paths. Owner re-validates shape and size on the way in
(app/crash_reports/intake.py) but cannot un-leak what a till should not have
sent, which is why the privacy tests live on the product side and in the
cross-stack test.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, UUIDPKMixin


class CrashReport(Base, UUIDPKMixin):
    __tablename__ = "owner_crash_reports"
    __table_args__ = (
        # The upsert key. Keep identical to the migration.
        UniqueConstraint("installation_id", "fingerprint", "app_version", name="uq_owner_crash_reports_install_fp_version"),
        # The staff page groups by fingerprint and sorts by last seen.
        Index("ix_owner_crash_reports_fingerprint", "fingerprint"),
        Index("ix_owner_crash_reports_last_seen_at", "last_seen_at"),
    )

    installation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_installations.id"), nullable=False
    )
    license_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("owner_licenses.id"))
    product_code: Mapped[str] = mapped_column(String(64), nullable=False)
    platform: Mapped[str] = mapped_column(String(32), nullable=False)
    app_version: Mapped[str] = mapped_column(String(64), nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(32), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)               # server | thread | client | diagnostics
    exception_type: Mapped[str] = mapped_column(String(200), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)                   # <= 300 chars, scrubbed on the till
    frames: Mapped[list] = mapped_column(JSONB, nullable=False)                  # [{file, function, line}], <= 30, innermost first
    route: Mapped[str | None] = mapped_column(String(200))
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    count: Mapped[int] = mapped_column(Integer, nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    installation = relationship("Installation")
    license = relationship("License")
