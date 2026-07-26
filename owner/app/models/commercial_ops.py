"""Phase 8 commercial-operations models (Milestone 1).

Adds the renewal *workflow* record (`RenewalRequest`) on top of the Phase
5-7 foundation's `RenewalRecord` (which remains what it already was: the
"apply" step's own audit-style record of a completed renewal, created by
`record_renewal()`; see `owner/app/subscriptions/services.py`). This module
does not replace anything in `app/models/subscriptions.py` -- it adds the
request/approve/apply pipeline in front of it, per
docs/owner/phase8/phase8-scope-and-baseline.md's Milestone 1 scope.

Also adds `PaymentCorrectionHistory`, closing a gap found during Phase 8
Part A discovery: `correct_payment()` in `app/subscriptions/services.py`
mutates a `PaymentRecord` row in place with no separate history row, which
conflicts with the governing spec's Part F rule ("correcting a payment must
create a correction record or history entry... no hard overwrite of
verified financial metadata"). Milestone 1 adds the table; wiring
`correct_payment()` to write to it is also done in this milestone since it
is a small, self-contained, additive change with no behavior change to any
existing route's request/response shape.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPKMixin

# Canonical renewal-request states (Part C of the governing Phase 8 spec).
# Adapted to this codebase's existing naming style (upper-snake, matching
# Subscription.status / License.status / PaymentRecord.status).
RENEWAL_REQUEST_STATUSES = (
    "DRAFT",
    "QUOTED",
    "AWAITING_CONFIRMATION",
    "AWAITING_PAYMENT",
    "PAYMENT_RECORDED",
    "APPROVED",
    "APPLIED",
    "REJECTED",
    "CANCELLED",
    "VOIDED",
)


class RenewalRequest(Base, UUIDPKMixin, TimestampMixin):
    """A renewal *in progress* -- from first quote through to application.
    Distinct from `RenewalRecord` (the historical fact "a renewal was
    applied on this date, extending the term to X"), which this table's
    `applied_renewal_record_id` points to once APPLIED. Never mutated after
    APPLIED/REJECTED/CANCELLED/VOIDED except by the status-history-producing
    transition function -- see `commercial_ops/renewal_requests.py`."""

    __tablename__ = "owner_renewal_requests"

    subscription_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_subscriptions.id"), nullable=False
    )
    customer_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_customers.id"), nullable=False
    )
    product_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("owner_products.id"), nullable=False)

    current_plan_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("owner_plans.id"), nullable=False)
    requested_plan_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("owner_plans.id"))

    current_term_start: Mapped[date | None] = mapped_column(Date)
    current_term_end: Mapped[date | None] = mapped_column(Date)
    proposed_term_start: Mapped[date | None] = mapped_column(Date)
    proposed_term_end: Mapped[date | None] = mapped_column(Date)
    # Which Part D date rule computed proposed_term_start -- e.g.
    # "EARLY_RENEWAL_FROM_CURRENT_END", "LATE_RENEWAL_FROM_APPROVAL_DATE".
    # Recorded explicitly (never inferred silently) per Part D's own rule.
    date_rule: Mapped[str | None] = mapped_column(String(64))

    billing_interval: Mapped[str | None] = mapped_column(String(32))
    currency: Mapped[str] = mapped_column(String(8), nullable=False)

    current_plan_price_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_plan_prices.id")
    )
    proposed_plan_price_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_plan_prices.id")
    )

    commercial_amount: Mapped[float | None] = mapped_column(Numeric(12, 2))
    adjustment_amount: Mapped[float | None] = mapped_column(Numeric(12, 2))

    device_allowance_before: Mapped[int | None] = mapped_column(Integer)
    device_allowance_after: Mapped[int | None] = mapped_column(Integer)

    sales_owner_staff_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_staff_users.id")
    )
    finance_reviewer_staff_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_staff_users.id")
    )
    approved_by_staff_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_staff_users.id")
    )
    applied_by_staff_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_staff_users.id")
    )
    created_by_staff_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_staff_users.id")
    )

    customer_confirmation_reference: Mapped[str | None] = mapped_column(String(128))
    related_payment_record_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_payment_records.id")
    )
    applied_renewal_record_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_renewal_records.id")
    )

    reason: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), default="DRAFT", nullable=False)

    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Part E: "a retry with the same idempotency key must not extend the
    # subscription twice." Unique+nullable (a DRAFT created interactively
    # through the UI has no idempotency key; one submitted by an
    # idempotency-aware caller does).
    idempotency_key: Mapped[str | None] = mapped_column(String(128), unique=True)

    # SQLAlchemy's built-in optimistic-locking column (Part E/Z: "concurrent
    # conflicting renewal" must be rejected, not silently double-applied).
    # `version_id_col` below makes every UPDATE include `WHERE version = :old`
    # and bump it, so a second, stale in-memory copy of this row fails with
    # StaleDataError instead of silently overwriting the first writer's change.
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    subscription: Mapped["Subscription"] = relationship()  # noqa: F821
    customer: Mapped["Customer"] = relationship()  # noqa: F821
    product: Mapped["Product"] = relationship()  # noqa: F821
    current_plan: Mapped["Plan"] = relationship(foreign_keys=[current_plan_id])  # noqa: F821
    requested_plan: Mapped["Plan | None"] = relationship(foreign_keys=[requested_plan_id])  # noqa: F821
    status_history: Mapped[list["RenewalRequestStatusHistory"]] = relationship(back_populates="renewal_request")

    __mapper_args__ = {"version_id_col": version}


class RenewalRequestStatusHistory(Base, UUIDPKMixin, TimestampMixin):
    """Append-only (Part C/Z: "renewal... status changes... must retain
    history"). Mirrors SubscriptionStatusHistory/LicenseStatusHistory's
    established shape exactly -- no delete-orphan cascade from the parent,
    by the same reasoning those two already document."""

    __tablename__ = "owner_renewal_request_status_history"

    renewal_request_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_renewal_requests.id"), nullable=False
    )
    from_status: Mapped[str | None] = mapped_column(String(32))
    to_status: Mapped[str] = mapped_column(String(32), nullable=False)
    changed_by_staff_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_staff_users.id")
    )
    reason: Mapped[str | None] = mapped_column(Text)

    renewal_request: Mapped[RenewalRequest] = relationship(back_populates="status_history")


class PaymentCorrectionHistory(Base, UUIDPKMixin, TimestampMixin):
    """Part F: "correcting a payment must create a correction record or
    history entry... no hard overwrite of verified financial metadata."
    `correct_payment()` still updates `PaymentRecord.status` /
    `verified_by_staff_user_id` / `internal_note` directly (those fields
    describe the record's *current* state, which a correction legitimately
    changes) but now also writes one immutable row here per correction,
    preserving what the values were immediately before this specific
    correction -- so the full correction history remains reconstructable
    even though the live row only ever shows the latest state."""

    __tablename__ = "owner_payment_correction_history"

    payment_record_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_payment_records.id"), nullable=False
    )
    previous_status: Mapped[str] = mapped_column(String(32), nullable=False)
    new_status: Mapped[str] = mapped_column(String(32), nullable=False)
    previous_internal_note: Mapped[str | None] = mapped_column(Text)
    correction_note: Mapped[str | None] = mapped_column(Text)
    corrected_by_staff_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_staff_users.id")
    )

    payment_record: Mapped["PaymentRecord"] = relationship(back_populates="correction_history")  # noqa: F821


# -- Milestone 3: commercial policy + internal notifications (Parts G/H/I) --

NOTIFICATION_STATUSES = ("OPEN", "ACKNOWLEDGED", "IN_PROGRESS", "RESOLVED", "DISMISSED", "EXPIRED")


class CommercialPolicy(Base, UUIDPKMixin, TimestampMixin):
    """Part I: the COMMERCIAL policy (warning schedule, past-due timing,
    grace, auto-expiry) -- deliberately a completely separate concept and
    a separate table from `commercial_runtime.licensing_contracts`'s
    TECHNICAL offline policy (`owner_offline_policies`, Phase 6/7:
    check-in interval, offline grace, warning-before-restricted). Part I is
    explicit these must never be conflated: an Owner outage is not unpaid
    status, an unpaid status is not a cryptographic failure, and a
    subscription's commercial term expiring is not the same event as an
    installation's locally-cached assertion going stale. This table only
    ever drives Subscription.status transitions and notification
    generation (`commercial_ops/expiry_scan.py`); it has no field that
    reaches the product side directly -- entitlements/assertions still flow
    through the existing Phase 6/7 pipeline exactly as before.

    Resolved per-subscription via `product_id` (one policy per product,
    plus a `product_id IS NULL` global default as fallback) -- see
    `commercial_ops/commercial_policy.py`'s `resolve_policy_for_subscription()`.
    Deliberately NOT a foreign key on `Subscription` itself in this
    milestone: policy resolution is a lookup, not a stored assignment, so
    changing a policy's rules retroactively affects every subscription
    using it without a separate migration to touch every existing
    subscription row.
    """

    __tablename__ = "owner_commercial_policies"

    policy_code: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    product_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("owner_products.id"))
    policy_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    # Part G: expiry-warning schedule. Days-before-end-date, descending
    # (e.g. [30, 14, 7, 3, 1, 0]) -- not hardcoded anywhere in code, per
    # Part G's own instruction.
    warning_offsets_days: Mapped[list] = mapped_column(JSONB, nullable=False)
    notify_role_codes: Mapped[list] = mapped_column(JSONB, nullable=False)
    requires_customer_contact: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Part I: past-due/grace timing. 0 = subscription goes straight from
    # ACTIVE to EXPIRED with no PAST_DUE interim state.
    past_due_start_days: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    payment_grace_days: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # Whether the scan job (commercial_ops/expiry_scan.py) may itself
    # transition PAST_DUE -> EXPIRED once payment_grace_days has fully
    # elapsed. This is safe for an automated job specifically because
    # EXPIRED is a purely date-driven, non-discretionary fact (the term
    # ended) that only ever RESTRICTS future commercial operation and never
    # touches customer data (Principle 1) -- unlike SUSPENSION or
    # reviving an already-EXPIRED subscription, both of which remain
    # staff-only actions (see commercial_ops/renewal_requests.py's own
    # comment on why EXPIRED stays terminal in the SHARED subscription
    # transition table).
    auto_expire_after_grace: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    effective_date: Mapped[date] = mapped_column(Date, nullable=False)
    retired_date: Mapped[date | None] = mapped_column(Date)
    created_by_staff_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_staff_users.id")
    )

    product: Mapped["Product | None"] = relationship()  # noqa: F821


class InternalNotification(Base, UUIDPKMixin, TimestampMixin):
    """Part H: the internal Action Aura notification center. Deliberately
    internal-only -- no WhatsApp/SMS/email delivery adapter exists or is
    wired up in this milestone (Part G/H are explicit: internal
    notifications only; a future outbound-delivery adapter may be defined
    later but must remain inactive)."""

    __tablename__ = "owner_internal_notifications"

    notification_type: Mapped[str] = mapped_column(String(64), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)  # INFO/WARNING/CRITICAL

    customer_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("owner_customers.id"))
    subscription_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("owner_subscriptions.id"))
    license_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("owner_licenses.id"))
    installation_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("owner_installations.id"))

    title: Mapped[str] = mapped_column(String(255), nullable=False)
    # Deliberately plain, pre-composed safe text (Part Y: "no unrestricted
    # internal notes in signed assertions" -- and more broadly here, no
    # patient/business data ever flows into a notification body; callers
    # that build the message are responsible for only including the safe
    # metadata this module's own docstrings enumerate).
    message: Mapped[str] = mapped_column(Text, nullable=False)

    assigned_staff_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_staff_users.id")
    )
    assigned_role_code: Mapped[str | None] = mapped_column(String(32))

    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    acknowledged_by_staff_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_staff_users.id")
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_by_staff_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_staff_users.id")
    )
    resolution: Mapped[str | None] = mapped_column(Text)

    source_policy_code: Mapped[str | None] = mapped_column(String(64))

    # The idempotency mechanism this whole table's safety rests on (Part H:
    # "deduplicate repeated scheduler runs... no notification flood").
    # Format is the creating caller's choice (expiry_scan.py uses
    # f"{notification_type}:{subscription_id}:{offset_days}") -- the
    # UNIQUE constraint is what actually guarantees a second scan run for
    # the same subscription at the same warning offset never creates a
    # duplicate row, not any application-level check-then-insert (which
    # would itself be a race).
    dedup_key: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)

    status: Mapped[str] = mapped_column(String(16), default="OPEN", nullable=False)

    customer: Mapped["Customer | None"] = relationship()  # noqa: F821
    subscription: Mapped["Subscription | None"] = relationship()  # noqa: F821
    license: Mapped["License | None"] = relationship()  # noqa: F821
    installation: Mapped["Installation | None"] = relationship()  # noqa: F821


# -- Milestone 4: pilot lifecycle + emergency extensions (Parts M/N) --------

PILOT_STATUSES = ("DRAFT", "APPROVED", "ACTIVE", "EXTENDED", "CONVERTED", "COMPLETED", "CANCELLED")

EMERGENCY_EXTENSION_STATUSES = ("ACTIVE", "EXPIRED", "REVOKED")


class PilotRecord(Base, UUIDPKMixin, TimestampMixin):
    """Part M. Pilot-specific metadata layered ON TOP of an existing
    Subscription (which must already be in PILOT status, created the
    normal way via create_subscription()/transition_subscription() --
    this table does not duplicate or replace Subscription, it only adds
    the fields a pilot needs that a regular paid subscription doesn't:
    allowed workflows, success criteria, exit plan, extension tracking.

    Conversion to paid is deliberately NOT implemented as a standalone
    action on this table -- it is done by creating and applying a real
    RenewalRequest through the existing Milestone 2 pipeline (full
    separation-of-duties, payment-confirmation, and recent-auth gates),
    exactly as Part M requires ("explicit paid plan... confirmed
    commercial record... no silent conversion"). `mark_pilot_converted()`
    (commercial_ops/pilot_lifecycle.py) only updates THIS table's
    bookkeeping after that renewal has genuinely been APPLIED -- it never
    grants paid status by itself.
    """

    __tablename__ = "owner_pilot_records"

    subscription_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_subscriptions.id"), unique=True, nullable=False
    )
    customer_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_customers.id"), nullable=False
    )
    product_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("owner_products.id"), nullable=False)
    platform_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("owner_platforms.id"))

    pilot_start: Mapped[date] = mapped_column(Date, nullable=False)
    pilot_end: Mapped[date] = mapped_column(Date, nullable=False)

    allowed_device_count: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    allowed_workflows: Mapped[list | None] = mapped_column(JSONB)
    support_level: Mapped[str | None] = mapped_column(String(32))

    sales_owner_staff_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_staff_users.id")
    )
    support_owner_staff_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_staff_users.id")
    )

    agreed_limitations: Mapped[str | None] = mapped_column(Text)
    success_criteria: Mapped[str | None] = mapped_column(Text)
    review_date: Mapped[date | None] = mapped_column(Date)
    exit_rollback_plan: Mapped[str | None] = mapped_column(Text)

    # "PENDING" until complete_pilot()/mark_pilot_converted() records the
    # actual outcome -- see PILOT_CONVERSION_DECISIONS.
    conversion_decision: Mapped[str] = mapped_column(String(32), default="PENDING", nullable=False)

    # Part M: "no indefinite rolling pilot" -- a hard, explicit ceiling
    # checked by extend_pilot(), not just a convention.
    extension_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_extensions_allowed: Mapped[int] = mapped_column(Integer, default=2, nullable=False)

    status: Mapped[str] = mapped_column(String(16), default="DRAFT", nullable=False)

    created_by_staff_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_staff_users.id")
    )
    approved_by_staff_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_staff_users.id")
    )
    converted_renewal_request_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_renewal_requests.id")
    )

    # Optimistic lock -- same reasoning as RenewalRequest.version: two
    # concurrent extend/convert/cancel attempts on the same pilot must not
    # silently overwrite each other.
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    subscription: Mapped["Subscription"] = relationship()  # noqa: F821
    customer: Mapped["Customer"] = relationship()  # noqa: F821
    product: Mapped["Product"] = relationship()  # noqa: F821
    status_history: Mapped[list["PilotStatusHistory"]] = relationship(back_populates="pilot_record")
    extensions: Mapped[list["PilotExtension"]] = relationship(back_populates="pilot_record")

    __mapper_args__ = {"version_id_col": version}


class PilotStatusHistory(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "owner_pilot_status_history"

    pilot_record_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_pilot_records.id"), nullable=False
    )
    from_status: Mapped[str | None] = mapped_column(String(16))
    to_status: Mapped[str] = mapped_column(String(16), nullable=False)
    changed_by_staff_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_staff_users.id")
    )
    reason: Mapped[str | None] = mapped_column(Text)

    pilot_record: Mapped[PilotRecord] = relationship(back_populates="status_history")


class PilotExtension(Base, UUIDPKMixin, TimestampMixin):
    """Append-only extension history (Part M: "extension history" is a
    required field on the pilot record -- modeled here as its own table,
    matching this codebase's established history-table pattern, rather
    than a JSON blob column)."""

    __tablename__ = "owner_pilot_extensions"

    pilot_record_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_pilot_records.id"), nullable=False
    )
    extension_number: Mapped[int] = mapped_column(Integer, nullable=False)
    previous_end_date: Mapped[date] = mapped_column(Date, nullable=False)
    new_end_date: Mapped[date] = mapped_column(Date, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    approved_by_staff_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_staff_users.id")
    )

    pilot_record: Mapped[PilotRecord] = relationship(back_populates="extensions")


class EmergencyExtension(Base, UUIDPKMixin, TimestampMixin):
    """Part N. An explicit, short-lived, permission-controlled,
    MFA-protected (enforced at the route layer, see
    commercial_ops/routes.py) override that keeps a subscription's
    commercial state usable despite its underlying Subscription/License
    status -- for exceptional support cases only. NOT a bypass: it never
    modifies Subscription/License/RenewalRecord rows, never marks a
    payment confirmed, and is explicitly checked AFTER `REVOKED` in
    `resolve_commercial_state()` (Milestone 1) so it can never override an
    explicit security revocation.
    """

    __tablename__ = "owner_emergency_extensions"

    subscription_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_subscriptions.id"), nullable=False
    )
    license_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("owner_licenses.id"))

    reason: Mapped[str] = mapped_column(Text, nullable=False)
    incident_reference: Mapped[str | None] = mapped_column(String(128))

    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # Hard cap enforced by create_emergency_extension() -- see
    # MAX_EMERGENCY_EXTENSION_HOURS in commercial_ops/emergency_extensions.py.
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    # None = every installation under this subscription/license; otherwise
    # a JSON list of installation IDs (Part N: "device scope").
    device_scope: Mapped[list | None] = mapped_column(JSONB)

    status: Mapped[str] = mapped_column(String(16), default="ACTIVE", nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_by_staff_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_staff_users.id")
    )
    revocation_reason: Mapped[str | None] = mapped_column(Text)

    created_by_staff_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_staff_users.id"), nullable=False
    )

    subscription: Mapped["Subscription"] = relationship()  # noqa: F821
    license: Mapped["License | None"] = relationship()  # noqa: F821
