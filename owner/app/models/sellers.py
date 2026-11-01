"""Seller-agreement rules (clauses 7, 8, 16, 17, 18): the tables that hold them.

One module for the four rules, one Alembic revision per rule (chained on this branch):

  owner_seller_cash_handins          R1  cash a seller collected, due back to the company in 24 hours
  (further rules add their tables below as they land)

None of these tables is read by the licensing path or the product apps: a rule here can
refuse or flag a SELLER'S action, it can never lock a customer's shop.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPKMixin

CASH_HANDIN_STATUSES = ("OPEN", "SUBMITTED", "RECEIVED", "RECEIVED_AT_OFFICE")


class SellerCashHandIn(Base, UUIDPKMixin, TimestampMixin):
    """Clause 7: every amount a seller collects from a customer belongs to the company and
    must reach it within 24 hours of collection, by bank transfer or at the office.

    ONE ROW PER PAYMENT (unique payment_record_id): "exactly one obligation per cash payment"
    is a database fact, not a habit of the code. Whole payment only -- a partial hand-in is
    not modelled (the seller hands in the payment, or has not).

      OPEN                 nothing handed in yet. The only state that can be OVERDUE.
      SUBMITTED            the seller uploaded the bank-transfer slip; a manager has not checked it yet.
                           Waiting for the company is not the seller's fault, so never overdue.
      RECEIVED             a manager confirmed the slip.
      RECEIVED_AT_OFFICE   a manager recorded that the cash was handed over at the office.
    A rejected slip puts the row back to OPEN (slip cleared, rejected_count + 1).
    """

    __tablename__ = "owner_seller_cash_handins"
    __table_args__ = (
        CheckConstraint(
            "status IN ('OPEN', 'SUBMITTED', 'RECEIVED', 'RECEIVED_AT_OFFICE')", name="ck_owner_seller_cash_handins_status"
        ),
        CheckConstraint("amount > 0", name="ck_owner_seller_cash_handins_amount_positive"),
        CheckConstraint("due_at > collected_at", name="ck_owner_seller_cash_handins_due_after_collected"),
        # A SUBMITTED row always names its slip; an OPEN row never does (a rejected slip is cleared).
        CheckConstraint("status <> 'SUBMITTED' OR slip_document_id IS NOT NULL", name="ck_owner_seller_cash_handins_submitted_has_slip"),
        CheckConstraint("status <> 'OPEN' OR slip_document_id IS NULL", name="ck_owner_seller_cash_handins_open_has_no_slip"),
        # Received means somebody received it, and when; nothing else carries those two.
        CheckConstraint(
            "(status IN ('RECEIVED', 'RECEIVED_AT_OFFICE')) = (received_at IS NOT NULL AND received_by_staff_user_id IS NOT NULL)",
            name="ck_owner_seller_cash_handins_received_stamped",
        ),
    )

    seller_staff_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_staff_users.id"), nullable=False, index=True
    )
    payment_record_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_payment_records.id"), nullable=False, unique=True
    )
    amount: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(8), nullable=False)
    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="OPEN", nullable=False)
    slip_document_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_customer_documents.id")
    )
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    received_by_staff_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_staff_users.id")
    )
    rejected_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_rejection_reason: Mapped[str | None] = mapped_column(Text)
    # Set the moment the "overdue" emails were queued, so the alert goes out once per obligation.
    overdue_notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
