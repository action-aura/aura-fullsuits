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

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Index, Numeric, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPKMixin

COMMISSION_RULE_TYPES = ("PERCENTAGE_OF_PAYMENT", "FIXED_AMOUNT", "PERCENTAGE_FIRST_SALE", "PERCENTAGE_RENEWAL")
COMMISSION_ENTRY_STATUSES = ("PENDING", "EARNED", "APPROVED", "PAID", "REVERSED", "CANCELLED", "DISPUTED")
PAYOUT_BATCH_STATUSES = ("DRAFT", "APPROVED", "PAID")


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
        # Partial unique index: a non-reversal entry can only exist once per
        # source payment -- prevents a duplicate-payment-confirmation event
        # from ever creating a second EARNED entry for the same real payment.
        # Reversal rows (reversal_of_ledger_entry_id IS NOT NULL) are exempt
        # since a reversal legitimately shares its origin's payment.
        Index(
            "uq_commission_ledger_one_entry_per_payment",
            "source_payment_record_id",
            unique=True,
            postgresql_where=text("reversal_of_ledger_entry_id IS NULL"),
        ),
    )

    employee_profile_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_employee_profiles.id"), nullable=False
    )
    commission_rule_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_commission_rule_versions.id"), nullable=False
    )
    source_commercial_invoice_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_commercial_invoices.id"), nullable=False
    )
    source_payment_record_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_payment_records.id"), nullable=False
    )
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
