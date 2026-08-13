"""Manual activation approval and device-slot governance (Phase 8 Parts O/P,
Milestone 5)."""
from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import Date, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPKMixin

ACTIVATION_MODES = ("AUTOMATIC", "MANUAL_APPROVAL", "RISK_REVIEW")
PENDING_ACTIVATION_STATUSES = ("PENDING_REVIEW", "APPROVED", "REJECTED")

# Phase 9.5A Milestone 3 -- multi-device licensing policy. WINDOWS/ANDROID/IOS
# are per-platform limits; MOBILE is the combined Android+iOS cap the
# governing spec's own example needs ("mobile category maximum: 1").
DEVICE_POLICY_PLATFORM_CATEGORIES = ("WINDOWS", "ANDROID", "IOS", "MOBILE")


class ActivationPolicy(Base, UUIDPKMixin, TimestampMixin):
    """Part O: configurable activation mode. Resolved per-product via
    `commercial_ops/activation_policy.py`'s `resolve_activation_mode()` --
    the same product-specific-then-global-default lookup pattern as
    `CommercialPolicy` (Milestone 3) -- deliberately a lookup, not a stored
    assignment on Installation/License, so correcting a policy retroactively
    affects every future activation without a migration touching existing
    rows.

    `AUTOMATIC` (what every existing installation was always activated
    under, and what resolution returns when no row exists at all) preserves
    the exact Phase 6/7 activation behavior: a validated request is
    approved and signed in the same call, no review step. `MANUAL_APPROVAL`
    and `RISK_REVIEW` both hold a newly-registered installation in
    `PENDING_ACTIVATION` and create a `PendingActivation` row instead of
    signing immediately -- `RISK_REVIEW` exists as a distinct value so a
    future risk-scoring signal can route only flagged requests for review
    while `MANUAL_APPROVAL` gates every new activation unconditionally;
    this milestone treats them identically (both gate every new
    registration), leaving the distinction itself for whichever milestone
    adds real risk scoring.

    Only ever gates a BRAND-NEW installation registration that would
    otherwise consume a fresh device slot -- an installation reactivating
    (same device retrying, or a routine check-in) never re-enters review
    once it has already been approved once.
    """

    __tablename__ = "owner_activation_policies"

    policy_code: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    product_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("owner_products.id"))
    mode: Mapped[str] = mapped_column(String(16), nullable=False)
    effective_date: Mapped[date] = mapped_column(Date, nullable=False)
    retired_date: Mapped[date | None] = mapped_column(Date)
    notes: Mapped[str | None] = mapped_column(Text)
    created_by_staff_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_staff_users.id")
    )


class PendingActivation(Base, UUIDPKMixin, TimestampMixin):
    """Part O. One open row per gated installation registration (UNIQUE on
    `installation_id`) -- `licensing_service/activation.py` creates this
    instead of signing an assertion when `ActivationPolicy` resolves to
    `MANUAL_APPROVAL`/`RISK_REVIEW`. Never stores the license key or any
    secret -- only the already-resolved `license_id`/`installation_id`,
    exactly like `owner_activation_requests`.

    Deliberately does NOT store a pre-built signed assertion: approving
    only flips `Installation.status` `PENDING_ACTIVATION -> ACTIVE` (a
    plain, already-declared `installations.services` transition); the next
    real check-in builds and signs the assertion fresh from current
    database state, the same "never cached, always resolved fresh"
    principle documented in `commercial_ops/renewal_requests.py`'s module
    docstring.
    """

    __tablename__ = "owner_pending_activations"

    installation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_installations.id"), unique=True, nullable=False
    )
    license_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("owner_licenses.id"), nullable=False)
    product_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("owner_products.id"), nullable=False)
    platform_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_platforms.id"), nullable=False
    )
    request_id: Mapped[str | None] = mapped_column(String(128))
    correlation_id: Mapped[str | None] = mapped_column(String(128))
    device_key_fingerprint: Mapped[str | None] = mapped_column(String(64))
    # Snapshot of the mode that gated this request, for audit -- not a live
    # reference to ActivationPolicy, which may change or retire afterward.
    mode: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="PENDING_REVIEW", nullable=False)
    decided_by_staff_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_staff_users.id")
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decision_reason: Mapped[str | None] = mapped_column(Text)

    installation: Mapped["Installation"] = relationship()  # noqa: F821
    license: Mapped["License"] = relationship()  # noqa: F821


class DeviceSlotException(Base, UUIDPKMixin, TimestampMixin):
    """Part P: a temporary, explicit, reasoned increase to a license's
    effective device limit -- e.g. "customer replacing hardware fleet, +2
    slots for 14 days." Never edits `License.device_limit` itself (that
    stays the permanent, contractual figure);
    `device_slot_ops.resolve_effective_device_limit()` adds every
    currently-active exception's `extra_slots` on top of it at
    activation-check time. Time-boxed by design, same "no hidden/indefinite
    extension" spirit as `EmergencyExtension` (Part N) even though this
    isn't a commercial extension -- `expires_at` is required and capped
    (see `MAX_DEVICE_SLOT_EXCEPTION_DAYS` in `device_slot_ops.py`), no
    indefinite exceptions.
    """

    __tablename__ = "owner_device_slot_exceptions"

    license_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("owner_licenses.id"), nullable=False)
    extra_slots: Mapped[int] = mapped_column(Integer, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="ACTIVE", nullable=False)  # ACTIVE / REVOKED
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_by_staff_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_staff_users.id")
    )
    revocation_reason: Mapped[str | None] = mapped_column(Text)
    created_by_staff_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_staff_users.id"), nullable=False
    )

    license: Mapped["License"] = relationship()  # noqa: F821


class DevicePolicyProfile(Base, UUIDPKMixin, TimestampMixin):
    """Phase 9.5A Milestone 3. A resolved LOOKUP, not a stored per-license
    assignment -- mirrors ActivationPolicy's own product-specific-then-
    global-default pattern exactly (plan_id NULL = global default). Answers
    "what platform combinations does this plan allow" -- a genuinely new
    question. Never replaces or recomputes License.device_limit /
    resolve_effective_device_limit(), which remain the only real enforcement
    authority (not wired into the live activation path this phase -- see
    docs/owner/phase9_5a/multi-device-policy-design.md's explicit
    enforcement-wiring boundary)."""

    __tablename__ = "owner_device_policy_profiles"

    profile_code: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    plan_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("owner_plans.id"))
    max_total_devices: Mapped[int | None] = mapped_column(Integer)
    approval_required_after_device_number: Mapped[int | None] = mapped_column(Integer)
    effective_date: Mapped[date] = mapped_column(Date, nullable=False)
    retired_date: Mapped[date | None] = mapped_column(Date)
    notes: Mapped[str | None] = mapped_column(Text)
    created_by_staff_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_staff_users.id")
    )

    platform_rules: Mapped[list["DevicePolicyPlatformRule"]] = relationship()


class DevicePolicyPlatformRule(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "owner_device_policy_platform_rules"

    device_policy_profile_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_device_policy_profiles.id"), nullable=False
    )
    platform_category: Mapped[str] = mapped_column(String(16), nullable=False)
    max_devices: Mapped[int] = mapped_column(Integer, nullable=False)


class SubscriptionDevicePolicyOverride(Base, UUIDPKMixin, TimestampMixin):
    """A subscription-level override always needs a named approver (unlike
    the plan-level default, which is just commercial configuration) --
    approved_by_staff_user_id is required, not nullable."""

    __tablename__ = "owner_subscription_device_policy_overrides"

    subscription_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_subscriptions.id"), nullable=False
    )
    max_total_devices: Mapped[int | None] = mapped_column(Integer)
    approval_required_after_device_number: Mapped[int | None] = mapped_column(Integer)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    approved_by_staff_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_staff_users.id"), nullable=False
    )
    effective_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    effective_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by_staff_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_staff_users.id")
    )
