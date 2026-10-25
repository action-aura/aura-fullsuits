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
from app.commercial_sales.sale_attribution import direct_sale_order_id_for_payment
from app.commissions import ladder
from app.commissions.ledger import (
    audit_earning,
    audit_reversal,
    post_earning_for_allocation,
    reverse_commissions_for_allocation,
)
from app.extensions import db_session
from app.models.base import utcnow
from app.models.commercial_sales import CommercialInvoice, PaymentAllocation
from app.models.subscriptions import PaymentRecord
from app.subscriptions.services import lock_payment_for_update


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
    # Step 0 fix round 3 -- decided on the payment row as it is NOW, locked
    # until this allocation commits (subscriptions/services.py::
    # lock_payment_for_update, which documents the lock order: payment row,
    # then the invoice row below). The status used to be read once off the
    # caller's in-memory row: measured on 580cd65a, a payment set FAILED
    # after the one-screen sale loaded it was still allocated, its
    # commission posted and its licence issued. Locked, a status change
    # either lands before this check (refused here) or waits until the
    # allocation is committed (and is then refused by its own re-read).
    payment = lock_payment_for_update(payment)
    try:
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
        # Step 0 fix round 4 -- a payment recorded FOR a sale goes to that
        # sale's invoice and no other. The commission ledger credits the
        # TARGET invoice's creator, so allocating a sale's payment to a
        # different invoice of the same customer handed the commission to
        # someone who did not make the sale. Measured on 69608e4b
        # (tests/test_payment_allocation_ownership.py): a one-screen sale's
        # payment allocated to another seller's invoice credited that seller
        # 10.00 and left the real sale unable to finish. Same shape for a
        # quote-pipeline payment that names its invoice -- and there it was
        # also a two-person side door: the confirmer is checked against the
        # NAMED invoice's seller (sale_attribution), so the creator of
        # another invoice could confirm the payment and then allocate it to
        # their own invoice. A payment linked to neither (a customer
        # payment covering several invoices) still goes wherever it belongs.
        sale_order_id = direct_sale_order_id_for_payment(payment.id)
        if sale_order_id is not None and invoice.sales_order_id != sale_order_id:
            raise CommercialSalesError("PAYMENT_BELONGS_TO_ANOTHER_SALE")
        if payment.commercial_invoice_id is not None and payment.commercial_invoice_id != invoice.id:
            raise CommercialSalesError("PAYMENT_BELONGS_TO_ANOTHER_INVOICE")

        # Row-level lock on the invoice for the duration of this allocation --
        # two concurrent allocation attempts against the same invoice must
        # serialize so neither can push the allocated sum past the invoice
        # total based on a stale outstanding-balance read. populate_existing:
        # the status/total decided on below are the locked row's, not the
        # caller's copy.
        db_session.execute(
            select(CommercialInvoice).where(CommercialInvoice.id == invoice.id).with_for_update()
            .execution_options(populate_existing=True)
        )

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

        # Non-Negotiable: commission basis is confirmed Payment Allocation --
        # this is the real earning trigger, not Quote/Order/Invoice creation
        # or bare payment confirmation.
        #
        # Step 0 fix round 3: posted IN THE SAME TRANSACTION as the
        # allocation (commit=False), so the two exist together or not at
        # all. They used to be two commits; a crash between them left an
        # allocation without its commission, and every resume (the
        # one-screen sale, a second Allocate press) reads "an allocation
        # exists" as "done" -- the seller's commission was lost for good
        # (580cd65a, verifier probe P1). Exactly once is still the ledger's
        # partial unique index on source_payment_allocation_id.
        entry = post_earning_for_allocation(allocation, actor_staff_user_id=actor_staff_user_id, commit=False)
        db_session.commit()
    except BaseException:
        db_session.rollback()
        raise

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

    if entry is not None:
        audit_earning(entry, actor_staff_user_id=actor_staff_user_id)
    ladder.write_pending_audit(actor_staff_user_id)

    return allocation


def reverse_allocation(
    allocation: PaymentAllocation, *, reason: str, actor_staff_user_id: uuid.UUID
) -> PaymentAllocation:
    if not reason or not reason.strip():
        raise CommercialSalesError("REASON_REQUIRED")

    # Step 0 ladder (2026-10-24) -- the gap 7571c3df reported: this reversed
    # the allocation and left the commission it had posted EARNED (measured:
    # reverse a finished sale's allocation -> commission still EARNED 10.00),
    # so the seller kept money for a sale whose money had been taken off it.
    # Now the reversal, the percentage commission it takes back
    # (ledger.reverse_commissions_for_allocation) and the ladder re-derived
    # (ladder.evaluate_sale: the sale stops counting -- a correction, so
    # whatever the hold) are ONE transaction; a crash leaves none of them.
    # Locked in the documented order (subscriptions/services.py::
    # lock_payment_for_update): the payment row first -- correct_payment
    # decides "is this payment allocated?" under that same lock -- then the
    # invoice row; then the seller's ladder lock, before any commission row.
    try:
        lock_payment_for_update(db_session.get(PaymentRecord, allocation.payment_record_id))
        locked_invoice = db_session.execute(
            select(CommercialInvoice).where(CommercialInvoice.id == allocation.commercial_invoice_id)
            .with_for_update().execution_options(populate_existing=True)
        ).scalars().one()
        # Step 0 fix round (2026-10-25): the seller lock NOW, before the
        # percentage reversal below inserts a row whose FK takes FOR KEY SHARE
        # on the row being reversed -- an approval of that row holds the
        # seller lock and asks FOR UPDATE on it; taken after the insert (as on
        # d15d11f0, inside ladder.evaluate_sale) the two deadlocked (verifier
        # probe v12, same shape as confirm_refund).
        ladder.lock_seller(locked_invoice.created_by_employee_profile_id)
        allocation = db_session.execute(
            select(PaymentAllocation).where(PaymentAllocation.id == allocation.id)
            .execution_options(populate_existing=True)
        ).scalars().one()
        if allocation.reversed_at is not None:
            raise CommercialSalesError("INVALID_INVOICE_TRANSITION", from_status="REVERSED", to_status="REVERSED")
        invoice = db_session.get(CommercialInvoice, allocation.commercial_invoice_id)

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
        db_session.flush()

        reversals = reverse_commissions_for_allocation(
            allocation, reason=f"Allocation {allocation.id} reversed: {reason}", actor_staff_user_id=actor_staff_user_id,
        )
        if invoice.sales_order_id is not None:
            ladder.evaluate_sale(invoice.sales_order_id)
        db_session.commit()
    except BaseException:
        db_session.rollback()
        raise

    for reversal in reversals:
        audit_reversal(reversal, reason=f"Allocation {allocation.id} reversed: {reason}",
                       actor_staff_user_id=actor_staff_user_id)
    ladder.write_pending_audit(actor_staff_user_id)

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
