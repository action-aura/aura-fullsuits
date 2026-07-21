"""Installation, device, and activation-event models (Part D: INSTALLATIONS AND DEVICES).

No raw hardware identifiers (IMEI, full MAC, geolocation) are ever stored -- only
generated installation IDs and privacy-safe fingerprint hashes (Part P)."""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPKMixin


class Installation(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "owner_installations"

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

    customer: Mapped["Customer"] = relationship()  # noqa: F821
    product: Mapped["Product"] = relationship()  # noqa: F821
    devices: Mapped[list["DeviceRecord"]] = relationship(back_populates="installation", cascade="all, delete-orphan")
    status_history: Mapped[list["InstallationStatusHistory"]] = relationship(
        back_populates="installation", cascade="all, delete-orphan"
    )
    activation_events: Mapped[list["ActivationEvent"]] = relationship(
        back_populates="installation", cascade="all, delete-orphan"
    )


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
