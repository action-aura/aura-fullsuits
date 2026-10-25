"""Phase 9.5D Milestone 12 -- Commercial Refund domain service.

Built on the existing CommercialRefund schema (Phase 9.5A), extended
this milestone with version/approved_at/paid_at/voided_at (migration
f2b7d4e91a63 -- a real, proven gap, see app/models/commercial_sales.py).
A Refund is a real, separate, immutable-history record -- never a
negative PaymentRecord row, never an edit to the original payment
(docs/owner/phase9_5a/payment-and-fulfillment-contract.md, unchanged).
See docs/owner/phase9_5d/refund-domain-contract.md,
refund-entitlement-consequence-policy.md.
"""
from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy import select

from app.audit.services import record as audit_record
from app.commercial_sales.calculator import calculate_refundable_balance, validate_refund_amount
from app.commercial_sales.errors import REFUND_TRANSITIONS, CommercialSalesError
from app.commercial_sales.invoices import confirmed_allocated_amount
from app.extensions import db_session
from app.models.base import utcnow
from app.models.commercial_sales import CommercialInvoice, CommercialRefund


def total_confirmed_refunds(invoice: CommercialInvoice) -> Decimal:
    amounts = db_session.execute(
        select(CommercialRefund.amount).where(CommercialRefund.commercial_invoice_id == invoice.id, CommercialRefund.status == "PAID")
    ).scalars().all()
    return sum(amounts, Decimal("0.00"))


def refundable_balance(invoice: CommercialInvoice) -> Decimal:
    """Refundable base is money actually collected (confirmed+allocated),
    not the invoice's face total -- you cannot refund money that was
    never received. Non-Negotiable: 'refund cannot exceed refundable
    amount', 'prior refunds included'."""
    return calculate_refundable_balance(confirmed_allocated_amount(invoice), total_confirmed_refunds(invoice))


def create_refund(
    invoice: CommercialInvoice,
    *,
    amount: Decimal,
    reason: str,
    payment_record_id: uuid.UUID | None,
    actor_employee_profile_id: uuid.UUID,
    actor_staff_user_id: uuid.UUID,
) -> CommercialRefund:
    if not reason or not reason.strip():
        raise CommercialSalesError("REASON_REQUIRED")

    amount = validate_refund_amount(amount, refundable_balance(invoice))

    refund = CommercialRefund(
        commercial_invoice_id=invoice.id,
        payment_record_id=payment_record_id,
        amount=amount,
        currency=invoice.currency,
        reason=reason,
        status="DRAFT",
        created_by_employee_profile_id=actor_employee_profile_id,
        version=1,
    )
    db_session.add(refund)
    db_session.commit()

    audit_record(
        actor_staff_user_id=actor_staff_user_id,
        actor_role_snapshot=None,
        action_code="REFUND_REQUESTED",
        entity_type="commercial_refund",
        entity_public_id=str(refund.id),
        after_state={"commercial_invoice_id": str(invoice.id), "amount": str(amount)},
    )
    return refund


def _check_transition(status: str, target: str) -> None:
    if target not in REFUND_TRANSITIONS.get(status, set()):
        raise CommercialSalesError("INVALID_REFUND_TRANSITION", from_status=status, to_status=target)


def _check_version(refund: CommercialRefund, expected_version: int | None) -> None:
    if expected_version is not None and refund.version != expected_version:
        raise CommercialSalesError("STALE_VERSION")


def approve_refund(
    refund: CommercialRefund, *, actor_staff_user_id: uuid.UUID, expected_version: int | None = None
) -> CommercialRefund:
    """Non-Negotiable Rule 12: sales staff cannot self-approve a refund --
    enforced here regardless of permission grant, matching the exact
    self-approval-block pattern already used for CommercialApproval and
    payment confirmation."""
    _check_version(refund, expected_version)
    _check_transition(refund.status, "APPROVED")

    creator_staff_user_id = _resolve_creator_staff_user_id(refund)
    if creator_staff_user_id == actor_staff_user_id:
        raise CommercialSalesError("SELF_APPROVAL_FORBIDDEN")

    refund.status = "APPROVED"
    refund.approved_by_staff_user_id = actor_staff_user_id
    refund.approved_at = utcnow()
    refund.version += 1
    db_session.commit()

    audit_record(
        actor_staff_user_id=actor_staff_user_id,
        actor_role_snapshot=None,
        action_code="REFUND_APPROVED",
        entity_type="commercial_refund",
        entity_public_id=str(refund.id),
        after_state={"status": "APPROVED"},
    )
    return refund


def _resolve_creator_staff_user_id(refund: CommercialRefund) -> uuid.UUID | None:
    from app.models.employees import EmployeeProfile

    profile = db_session.get(EmployeeProfile, refund.created_by_employee_profile_id)
    return profile.staff_user_id if profile else None


def confirm_refund(
    refund: CommercialRefund, *, actor_staff_user_id: uuid.UUID, expected_version: int | None = None
) -> CommercialRefund:
    _check_version(refund, expected_version)
    _check_transition(refund.status, "PAID")

    invoice = db_session.get(CommercialInvoice, refund.commercial_invoice_id)
    # Milestone 24 (financial-property pass) -- real gap found: nothing
    # previously re-validated the refund total at confirm time, nor
    # locked the invoice. Two DRAFT refunds can each independently pass
    # create_refund()'s own validate_refund_amount() check (each within
    # the refundable balance individually) while their SUM exceeds it --
    # without a lock + re-check here, both could be confirmed
    # concurrently and over-refund the customer past what was actually
    # collected. Row-locked, matching allocate_payment()'s own pattern.
    db_session.execute(select(CommercialInvoice).where(CommercialInvoice.id == invoice.id).with_for_update())

    collected = confirmed_allocated_amount(invoice)
    already_refunded = total_confirmed_refunds(invoice)  # excludes this refund -- still pre-PAID
    if already_refunded + refund.amount > collected:
        raise CommercialSalesError("REFUND_EXCEEDS_REFUNDABLE", amount=refund.amount, refundable=collected - already_refunded)

    refund.status = "PAID"
    refund.paid_at = utcnow()
    refund.version += 1
    db_session.flush()

    refunded = already_refunded + refund.amount
    before_status = invoice.status
    is_full_refund = refunded >= collected and collected > 0
    if is_full_refund:
        invoice.status = "REFUNDED"
    elif refunded > 0:
        invoice.status = "PARTIALLY_REFUNDED"
    invoice.version += 1
    db_session.flush()

    # Step 0 ladder (2026-10-24): the refund, the commission it takes back and
    # the licence's stop are ONE transaction. Before (1025d626) the refund
    # committed first, then the subscription was suspended (its own commit),
    # then each commission reversal committed on its own -- a crash between
    # them left a refunded customer whose seller kept the commission.
    from app.commissions import ladder
    from app.commissions.ledger import audit_reversal, reverse_commissions_for_refund

    reason = f"Refund {refund.id} confirmed against invoice {invoice.id}"
    try:
        stops = _schedule_entitlement_consequence(refund, invoice, is_full_refund=is_full_refund,
                                                  actor_staff_user_id=actor_staff_user_id)
        reversals = reverse_commissions_for_refund(
            invoice, refund_amount=refund.amount, collected_amount=collected, reason=reason,
            actor_staff_user_id=actor_staff_user_id, commit=False,
        )
        # The ladder: a refund of package money inside the sale's 30-day hold
        # removes the sale and re-derives its month; after the hold the sale
        # stays counted and the refund is recorded on it (ladder.py).
        if invoice.sales_order_id is not None:
            ladder.evaluate_sale(invoice.sales_order_id)
        db_session.commit()
    except BaseException:
        db_session.rollback()
        raise

    audit_record(
        actor_staff_user_id=actor_staff_user_id,
        actor_role_snapshot=None,
        action_code="REFUND_CONFIRMED",
        entity_type="commercial_refund",
        entity_public_id=str(refund.id),
        after_state={"status": "PAID", "invoice_status": {"before": before_status, "after": invoice.status}},
    )
    for stop in stops:
        audit_record(
            actor_staff_user_id=actor_staff_user_id,
            actor_role_snapshot=None,
            action_code="ENTITLEMENT_CONSEQUENCE_SCHEDULED",
            entity_type="license",
            entity_public_id=str(stop.license_id),
            after_state={"to_status": stop.to_status, "effective_at": stop.effective_at.isoformat(),
                         "subscription_id": str(stop.subscription_id) if stop.subscription_id else None},
        )
    for reversal in reversals:
        audit_reversal(reversal, reason=reason, actor_staff_user_id=actor_staff_user_id)
    ladder.write_pending_audit(actor_staff_user_id)
    return refund


def _schedule_entitlement_consequence(refund: CommercialRefund, invoice: CommercialInvoice, *, is_full_refund: bool,
                                      actor_staff_user_id: uuid.UUID) -> list:
    """Item #7: refund after fulfillment must invoke the configured
    entitlement consequence -- Non-Negotiable Rule 7, never a silent choice.

    Step 0 (2026-10-24), the owner's rule: the licence keeps working 2 days
    after the refund, then stops. The consequence is SCHEDULED, flush only
    (app/licensing/scheduled_stops.py): a stop for each live licence of the
    fulfilled subscription, at the refund's paid_at + 2 days, applied by the
    licence checks themselves. It used to suspend the subscription on the
    spot (and never the licence). Only a FULL refund after fulfilment has
    this consequence -- a partial one (e.g. only the extra pair given back)
    keeps the licence, as entitlement_consequence.py has always decided."""
    from app.commercial_sales.entitlement_consequence import SUSPEND_ENTITLEMENTS, determine_entitlement_consequence
    from app.licensing.scheduled_stops import schedule_stops_after_refund
    from app.models.commercial_sales import SalesOrder
    from app.models.subscriptions import Subscription

    if invoice.sales_order_id is None:
        return []
    order = db_session.get(SalesOrder, invoice.sales_order_id)
    order_was_fulfilled = order is not None and order.status == "FULFILLED"

    consequence = determine_entitlement_consequence(order_was_fulfilled=order_was_fulfilled, is_full_refund=is_full_refund)
    if consequence != SUSPEND_ENTITLEMENTS:
        return []

    subscription = db_session.execute(
        select(Subscription).where(Subscription.sales_order_id == order.id)
    ).scalars().first()
    if subscription is None:
        return []
    return schedule_stops_after_refund(refund, subscription, actor_staff_user_id=actor_staff_user_id)


def void_refund(refund: CommercialRefund, *, actor_staff_user_id: uuid.UUID, expected_version: int | None = None) -> CommercialRefund:
    _check_version(refund, expected_version)
    _check_transition(refund.status, "VOID")

    refund.status = "VOID"
    refund.voided_at = utcnow()
    refund.version += 1
    db_session.commit()

    audit_record(
        actor_staff_user_id=actor_staff_user_id,
        actor_role_snapshot=None,
        action_code="REFUND_VOIDED",
        entity_type="commercial_refund",
        entity_public_id=str(refund.id),
        after_state={"status": "VOID"},
    )
    return refund
