"""Phase 9.5A Milestone 10/11 -- catalog-priced commercial sales documents.

Quote -> SalesOrder -> CommercialInvoice -> PaymentRecord (existing,
extended additively) / CommercialRefund. Every line item snapshots its
plan/addon and exact price_version_id at creation time -- never a live join
to the catalog for display (docs/owner/phase9_5a/price-authority-rules.md).
See docs/owner/phase9_5a/commercial-document-lifecycle.md.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import CheckConstraint, Date, DateTime, ForeignKey, Index, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPKMixin

QUOTE_STATUSES = ("DRAFT", "SENT", "ACCEPTED", "REJECTED", "EXPIRED", "CANCELLED")
SALES_ORDER_STATUSES = ("DRAFT", "CONFIRMED", "CANCELLED", "FULFILLED")
INVOICE_STATUSES = ("DRAFT", "ISSUED", "PARTIALLY_PAID", "PAID", "VOID", "REFUNDED", "PARTIALLY_REFUNDED")
REFUND_STATUSES = ("DRAFT", "APPROVED", "PAID", "VOID")


def _line_owner_check(fk_column: str) -> CheckConstraint:
    return CheckConstraint(
        "(plan_id IS NOT NULL)::int + (addon_id IS NOT NULL)::int = 1",
        name=f"ck_{fk_column}_exactly_one_of_plan_addon",
    )


class Quote(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "owner_quotes"
    __table_args__ = (
        CheckConstraint(
            "(customer_id IS NOT NULL)::int + (lead_id IS NOT NULL)::int >= 1",
            name="ck_owner_quotes_at_least_one_of_customer_lead",
        ),
    )

    # Phase 9.5D Milestone 7 -- additive. Non-Negotiable Rule 13 requires
    # "a Quote may originate from a qualified Lead" but the original
    # Phase 9.5A migration only ever gave Quote a required customer_id --
    # no way to represent a Lead-based Quote at all. customer_id is
    # relaxed to nullable and lead_id added; at least one is required
    # (never neither), but NOT exactly-one -- once a Lead-based Quote's
    # boundary conversion runs (Milestone 7), customer_id is populated
    # while lead_id is deliberately retained as the permanent historical
    # origin marker (same pattern as Customer.converted_from_lead_id).
    # See docs/owner/phase9_5d/lead-quote-customer-boundary.md.
    customer_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("owner_customers.id"))
    lead_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("owner_leads.id"))
    created_by_employee_profile_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_employee_profiles.id"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(16), default="DRAFT", nullable=False)
    quote_number: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    subtotal: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0, nullable=False)
    discount_total: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0, nullable=False)
    total: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0, nullable=False)
    valid_until: Mapped[date | None] = mapped_column(Date)
    notes: Mapped[str | None] = mapped_column(Text)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rejected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    lines: Mapped[list["QuoteLine"]] = relationship(order_by="QuoteLine.sort_order")


class QuoteLine(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "owner_quote_lines"
    __table_args__ = (_line_owner_check("owner_quote_lines"),)

    quote_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("owner_quotes.id"), nullable=False)
    plan_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("owner_plans.id"))
    addon_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("owner_addons.id"))
    price_version_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    description: Mapped[str] = mapped_column(String(300), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    overridden_unit_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    override_reason: Mapped[str | None] = mapped_column(Text)
    discount_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    line_total: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # Phase 9.5D Milestone 6 -- additive. Pins CommercialApproval.
    # target_version_at_request to THIS line's own version, not the
    # parent Quote's -- the Quote's version increments on unrelated
    # actions (submit, sibling line adds) that must never invalidate a
    # still-accurate pending approval for this line.
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)


class SalesOrder(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "owner_sales_orders"

    quote_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("owner_quotes.id"))
    customer_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("owner_customers.id"), nullable=False)
    created_by_employee_profile_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_employee_profiles.id"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(16), default="DRAFT", nullable=False)
    order_number: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    total: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0, nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    fulfilled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    lines: Mapped[list["SalesOrderLine"]] = relationship(order_by="SalesOrderLine.sort_order")


class SalesOrderLine(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "owner_sales_order_lines"
    __table_args__ = (_line_owner_check("owner_sales_order_lines"),)

    sales_order_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("owner_sales_orders.id"), nullable=False)
    plan_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("owner_plans.id"))
    addon_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("owner_addons.id"))
    price_version_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    description: Mapped[str] = mapped_column(String(300), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    overridden_unit_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    override_reason: Mapped[str | None] = mapped_column(Text)
    discount_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    line_total: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class CommercialInvoice(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "owner_commercial_invoices"

    sales_order_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("owner_sales_orders.id"))
    customer_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("owner_customers.id"), nullable=False)
    created_by_employee_profile_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_employee_profiles.id"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(24), default="DRAFT", nullable=False)
    invoice_number: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    subtotal: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0, nullable=False)
    discount_total: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0, nullable=False)
    tax_total: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0, nullable=False)
    total: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0, nullable=False)
    issued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    due_date: Mapped[date | None] = mapped_column(Date)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    lines: Mapped[list["CommercialInvoiceItem"]] = relationship(order_by="CommercialInvoiceItem.sort_order")


class CommercialInvoiceItem(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "owner_commercial_invoice_items"
    __table_args__ = (_line_owner_check("owner_commercial_invoice_items"),)

    commercial_invoice_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_commercial_invoices.id"), nullable=False
    )
    plan_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("owner_plans.id"))
    addon_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("owner_addons.id"))
    price_version_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    description: Mapped[str] = mapped_column(String(300), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    overridden_unit_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    override_reason: Mapped[str | None] = mapped_column(Text)
    discount_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    line_total: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class CommercialRefund(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "owner_commercial_refunds"

    commercial_invoice_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_commercial_invoices.id"), nullable=False
    )
    payment_record_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("owner_payment_records.id"))
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="DRAFT", nullable=False)
    created_by_employee_profile_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_employee_profiles.id"), nullable=False
    )
    approved_by_staff_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_staff_users.id")
    )


class CommercialOperationsIdempotencyKey(Base, UUIDPKMixin, TimestampMixin):
    """Shared idempotency ledger across Quote/Order/Invoice/Payment/Refund/
    Commission operations -- one table, not one per operation type. Mirrors
    the exact real pattern already proven in issue_license_key() (Phase 6).
    See docs/owner/phase9_5a/payment-and-fulfillment-contract.md."""

    __tablename__ = "owner_commercial_operations_idempotency_keys"
    __table_args__ = (
        UniqueConstraint("idempotency_key", "operation_code", name="uq_commercial_ops_idempotency"),
    )

    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    operation_code: Mapped[str] = mapped_column(String(64), nullable=False)
    result_reference_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)


class DocumentNumberCounter(Base, UUIDPKMixin, TimestampMixin):
    """Phase 9.5D Milestone 5 -- concurrency-safe sequential document
    numbering (quote_number/order_number/invoice_number), one counter row
    per (document_type, period_key e.g. "2026"). Milestone 1's audit found
    no existing numbering-sequence generator to reuse -- this is genuinely
    new authority. See docs/owner/phase9_5d/quote-numbering-contract.md."""

    __tablename__ = "owner_document_number_counters"
    __table_args__ = (
        UniqueConstraint("document_type", "period_key", name="uq_document_number_counter"),
    )

    document_type: Mapped[str] = mapped_column(String(32), nullable=False)
    period_key: Mapped[str] = mapped_column(String(8), nullable=False)
    next_value: Mapped[int] = mapped_column(Integer, default=1, nullable=False)


APPROVAL_STATUSES = ("PENDING", "APPROVED", "REJECTED", "CANCELLED", "EXPIRED")
APPROVAL_TARGET_TYPES = ("QUOTE", "QUOTE_LINE")
APPROVAL_REASON_CODES = (
    "DISCOUNT_ABOVE_LIMIT", "PRICE_OVERRIDE", "ZERO_PRICE_LINE", "OTHER_EXCEPTION",
)


class CommercialApproval(Base, UUIDPKMixin, TimestampMixin):
    """Phase 9.5D Milestone 6 -- a separate, per-document approval record,
    matching the existing RenewalRequest/PendingActivation/PilotRecord
    convention (status enum + dedicated approve/reject function +
    permanent record) -- deliberately NOT folded into Quote.status
    (Milestone 1's audit: a generic Approval model would itself be the
    duplication-risk anti-pattern this phase's audit exists to prevent;
    a per-document-type approval record matches the established
    convention instead)."""

    __tablename__ = "owner_commercial_approvals"
    __table_args__ = (
        Index("ix_owner_commercial_approvals_target", "target_type", "target_id"),
    )

    target_type: Mapped[str] = mapped_column(String(16), nullable=False)
    target_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    target_version_at_request: Mapped[int] = mapped_column(Integer, nullable=False)
    reason_code: Mapped[str] = mapped_column(String(32), nullable=False)
    requested_by_staff_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_staff_users.id"), nullable=False
    )
    requested_values: Mapped[dict] = mapped_column(JSONB, nullable=False)
    original_values: Mapped[dict] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="PENDING", nullable=False)
    decided_by_staff_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_staff_users.id")
    )
    decision_reason: Mapped[str | None] = mapped_column(Text)
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)


class PaymentAllocation(Base, UUIDPKMixin, TimestampMixin):
    """Phase 9.5D Milestone 11 -- many-to-many Payment<->Invoice allocation,
    a genuine additive extension beyond Phase 9.5A's original implicit
    1-payment-to-1-invoice design (PaymentRecord.commercial_invoice_id).
    See docs/owner/phase9_5d/payment-allocation-contract.md."""

    __tablename__ = "owner_payment_allocations"

    payment_record_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_payment_records.id"), nullable=False, index=True
    )
    commercial_invoice_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_commercial_invoices.id"), nullable=False, index=True
    )
    allocated_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    allocated_by_staff_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_staff_users.id"), nullable=False
    )
    allocated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    reversed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reversed_by_staff_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_staff_users.id")
    )
    reversal_reason: Mapped[str | None] = mapped_column(Text)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
