"""Commercial catalog models (Part D: COMMERCIAL CATALOG)."""
from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Numeric, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPKMixin


class Product(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "owner_products"

    product_code: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)  # AURA_RETAIL / AURA_CLINIC
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    commercial_status: Mapped[str] = mapped_column(String(32), default="PILOT", nullable=False)
    pilot_status: Mapped[str | None] = mapped_column(String(64))
    is_sellable: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)  # AURA_OWNER itself = False
    deprecated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Platform(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "owner_platforms"

    platform_code: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)  # WINDOWS / ANDROID
    name: Mapped[str] = mapped_column(String(128), nullable=False)


class ProductPlatform(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "owner_product_platforms"
    __table_args__ = (UniqueConstraint("product_id", "platform_id", name="uq_product_platform"),)

    product_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("owner_products.id"), nullable=False)
    platform_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_platforms.id"), nullable=False
    )
    supported: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    product: Mapped[Product] = relationship()
    platform: Mapped[Platform] = relationship()


class ReleaseChannel(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "owner_release_channels"

    channel_code: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)  # STABLE / RC / PILOT / BETA
    name: Mapped[str] = mapped_column(String(128), nullable=False)


class ProductVersion(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "owner_product_versions"
    __table_args__ = (UniqueConstraint("product_id", "platform_id", "version", name="uq_product_platform_version"),)

    product_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("owner_products.id"), nullable=False)
    platform_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_platforms.id"), nullable=False
    )
    release_channel_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_release_channels.id"), nullable=False
    )
    version: Mapped[str] = mapped_column(String(64), nullable=False)  # e.g. 1.0.0-rc.1
    schema_version: Mapped[int | None] = mapped_column()
    financial_contract_version: Mapped[str | None] = mapped_column(String(64))
    artifact_checksum_sha256: Mapped[str | None] = mapped_column(String(64))
    artifact_path: Mapped[str | None] = mapped_column(Text)
    release_notes: Mapped[str | None] = mapped_column(Text)
    is_current_stable: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_deprecated: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    imported_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # -- Phase 9R M10: product release authority --
    build_number: Mapped[int | None] = mapped_column()
    # Clients below this version are told an update is available but not
    # required (compared against the client's own reported app_version at
    # check-in/refresh time -- comparison logic lives in the licensing
    # service, not here; this column is just the published policy value).
    min_supported_version: Mapped[str | None] = mapped_column(String(64))
    # Clients below THIS version are told the update is mandatory. Always
    # >= min_supported_version when both are set (enforced by
    # publish_release(), not a DB constraint -- semver comparison isn't
    # expressible in SQL).
    forced_upgrade_threshold: Mapped[str | None] = mapped_column(String(64))
    artifact_size_bytes: Mapped[int | None] = mapped_column()
    # DRAFT: imported/created, not yet visible to any client or download
    # authorization check. PUBLISHED: live -- immutable from here on
    # (version/checksum/artifact_path/build_number never change after
    # publish; a correction is a new row, not an edit). WITHDRAWN: was
    # published, no longer authorized for new downloads or activations,
    # but existing installations already running it are not retroactively
    # broken (see private-distribution-contract.md).
    publication_state: Mapped[str] = mapped_column(String(16), default="DRAFT", nullable=False)
    created_by_staff_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("owner_staff_users.id"))
    published_by_staff_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("owner_staff_users.id"))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    withdrawn_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    withdrawn_reason: Mapped[str | None] = mapped_column(Text)

    product: Mapped[Product] = relationship()
    platform: Mapped[Platform] = relationship()
    release_channel: Mapped[ReleaseChannel] = relationship()


class Plan(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "owner_plans"

    plan_code: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    product_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("owner_products.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    lifecycle_status: Mapped[str] = mapped_column(String(32), default="DRAFT", nullable=False)
    billing_model: Mapped[str] = mapped_column(String(32), nullable=False)  # ONE_TIME/MONTHLY/ANNUAL/PILOT/CUSTOM
    billing_interval_months: Mapped[int | None] = mapped_column()
    currency: Mapped[str] = mapped_column(String(8), default="USD", nullable=False)
    included_device_count: Mapped[int] = mapped_column(default=1, nullable=False)
    max_device_count: Mapped[int | None] = mapped_column()
    support_level: Mapped[str] = mapped_column(String(32), default="STANDARD", nullable=False)
    release_channel_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_release_channels.id")
    )
    effective_date: Mapped[date | None] = mapped_column(Date)
    retirement_date: Mapped[date | None] = mapped_column(Date)

    product: Mapped[Product] = relationship()
    prices: Mapped[list["PlanPrice"]] = relationship(back_populates="plan", cascade="all, delete-orphan")


class PlanPrice(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "owner_plan_prices"

    plan_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("owner_plans.id"), nullable=False)
    base_price: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    setup_fee: Mapped[float] = mapped_column(Numeric(12, 2), default=0, nullable=False)
    currency: Mapped[str] = mapped_column(String(8), nullable=False)
    effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    effective_until: Mapped[date | None] = mapped_column(Date)  # NULL = still current; historical rows never overwritten

    plan: Mapped[Plan] = relationship(back_populates="prices")


class Addon(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "owner_addons"

    addon_code: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    product_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("owner_products.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    availability_status: Mapped[str] = mapped_column(String(32), default="DRAFT", nullable=False)
    price: Mapped[float | None] = mapped_column(Numeric(12, 2))
    currency: Mapped[str] = mapped_column(String(8), default="USD", nullable=False)

    product: Mapped[Product] = relationship()


class EntitlementDefinition(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "owner_entitlement_definitions"

    entitlement_code: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    value_type: Mapped[str] = mapped_column(String(16), nullable=False)  # boolean/integer/string/list/date
    description: Mapped[str | None] = mapped_column(Text)


class PlanEntitlement(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "owner_plan_entitlements"
    __table_args__ = (UniqueConstraint("plan_id", "entitlement_definition_id", name="uq_plan_entitlement"),)

    plan_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("owner_plans.id"), nullable=False)
    entitlement_definition_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_entitlement_definitions.id"), nullable=False
    )
    value: Mapped[dict] = mapped_column(JSONB, nullable=False)  # typed per EntitlementDefinition.value_type

    plan: Mapped[Plan] = relationship()
    entitlement_definition: Mapped[EntitlementDefinition] = relationship()


class AddonEntitlement(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "owner_addon_entitlements"
    __table_args__ = (UniqueConstraint("addon_id", "entitlement_definition_id", name="uq_addon_entitlement"),)

    addon_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("owner_addons.id"), nullable=False)
    entitlement_definition_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_entitlement_definitions.id"), nullable=False
    )
    value: Mapped[dict] = mapped_column(JSONB, nullable=False)

    addon: Mapped[Addon] = relationship()
    entitlement_definition: Mapped[EntitlementDefinition] = relationship()
