"""Phase 9.5A Milestone 13 -- Commercial Operations Ledger foundation. Not a
full accounting ERP -- a flat operational expense record, never a GL
posting. See docs/owner/phase9_5a/mini-financial-ledger-design.md.

Phase 9.5E extends this foundation with Payee, ExpenseApproval, ExpensePayment,
and ExpenseAttachment, plus new Expense columns (payee_id,
beneficiary_employee_profile_id, external_reference, expense_number,
approved_amount) and two new statuses (RETURNED, PARTIALLY_PAID). See
docs/owner/phase9_5e/operational-finance-funnel-contract.md and
expense-approval-and-segregation-contract.md for the full lifecycle/approval
rules this schema exists to support."""
from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPKMixin

EXPENSE_STATUSES = ("DRAFT", "SUBMITTED", "RETURNED", "APPROVED", "PARTIALLY_PAID", "PAID", "REJECTED", "VOID")
PAYEE_TYPES = ("EXTERNAL", "EMPLOYEE")
EXPENSE_APPROVAL_STATUSES = ("PENDING", "APPROVED", "REJECTED", "RETURNED", "CANCELLED")
EXPENSE_PAYMENT_STATUSES = ("RECORDED", "REVERSED")
EXPENSE_ATTACHMENT_STATUSES = ("ACTIVE", "ARCHIVED")


class ExpenseCategory(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "owner_expense_categories"

    category_code: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class Payee(Base, UUIDPKMixin, TimestampMixin):
    """Phase 9.5E -- either an external vendor/contact (payee_type=EXTERNAL,
    employee_profile_id NULL) or an internal employee beneficiary
    (payee_type=EMPLOYEE, employee_profile_id set). Expense.beneficiary_employee_profile_id
    is denormalized from this record at Expense creation time for fast
    approver-conflict queries without a join -- see
    expense-approval-and-segregation-contract.md Rule 3."""

    __tablename__ = "owner_expense_payees"

    payee_type: Mapped[str] = mapped_column(String(16), nullable=False)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    employee_profile_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_employee_profiles.id"), index=True
    )
    external_contact_reference: Mapped[str | None] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_by_staff_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_staff_users.id"), nullable=False
    )


class Expense(Base, UUIDPKMixin, TimestampMixin):
    """A correction is a new row referencing the original
    (reversal_of_expense_id) -- never an in-place edit of a SUBMITTED+
    expense, matching the append-only-correction pattern used throughout
    this phase (commission ledger, price versions, etc.).

    `amount` is the requester's REQUESTED amount (immutable once SUBMITTED --
    a material change requires RETURNED -> revise -> resubmit, a new approval
    cycle). `approved_amount` is set only by a real APPROVED decision and is
    always <= amount. `description` doubles as the fingerprint's "business
    purpose" field -- no separate column, avoiding a duplicate free-text field
    for the same concept."""

    __tablename__ = "owner_expenses"
    __table_args__ = (
        UniqueConstraint("expense_number", name="uq_expense_number"),
    )

    expense_number: Mapped[str | None] = mapped_column(String(32))
    category_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("owner_expense_categories.id"), nullable=False, index=True)
    payee_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("owner_expense_payees.id"), index=True)
    beneficiary_employee_profile_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_employee_profiles.id"), index=True
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    approved_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    expense_date: Mapped[date] = mapped_column(Date, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    external_reference: Mapped[str | None] = mapped_column(String(200), index=True)
    payment_method: Mapped[str] = mapped_column(String(32), nullable=False)
    payment_reference: Mapped[str | None] = mapped_column(String(120))
    entered_by_employee_profile_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_employee_profiles.id"), nullable=False, index=True
    )
    approved_by_staff_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_staff_users.id")
    )
    status: Mapped[str] = mapped_column(String(16), default="DRAFT", nullable=False, index=True)
    attachment_reference: Mapped[str | None] = mapped_column(String(512))
    reversal_of_expense_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("owner_expenses.id"))
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)


class ExpenseApproval(Base, UUIDPKMixin, TimestampMixin):
    """One row per approval cycle (append-only -- a RETURNED/REJECTED/CANCELLED
    row is never mutated further; resubmission creates a new PENDING row).
    `fingerprint` is captured at submit time (compute_expense_fingerprint()
    over the material fields listed in the segregation contract) and
    recomputed live at decision time for staleness comparison -- never a
    caller-supplied version number. See expense-approval-and-segregation-contract.md."""

    __tablename__ = "owner_expense_approvals"

    expense_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("owner_expenses.id"), nullable=False, index=True)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="PENDING", nullable=False, index=True)
    requested_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    approved_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    requested_by_employee_profile_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_employee_profiles.id"), nullable=False, index=True
    )
    decided_by_staff_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_staff_users.id")
    )
    decision_reason: Mapped[str | None] = mapped_column(Text)
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ExpensePayment(Base, UUIDPKMixin, TimestampMixin):
    """Append-only. A reversal is a new row with reversal_of_payment_id set,
    matching the Commercial Sales refund/allocation-reversal pattern -- never
    an in-place edit or delete of a recorded payment."""

    __tablename__ = "owner_expense_payments"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_expense_payment_idempotency_key"),
    )

    expense_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("owner_expenses.id"), nullable=False, index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    payment_method: Mapped[str] = mapped_column(String(32), nullable=False)
    payment_reference: Mapped[str | None] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(16), default="RECORDED", nullable=False)
    idempotency_key: Mapped[str | None] = mapped_column(String(128))
    recorded_by_staff_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_staff_users.id"), nullable=False
    )
    reversal_of_payment_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("owner_expense_payments.id"))
    paid_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ExpenseAttachment(Base, UUIDPKMixin, TimestampMixin):
    """`storage_key` is a private, server-generated, non-guessable key into
    the attachment storage backend -- never a raw filesystem path exposed to
    a client, never a public URL. See attachment-security-contract.md."""

    __tablename__ = "owner_expense_attachments"
    __table_args__ = (
        UniqueConstraint("storage_key", name="uq_expense_attachment_storage_key"),
    )

    expense_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("owner_expenses.id"), nullable=False, index=True)
    storage_key: Mapped[str] = mapped_column(String(256), nullable=False)
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[str] = mapped_column(String(127), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="ACTIVE", nullable=False)
    uploaded_by_employee_profile_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_employee_profiles.id"), nullable=False
    )
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
