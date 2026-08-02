"""Phase 9.5D Milestone 11 -- Payment allocation.

PaymentAllocation (migration b7e4a2c91f30) is a genuine, acknowledged
extension beyond Phase 9.5A's original implicit 1-payment-to-1-invoice
design (PaymentRecord.commercial_invoice_id) -- additive, not
contradicting it. See docs/owner/phase9_5d/payment-allocation-contract.md.
"""
from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy import select

from app.audit.services import record as audit_record
from app.commercial_sales.calculator import resolve_invoice_status, validate_allocation_amount
from app.commercial_sales.errors import CommercialSalesError
from app.commercial_sales.invoices import confirmed_allocated_amount
from app.commissions.ledger import post_earning_for_allocation
from app.extensions import db_session
from app.models.base import utcnow
from app.models.commercial_sales import CommercialInvoice, PaymentAllocation
from app.models.subscriptions import PaymentRecord


def unallocated_payment_balance(payment: PaymentRecord) -> Decimal:
    allocated = db_session.execute(
        select(PaymentAllocation.allocated_amount).where(
            PaymentAllocation.payment_record_id == payment.id, PaymentAllocation.reversed_at.is_(None)
        )
    ).scalars().all()
    return Decimal(payment.amount) - sum(allocated, Decimal("0.00"))


def allocate_payment(
    *,
    payment: PaymentRecord,
    invoice: CommercialInvoice,
    amount: Decimal,
    actor_staff_user_id: uuid.UUID,
) -> PaymentAllocation:
    if payment.status != "CONFIRMED":
        raise CommercialSalesError("PAYMENT_NOT_CONFIRMED")
    if payment.currency != invoice.currency:
        raise CommercialSalesError("CURRENCY_MISMATCH", given=payment.currency, expected=invoice.currency)
    # Milestone 23 (IDOR/tampering pass) -- real gap found: nothing
    # previously verified the payment and invoice belong to the same
    # customer. Without this check, a Finance actor (or a tampered
    # request supplying an arbitrary payment_record_id) could allocate
    # Customer A's confirmed payment against Customer B's invoice,
    # crediting the wrong account -- a real financial-integrity and
    # cross-tenant-data bug, not merely a permissions gap.
    if payment.customer_id != invoice.customer_id:
        raise CommercialSalesError("PAYMENT_CUSTOMER_MISMATCH")

    # Row-level lock on the invoice for the duration of this allocation --
    # two concurrent allocation attempts against the same invoice must
    # serialize so neither can push the allocated sum past the invoice
    # total based on a stale outstanding-balance read.
    db_session.execute(select(CommercialInvoice).where(CommercialInvoice.id == invoice.id).with_for_update())

    unallocated = unallocated_payment_balance(payment)
    outstanding = invoice.total - confirmed_allocated_amount(invoice)
    amount = validate_allocation_amount(amount, unallocated_payment_balance=unallocated, invoice_outstanding_balance=outstanding)

    allocation = PaymentAllocation(
        payment_record_id=payment.id,
        commercial_invoice_id=invoice.id,
        allocated_amount=amount,
        currency=invoice.currency,
        allocated_by_staff_user_id=actor_staff_user_id,
        allocated_at=utcnow(),
        version=1,
    )
    db_session.add(allocation)
    db_session.flush()

    new_status = resolve_invoice_status(
        current_status=invoice.status, invoice_total=invoice.total, allocated_payment_sum=confirmed_allocated_amount(invoice)
    )
    before_status = invoice.status
    invoice.status = new_status
    invoice.version += 1
    db_session.commit()

    audit_record(
        actor_staff_user_id=actor_staff_user_id,
        actor_role_snapshot=None,
        action_code="PAYMENT_ALLOCATED",
        entity_type="payment_allocation",
        entity_public_id=str(allocation.id),
        after_state={
            "payment_record_id": str(payment.id), "commercial_invoice_id": str(invoice.id),
            "amount": str(amount), "invoice_status": {"before": before_status, "after": new_status},
        },
    )

    # Non-Negotiable: commission basis is confirmed Payment Allocation --
    # this is the real earning trigger, not Quote/Order/Invoice creation
    # or bare payment confirmation.
    post_earning_for_allocation(allocation, actor_staff_user_id=actor_staff_user_id)

    return allocation


def reverse_allocation(
    allocation: PaymentAllocation, *, reason: str, actor_staff_user_id: uuid.UUID
) -> PaymentAllocation:
    if allocation.reversed_at is not None:
        raise CommercialSalesError("INVALID_INVOICE_TRANSITION", from_status="REVERSED", to_status="REVERSED")
    if not reason or not reason.strip():
        raise CommercialSalesError("REASON_REQUIRED")

    invoice = db_session.get(CommercialInvoice, allocation.commercial_invoice_id)
    db_session.execute(select(CommercialInvoice).where(CommercialInvoice.id == invoice.id).with_for_update())

    allocation.reversed_at = utcnow()
    allocation.reversed_by_staff_user_id = actor_staff_user_id
    allocation.reversal_reason = reason
    allocation.version += 1
    db_session.flush()

    new_status = resolve_invoice_status(
        current_status=invoice.status, invoice_total=invoice.total, allocated_payment_sum=confirmed_allocated_amount(invoice)
    )
    # Reversal can only ever reduce the allocated sum, never increase it --
    # so it can only move status "backwards" (PAID -> PARTIALLY_PAID ->
    # ISSUED), never re-trigger a VOID/DRAFT override (resolve_invoice_status
    # itself already guards VOID/DRAFT as never overridden by a payment-sum
    # computation).
    before_status = invoice.status
    invoice.status = new_status
    invoice.version += 1
    db_session.commit()

    audit_record(
        actor_staff_user_id=actor_staff_user_id,
        actor_role_snapshot=None,
        action_code="PAYMENT_ALLOCATION_REVERSED",
        entity_type="payment_allocation",
        entity_public_id=str(allocation.id),
        reason=reason,
        after_state={"invoice_status": {"before": before_status, "after": new_status}},
    )
    return allocation
