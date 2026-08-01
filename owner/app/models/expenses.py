"""Phase 9.5A Milestone 13 -- Commercial Operations Ledger foundation. Not a
full accounting ERP -- a flat operational expense record, never a GL
posting. See docs/owner/phase9_5a/mini-financial-ledger-design.md."""
from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import Boolean, Date, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPKMixin

EXPENSE_STATUSES = ("DRAFT", "SUBMITTED", "APPROVED", "REJECTED", "PAID", "VOID")


class ExpenseCategory(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "owner_expense_categories"

    category_code: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class Expense(Base, UUIDPKMixin, TimestampMixin):
    """A correction is a new row referencing the original
    (reversal_of_expense_id) -- never an in-place edit of a SUBMITTED+
    expense, matching the append-only-correction pattern used throughout
    this phase (commission ledger, price versions, etc.)."""

    __tablename__ = "owner_expenses"

    category_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("owner_expense_categories.id"), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    expense_date: Mapped[date] = mapped_column(Date, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    payment_method: Mapped[str] = mapped_column(String(32), nullable=False)
    payment_reference: Mapped[str | None] = mapped_column(String(120))
    entered_by_employee_profile_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_employee_profiles.id"), nullable=False
    )
    approved_by_staff_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_staff_users.id")
    )
    status: Mapped[str] = mapped_column(String(16), default="DRAFT", nullable=False)
    attachment_reference: Mapped[str | None] = mapped_column(String(512))
    reversal_of_expense_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("owner_expenses.id"))
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
