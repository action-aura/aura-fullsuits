"""License domain models (Part D: LICENSE DOMAIN). Full plaintext key is never stored (ADR-9)."""
from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import CheckConstraint, Date, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPKMixin


class License(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "owner_licenses"
    __table_args__ = (
        # Typed extras (migration a8f2c5d9e317): never negative; device_limit stays the total.
        CheckConstraint("extra_desktop_slots >= 0", name="ck_owner_licenses_extra_desktop_slots_nonneg"),
        CheckConstraint("extra_phone_slots >= 0", name="ck_owner_licenses_extra_phone_slots_nonneg"),
    )

    customer_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_customers.id"), nullable=False
    )
    subscription_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_subscriptions.id"), nullable=False
    )
    product_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("owner_products.id"), nullable=False)
    plan_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("owner_plans.id"), nullable=False)
    allowed_platforms: Mapped[str] = mapped_column(String(128), nullable=False)  # comma-separated platform codes
    allowed_release_channel_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_release_channels.id")
    )
    device_limit: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    # Owner decision 2026-10-31: extras are strictly typed. A package includes
    # (device_limit - these two) devices of ANY kind; each bought extra desktop
    # lets one MORE Windows device in, each bought extra phone one MORE Android
    # device -- never the other way round. device_limit stays the TOTAL every
    # existing reader uses; these only say how many of the extras are typed.
    # Rule in commercial_ops/device_slot_ops.py::fits_typed_slots.
    extra_desktop_slots: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    extra_phone_slots: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    valid_from: Mapped[date | None] = mapped_column(Date)
    valid_until: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(32), default="DRAFT", nullable=False)
    issued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    issuing_staff_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_staff_users.id")
    )
    key_prefix: Mapped[str | None] = mapped_column(String(32))  # e.g. AURA-CLN-1 -- safe to display always
    key_suffix_masked: Mapped[str | None] = mapped_column(String(16))  # last 4 chars only, e.g. ****WXYZ
    # HMAC-SHA256(pepper, secret) -- never reversible. Unique+indexed as of
    # Phase 6: activation must look up a license by its submitted key's HMAC
    # (Postgres allows multiple NULLs under a unique constraint, so DRAFT
    # licenses with no issued key yet are unaffected).
    key_secret_hmac: Mapped[str | None] = mapped_column(String(128), unique=True)
    key_format_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    replaced_by_license_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("owner_licenses.id"))
    revocation_reason: Mapped[str | None] = mapped_column(Text)
    activation_policy_identifier: Mapped[str] = mapped_column(String(64), default="offline-first-v1", nullable=False)

    customer: Mapped["Customer"] = relationship()  # noqa: F821
    subscription: Mapped["Subscription"] = relationship()  # noqa: F821
    product: Mapped["Product"] = relationship()  # noqa: F821
    plan: Mapped["Plan"] = relationship()  # noqa: F821
    allowed_release_channel: Mapped["ReleaseChannel | None"] = relationship()  # noqa: F821 -- added Phase 6, no schema change
    # No delete-orphan on the audit-trail relationships (status_history,
    # issuance_events) -- these must never silently vanish if a License row
    # is ever deleted. entitlements is normal child detail, not a history
    # trail, so it keeps delete-orphan.
    status_history: Mapped[list["LicenseStatusHistory"]] = relationship(back_populates="license")
    entitlements: Mapped[list["LicenseEntitlement"]] = relationship(
        back_populates="license", cascade="all, delete-orphan"
    )
    issuance_events: Mapped[list["LicenseKeyIssuanceEvent"]] = relationship(back_populates="license")


class LicenseStatusHistory(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "owner_license_status_history"

    # AUDIT-perf: looked up per suspended license by
    # app/attention/service.py::_suspended_license_items() (one batched
    # `IN (...)` query per render, not one per license) -- indexed to match
    # PaymentAllocation's FK columns; see migration
    # 24d38372230e_license_status_history_missing_index.py.
    license_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_licenses.id"), nullable=False, index=True
    )
    from_status: Mapped[str | None] = mapped_column(String(32))
    to_status: Mapped[str] = mapped_column(String(32), nullable=False)
    changed_by_staff_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_staff_users.id")
    )
    reason: Mapped[str | None] = mapped_column(Text)

    license: Mapped[License] = relationship(back_populates="status_history")


class LicenseEntitlement(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "owner_license_entitlements"

    # AUDIT-perf: indexed to match PaymentAllocation's FK columns and
    # LicenseStatusHistory.license_id; see migration
    # d8dfeb46d1d6_license_entitlement_and_issuance_event_missing_indexes.py.
    license_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_licenses.id"), nullable=False, index=True
    )
    entitlement_definition_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_entitlement_definitions.id"), nullable=False
    )
    value: Mapped[dict] = mapped_column(JSONB, nullable=False)

    license: Mapped[License] = relationship(back_populates="entitlements")
    entitlement_definition: Mapped["EntitlementDefinition"] = relationship()  # noqa: F821


class LicenseKeyIssuanceEvent(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "owner_license_key_issuance_events"

    # AUDIT-perf: indexed to match PaymentAllocation's FK columns and
    # LicenseStatusHistory.license_id; see migration
    # d8dfeb46d1d6_license_entitlement_and_issuance_event_missing_indexes.py.
    license_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_licenses.id"), nullable=False, index=True
    )
    issued_by_staff_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_staff_users.id"), nullable=False
    )
    idempotency_key: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    key_prefix: Mapped[str] = mapped_column(String(32), nullable=False)
    key_format_version: Mapped[int] = mapped_column(Integer, nullable=False)

    license: Mapped[License] = relationship(back_populates="issuance_events")


class LicenseScheduledStop(Base, UUIDPKMixin, TimestampMixin):
    """Step 0 (2026-10-24, revision 38a13f8b2f8e) -- "a refunded licence keeps
    working 2 days, then stops" (the owner's rule), as a STORED moment, not a
    timer: confirm_refund writes the row (effective_at = the refund's paid_at
    + 2 days); app/licensing/scheduled_stops.py::apply_due_licence_stops moves
    the licence (and its subscription) to to_status once that moment has
    passed. It is applied by the product's own check-in and activation for
    that licence before they read its status -- so the shop learns at its
    first check-in after the moment even if no job ran -- and by the
    `flask commercial apply-licence-stops` job for everything else (the
    Owner screens). applied_at makes it happen once.

    Step 0 fix round (2026-10-25, revision f16f0245da18): a refund of only the
    extra-device (pair) money keeps the licence but takes those devices away
    after the same 2 days -- such a row has no to_status and names
    devices_removed. One CHECK: a stop (SUSPENDED, no devices) or a device
    removal (no status, devices > 0), never both, never neither."""

    __tablename__ = "owner_license_scheduled_stops"
    __table_args__ = (
        UniqueConstraint("source_refund_id", "license_id", name="uq_owner_license_scheduled_stops_refund_license"),
        CheckConstraint(
            "(to_status IS NOT NULL AND to_status = 'SUSPENDED' AND devices_removed IS NULL) "
            "OR (to_status IS NULL AND devices_removed IS NOT NULL AND devices_removed > 0)",
            name="ck_owner_license_scheduled_stops_to_status",
        ),
        Index(
            "ix_owner_license_scheduled_stops_due", "effective_at",
            postgresql_where=text("applied_at IS NULL AND cancelled_at IS NULL"),
        ),
    )

    license_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_licenses.id"), nullable=False, index=True
    )
    subscription_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("owner_subscriptions.id"))
    effective_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    to_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    devices_removed: Mapped[int | None] = mapped_column(nullable=True)
    # Typed split of devices_removed (migration a8f2c5d9e317): how many of the
    # removed devices were extra desktops / extra phones. devices_removed stays
    # the total; any remainder is untyped (single EXTRA_DEVICE) capacity.
    desktops_removed: Mapped[int | None] = mapped_column(nullable=True)
    phones_removed: Mapped[int | None] = mapped_column(nullable=True)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    source_refund_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_commercial_refunds.id"), nullable=False
    )
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by_staff_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_staff_users.id")
    )