"""Phase 9.5E -- Daily Cash Closing. An operational cash-control record, not
bank reconciliation and not General Ledger posting. Scope key is
(business_date, currency) -- no branch/operational-unit authority exists
anywhere in this codebase, so per the authoritative closing procedure's own
fallback instruction, branch is omitted rather than invented. See
docs/owner/phase9_5e/cash-closing-contract.md."""
from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Date, DateTime, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPKMixin

CASH_CLOSING_STATUSES = ("DRAFT", "SUBMITTED", "REVIEW_REQUIRED", "APPROVED", "REJECTED", "REOPENED", "CLOSED")


class CashClosing(Base, UUIDPKMixin, TimestampMixin):
    """One row per (business_date, currency) -- reopening mutates this same
    row (never creates a second row for the scope), with every reopen event
    separately recorded in CashClosingReopenEvent so prior approval history
    is retained, not overwritten."""

    __tablename__ = "owner_cash_closings"
    __table_args__ = (
        UniqueConstraint("business_date", "currency", name="uq_cash_closing_scope"),
    )

    business_date: Mapped[date] = mapped_column(Date, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="DRAFT", nullable=False, index=True)

    opening_cash: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    opening_cash_is_override: Mapped[bool] = mapped_column(default=False, nullable=False)
    opening_cash_override_reason: Mapped[str | None] = mapped_column(Text)

    confirmed_cash_collections: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0"), nullable=False)
    confirmed_cash_refunds: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0"), nullable=False)
    cash_expense_payments: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0"), nullable=False)
    cash_commission_payouts: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0"), nullable=False)
    approved_cash_adjustments: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0"), nullable=False)
    expected_closing_cash: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)

    actual_counted_cash: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    variance: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    variance_explanation: Mapped[str | None] = mapped_column(Text)

    prepared_by_staff_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_staff_users.id"), nullable=False
    )
    reviewed_by_staff_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_staff_users.id")
    )
    approved_by_staff_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_staff_users.id")
    )

    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    reopen_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)


class CashClosingAdjustment(Base, UUIDPKMixin, TimestampMixin):
    """Append-only line items backing CashClosing.approved_cash_adjustments
    (the sum of all ACTIVE adjustment rows for the closing). A correction is
    a new offsetting row, never an edit."""

    __tablename__ = "owner_cash_closing_adjustments"

    cash_closing_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("owner_cash_closings.id"), nullable=False, index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    created_by_staff_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_staff_users.id"), nullable=False
    )
    approved_by_staff_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_staff_users.id")
    )


class CashClosingReopenEvent(Base, UUIDPKMixin, TimestampMixin):
    """One append-only row per reopen -- snapshots the prior approved state
    before it's mutated, so "prior closing history retained after reopen" is
    provable from real rows, not just inferred from the mutated CashClosing."""

    __tablename__ = "owner_cash_closing_reopen_events"

    cash_closing_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("owner_cash_closings.id"), nullable=False, index=True)
    reopened_by_staff_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_staff_users.id"), nullable=False
    )
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    reopened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    prior_status: Mapped[str] = mapped_column(String(20), nullable=False)
    prior_approved_by_staff_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    prior_approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    prior_expected_closing_cash: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    prior_actual_counted_cash: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    prior_variance: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
