"""Phase 9.5A Milestone 12 -- append-only commission ledger.

commission_ledger_entries is append-only: a reversal is a NEW row
(reversal_of_ledger_entry_id set, negative commission_amount), never an
edit of the original EARNED/APPROVED/PAID entry. commission_rule_versions
is append-only too (a rule change closes the old row's effective_until and
opens a new one) -- an entry's commission_rule_version_id permanently pins
the exact rate it used. See docs/owner/phase9_5a/commission-domain-design.md.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Boolean, CheckConstraint, Date, DateTime, ForeignKey, Index, Integer, Numeric, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPKMixin

# Step 0 (2026-10-24, revision 38a13f8b2f8e): MONTHLY_LADDER is the owner's
# monthly sales ladder (app/commissions/ladder.py). A seller is put on it by
# assigning them a commission plan whose rule is MONTHLY_LADDER; the rule in
# force on the day a sale qualifies decides which rule pays it -- never both.
COMMISSION_RULE_TYPES = (
    "PERCENTAGE_OF_PAYMENT", "FIXED_AMOUNT", "PERCENTAGE_FIRST_SALE", "PERCENTAGE_RENEWAL", "MONTHLY_LADDER",
)
COMMISSION_ENTRY_STATUSES = ("PENDING", "EARNED", "APPROVED", "PAID", "REVERSED", "CANCELLED", "DISPUTED")
PAYOUT_BATCH_STATUSES = ("DRAFT", "APPROVED", "PAID")

# What a ledger row is for. PAYMENT_RULE: the original per-allocation
# percentage/fixed earning (ledger.py::post_earning_for_allocation), always
# tied to an invoice and an allocation. LADDER: one sale's share of its
# seller's monthly ladder total (ladder.py), tied to a CommissionLadderSale.
# RENEWAL: the owner's 10 JOD for a confirmed renewal payment.
COMMISSION_ENTRY_KINDS = ("PAYMENT_RULE", "LADDER", "RENEWAL")
LADDER_SALE_STATUSES = ("COUNTED", "UNCOUNTED")
# Why a counted sale stopped counting: a refund confirmed inside its 30-day
# hold (the owner's rule), or a correction that took its money off the sale
# (allocation reversed, a qualifying payment no longer confirmed).
LADDER_UNCOUNT_CAUSES = ("REFUND_IN_HOLD", "CORRECTION")

# AUDIT-owner-cross-screen: the single name for "a commission entry that is
# waiting for a FINANCE approval decision". Verified in code, not assumed:
# the only two rows this application ever inserts are
# commissions/ledger.py::post_earning_for_allocation() (status="EARNED",
# ledger.py:80) and its reversal counterpart (status="REVERSED",
# ledger.py:164); approve_commission_entry()/record_payout() only ever
# transition FROM "EARNED"/"APPROVED". "PENDING" survives in
# COMMISSION_ENTRY_STATUSES and as this table's column default purely as a
# historical artifact -- no code path assigns it, so no real row can hold it.
#
# It exists as a constant because the awaiting-approval status has to be
# read by three places that MUST agree, and previously did not: the
# Attention Center's badge count, the deep link that badge points at
# (attention/service.py used "EARNED" to count and "PENDING" to link, so
# clicking a badge showing N always landed on an empty list), and the
# Finance dashboard's "Commissions pending approval" figure. One constant
# means the count and the link cannot disagree again.
COMMISSION_AWAITING_APPROVAL_STATUS = "EARNED"


def commission_awaiting_approval_clause(now):
    """Step 0 ladder (2026-10-24): "awaiting approval" is EARNED AND past its
    30-day hold. A held ladder/renewal row cannot be approved
    (ledger.approve_commission_entry: COMMISSION_ON_HOLD), so counting it on
    the badge and the Finance figure would send a manager to a row whose only
    answer is a refusal. Rows with no hold (the percentage rule) are
    unchanged. One definition, read by the badge and the Finance figure alike."""
    from sqlalchemy import and_, or_

    return and_(
        CommissionLedgerEntry.status == COMMISSION_AWAITING_APPROVAL_STATUS,
        or_(CommissionLedgerEntry.payable_after.is_(None), CommissionLedgerEntry.payable_after <= now),
    )


class CommissionPlan(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "owner_commission_plans"

    plan_code: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class CommissionRuleVersion(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "owner_commission_rule_versions"

    commission_plan_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_commission_plans.id"), nullable=False
    )
    rule_type: Mapped[str] = mapped_column(String(32), nullable=False)
    rate_percentage: Mapped[Decimal | None] = mapped_column(Numeric(6, 3))
    fixed_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    currency: Mapped[str | None] = mapped_column(String(3))
    product_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("owner_products.id"))
    effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    effective_until: Mapped[date | None] = mapped_column(Date)
    created_by_staff_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_staff_users.id")
    )


class EmployeeCommissionPlanAssignment(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "owner_employee_commission_plan_assignments"
    __table_args__ = (
        # Phase 9.5D Milestone 22 -- plain index=True's auto-generated name
        # (ix_owner_employee_commission_plan_assignments_employee_profile_id,
        # 67 chars) exceeds Postgres's 63-character identifier limit;
        # explicit shorter name kept in sync with the migration.
        Index("ix_owner_commission_plan_assignment_employee_profile_id", "employee_profile_id"),
    )

    employee_profile_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_employee_profiles.id"), nullable=False
    )
    commission_plan_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_commission_plans.id"), nullable=False
    )
    effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    effective_until: Mapped[date | None] = mapped_column(Date)
    created_by_staff_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_staff_users.id")
    )


class CommissionLedgerEntry(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "owner_commission_ledger_entries"
    __table_args__ = (
        # Phase 9.5D Milestone 15 -- real gap found and closed: this
        # constraint originally deduped on source_payment_record_id
        # (Phase 9.5A, before PaymentAllocation existed). Non-Negotiable
        # Rule: commission basis is confirmed Payment Allocation, and
        # partial allocations create proportional earnings -- Milestone
        # 11 already proved a single Payment can be split-allocated
        # across multiple Invoices, each needing its own commission. A
        # per-payment uniqueness constraint would silently block the
        # second allocation's legitimate earning. Moved to
        # source_payment_allocation_id (added this milestone, additive,
        # table was empty -- MODEL PRESENT SERVICE MISSING per Milestone
        # 1's audit, nothing to migrate). Reversal rows
        # (reversal_of_ledger_entry_id IS NOT NULL) remain exempt.
        Index(
            "uq_commission_ledger_one_entry_per_allocation",
            "source_payment_allocation_id",
            unique=True,
            postgresql_where=text("reversal_of_ledger_entry_id IS NULL"),
        ),
        # Step 0 ladder (revision 38a13f8b2f8e). A ladder sale has at most ONE
        # approvable (EARNED) row at a time: when its share of the month
        # changes while it is still held, the open row is CANCELLED and a new
        # one posted (ladder.py::_recompute_months) -- so a payout can never
        # approve an old, larger share next to the new one. Also the DB-level
        # stop for a double post if two recomputes ever raced past the lock.
        Index(
            "uq_commission_ledger_one_open_ladder_entry_per_sale",
            "ladder_sale_id",
            unique=True,
            postgresql_where=text("entry_kind = 'LADDER' AND status = 'EARNED'"),
        ),
        # One live 10 JOD renewal per renewal payment, however often the
        # payment is re-saved or two managers confirm at once.
        Index(
            "uq_commission_ledger_one_renewal_per_payment",
            "source_payment_record_id",
            unique=True,
            postgresql_where=text(
                "entry_kind = 'RENEWAL' AND reversal_of_ledger_entry_id IS NULL AND status <> 'CANCELLED'"
            ),
        ),
        CheckConstraint(
            "entry_kind IN ('PAYMENT_RULE', 'LADDER', 'RENEWAL')", name="ck_owner_commission_ledger_entries_kind"
        ),
        # The original rule's rows keep the guarantee they always had: an
        # invoice and an allocation behind every one. Only ladder/renewal rows
        # may lack them (a monthly payment or a renewal is not allocated).
        CheckConstraint(
            "entry_kind <> 'PAYMENT_RULE' OR "
            "(source_commercial_invoice_id IS NOT NULL AND source_payment_allocation_id IS NOT NULL)",
            name="ck_owner_commission_ledger_entries_payment_rule_sources",
        ),
        CheckConstraint(
            "entry_kind <> 'LADDER' OR (ladder_sale_id IS NOT NULL AND ladder_month IS NOT NULL)",
            name="ck_owner_commission_ledger_entries_ladder_sources",
        ),
    )

    employee_profile_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_employee_profiles.id"), nullable=False, index=True
    )
    commission_rule_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_commission_rule_versions.id"), nullable=False
    )
    # Nullable since revision 38a13f8b2f8e for LADDER/RENEWAL rows only (the
    # CHECK above keeps PAYMENT_RULE rows exactly as strict as before).
    source_commercial_invoice_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_commercial_invoices.id"), nullable=True, index=True
    )
    source_payment_record_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_payment_records.id"), nullable=False
    )
    source_payment_allocation_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_payment_allocations.id"), nullable=True, index=True
    )
    entry_kind: Mapped[str] = mapped_column(
        String(16), default="PAYMENT_RULE", server_default="PAYMENT_RULE", nullable=False
    )
    ladder_sale_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_commission_ladder_sales.id"), nullable=True, index=True
    )
    # The ladder month (first day, Jordan time) a LADDER/RENEWAL row counts in.
    ladder_month: Mapped[date | None] = mapped_column(Date)
    # The sale's place in its month when this row was posted (1 = first sale).
    ladder_position: Mapped[int | None] = mapped_column(Integer)
    # The owner's 30-day hold: the row cannot be approved for payout before
    # this moment (ledger.py::approve_commission_entry). NULL = no hold (the
    # original percentage rule's rows, unchanged).
    payable_after: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    base_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    rate_or_fixed_applied: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False)
    commission_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="PENDING", nullable=False)
    earned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approved_by_staff_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_staff_users.id")
    )
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reversal_of_ledger_entry_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_commission_ledger_entries.id")
    )
    dispute_reason: Mapped[str | None] = mapped_column(Text)


class CommissionPayoutBatch(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "owner_commission_payout_batches"

    batch_reference: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="DRAFT", nullable=False)
    # Phase 9.5E -- additive. NULL (pre-9.5E batches) is treated as "not
    # cash" by Cash Closing's aggregation -- see
    # docs/owner/phase9_5e/cash-closing-contract.md.
    payment_method: Mapped[str | None] = mapped_column(String(32))
    created_by_staff_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_staff_users.id")
    )
    approved_by_staff_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_staff_users.id")
    )


class CommissionPayoutLine(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "owner_commission_payout_lines"
    __table_args__ = (
        UniqueConstraint("commission_ledger_entry_id", name="uq_commission_payout_one_per_entry"),
    )

    commission_payout_batch_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_commission_payout_batches.id"), nullable=False
    )
    employee_profile_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_employee_profiles.id"), nullable=False
    )
    commission_ledger_entry_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_commission_ledger_entries.id"), nullable=False
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)


class CommissionLadderSale(Base, UUIDPKMixin, TimestampMixin):
    """Step 0 (2026-10-24, revision 38a13f8b2f8e) -- one sale counted on its
    seller's monthly ladder (app/commissions/ladder.py).

    A FACT row: the sale qualified at qualified_at (the confirmation moment of
    the payment that made it count), so it counts in ladder_month (that
    moment's calendar month in Jordan time) and its commission is held until
    hold_ends_at (30 days later). The ledger rows (CommissionLedgerEntry with
    entry_kind LADDER) carry the money; this row says WHY.

    A sale that stops counting is never deleted: it moves to UNCOUNTED with
    its cause (a refund inside the hold, or a correction) and the month is
    re-derived. A sale that counts again (money re-allocated after a
    correction) gets a NEW row -- the partial unique index allows only one
    COUNTED row per sales order, which is also the database's own guarantee
    that one sale is never counted twice, whatever races above it."""

    __tablename__ = "owner_commission_ladder_sales"
    __table_args__ = (
        Index(
            "uq_commission_ladder_one_counted_sale_per_order",
            "sales_order_id",
            unique=True,
            postgresql_where=text("status = 'COUNTED'"),
        ),
        Index("ix_commission_ladder_sales_seller_month", "employee_profile_id", "ladder_month"),
        CheckConstraint("status IN ('COUNTED', 'UNCOUNTED')", name="ck_owner_commission_ladder_sales_status"),
        CheckConstraint(
            "uncount_cause IS NULL OR uncount_cause IN ('REFUND_IN_HOLD', 'CORRECTION')",
            name="ck_owner_commission_ladder_sales_uncount_cause",
        ),
        CheckConstraint(
            "(status = 'COUNTED' AND uncounted_at IS NULL AND uncount_cause IS NULL) OR "
            "(status = 'UNCOUNTED' AND uncounted_at IS NOT NULL AND uncount_cause IS NOT NULL)",
            name="ck_owner_commission_ladder_sales_uncounted_has_cause",
        ),
    )

    employee_profile_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_employee_profiles.id"), nullable=False
    )
    sales_order_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_sales_orders.id"), nullable=False, index=True
    )
    commercial_invoice_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_commercial_invoices.id"), nullable=False
    )
    subscription_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("owner_subscriptions.id"))
    package_plan_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("owner_plans.id"), nullable=False)
    commission_rule_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_commission_rule_versions.id"), nullable=False
    )
    qualifying_payment_record_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_payment_records.id"), nullable=False
    )
    qualifying_allocation_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_payment_allocations.id")
    )
    qualified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ladder_month: Mapped[date] = mapped_column(Date, nullable=False)
    hold_ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="COUNTED", server_default="COUNTED", nullable=False)
    uncounted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    uncount_cause: Mapped[str | None] = mapped_column(String(24))
    uncount_reason: Mapped[str | None] = mapped_column(Text)
    source_refund_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_commercial_refunds.id")
    )
    # A refund confirmed AFTER the hold: the sale keeps counting (the default
    # this change chose; the owner ruled only on refunds inside the hold) and
    # this records that it happened, for the month's breakdown.
    refunded_after_hold_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))