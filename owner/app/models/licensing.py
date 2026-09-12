"""License domain models (Part D: LICENSE DOMAIN). Full plaintext key is never stored (ADR-9)."""
from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import Date, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPKMixin


class License(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "owner_licenses"

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
