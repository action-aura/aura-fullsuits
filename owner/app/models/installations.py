"""Installation, device, and activation-event models (Part D: INSTALLATIONS AND DEVICES).

No raw hardware identifiers (IMEI, full MAC, geolocation) are ever stored -- only
generated installation IDs and privacy-safe fingerprint hashes (Part P)."""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPKMixin


class Installation(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "owner_installations"
    __table_args__ = (
        # Phase 9R M4: every activation and check-in/refresh request looks
        # up an installation by exactly this pair
        # (app/licensing_service/activation.py) -- previously an unindexed
        # full-table scan on every request. Found by inspecting real query
        # patterns against pg_indexes, not assumed.
        Index("ix_owner_installations_license_id_installation_label", "license_id", "installation_label"),
        # AUDIT-033 fix round (verifier V finding #1): the shop-authority
        # registry device id is pinned to ONE installation per licence among
        # installations that can still speak. A DEACTIVATED/REPLACED
        # installation frees the id, so a re-activated or slot-replaced
        # computer that keeps its app data can pin its own id again. Keep
        # this predicate identical to migration 5f2c8e1a9b37 and to
        # licensing_service/registry_pin.py::TERMINAL_STATUSES.
        Index(
            "uq_owner_installations_license_registry_device", "license_id", "registry_device_id", unique=True,
            postgresql_where=text("registry_device_id IS NOT NULL AND status NOT IN ('DEACTIVATED','REPLACED')"),
        ),
    )

    customer_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_customers.id"), nullable=False
    )
    subscription_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_subscriptions.id")
    )
    license_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("owner_licenses.id"))
    product_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("owner_products.id"), nullable=False)
    platform_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_platforms.id"), nullable=False
    )
    installation_label: Mapped[str | None] = mapped_column(String(128))
    device_label: Mapped[str | None] = mapped_column(String(128))
    os_version: Mapped[str | None] = mapped_column(String(64))
    app_version: Mapped[str | None] = mapped_column(String(64))
    release_channel_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_release_channels.id")
    )
    status: Mapped[str] = mapped_column(String(32), default="REGISTERED", nullable=False)
    first_registered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_check_in_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    activation_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    deactivated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    support_notes: Mapped[str | None] = mapped_column(Text)
    fingerprint_hash: Mapped[str | None] = mapped_column(String(128))  # privacy-safe hash placeholder, not raw HW ID
    device_public_key: Mapped[str | None] = mapped_column(Text)  # future device-identity placeholder
    # AUDIT-033 fix round (verifier V finding #1): the computer's shop-authority
    # registry device id (retail local_device_uuid), pinned by Owner on the
    # first activation/check-in that claims it and echoed in every signed lease
    # as `registry_device_id`. Receivers trust the id only because Owner signed
    # it. Immutable once set (registry_pin.py rule 3).
    registry_device_id: Mapped[str | None] = mapped_column(String(64))
    registry_device_id_pinned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    customer: Mapped["Customer"] = relationship()  # noqa: F821
    product: Mapped["Product"] = relationship()  # noqa: F821
    platform: Mapped["Platform"] = relationship()  # noqa: F821 -- added Phase 6, no schema change (FK already existed)
    license: Mapped["License | None"] = relationship()  # noqa: F821 -- added Phase 6, no schema change (FK already existed)
    devices: Mapped[list["DeviceRecord"]] = relationship(back_populates="installation", cascade="all, delete-orphan")
    # No delete-orphan on the audit-trail relationships (status_history,
    # activation_events) -- must never silently vanish if an Installation
    # row is ever deleted.
    status_history: Mapped[list["InstallationStatusHistory"]] = relationship(back_populates="installation")
    activation_events: Mapped[list["ActivationEvent"]] = relationship(back_populates="installation")


class InstallationStatusHistory(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "owner_installation_status_history"

    installation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_installations.id"), nullable=False
    )
    from_status: Mapped[str | None] = mapped_column(String(32))
    to_status: Mapped[str] = mapped_column(String(32), nullable=False)
    changed_by_staff_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_staff_users.id")
    )
    reason: Mapped[str | None] = mapped_column(Text)

    installation: Mapped[Installation] = relationship(back_populates="status_history")


class DeviceRecord(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "owner_device_records"

    installation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_installations.id"), nullable=False
    )
    device_label: Mapped[str | None] = mapped_column(String(128))
    fingerprint_hash: Mapped[str | None] = mapped_column(String(128))
    registered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    replaced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    installation: Mapped[Installation] = relationship(back_populates="devices")


class ActivationEvent(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "owner_activation_events"

    license_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("owner_licenses.id"))
    installation_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_installations.id")
    )
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    result: Mapped[str] = mapped_column(String(32), nullable=False)
    reason_code: Mapped[str | None] = mapped_column(String(64))
    correlation_id: Mapped[str | None] = mapped_column(String(128))
    originating_staff_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_staff_users.id")
    )
    safe_metadata: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    event_schema_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    installation: Mapped[Installation | None] = relationship(back_populates="activation_events")
    license: Mapped["License | None"] = relationship()  # noqa: F821
