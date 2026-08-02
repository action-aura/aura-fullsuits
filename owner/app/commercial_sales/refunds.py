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
    db_session.commit()

    audit_record(
        actor_staff_user_id=actor_staff_user_id,
        actor_role_snapshot=None,
        action_code="REFUND_CONFIRMED",
        entity_type="commercial_refund",
        entity_public_id=str(refund.id),
        after_state={"status": "PAID", "invoice_status": {"before": before_status, "after": invoice.status}},
    )

    _apply_entitlement_consequence(invoice, is_full_refund=is_full_refund, actor_staff_user_id=actor_staff_user_id)

    from app.commissions.ledger import reverse_commissions_for_refund

    reverse_commissions_for_refund(
        invoice,
        refund_amount=refund.amount,
        collected_amount=collected,
        reason=f"Refund {refund.id} confirmed against invoice {invoice.id}",
        actor_staff_user_id=actor_staff_user_id,
    )
    return refund


def _apply_entitlement_consequence(invoice: CommercialInvoice, *, is_full_refund: bool, actor_staff_user_id: uuid.UUID) -> None:
    """Item #7: refund after fulfillment must invoke the configured
    entitlement consequence -- closes Milestone 12's own forward
    reference now that Milestone 13's fulfillment link (Subscription.
    sales_order_id) exists to act on. Non-Negotiable Rule 7 -- never a
    silent choice."""
    from app.commercial_sales.entitlement_consequence import SUSPEND_ENTITLEMENTS, determine_entitlement_consequence
    from app.models.commercial_sales import SalesOrder
    from app.models.subscriptions import Subscription
    from app.subscriptions.services import transition_subscription

    if invoice.sales_order_id is None:
        return
    order = db_session.get(SalesOrder, invoice.sales_order_id)
    order_was_fulfilled = order is not None and order.status == "FULFILLED"

    consequence = determine_entitlement_consequence(order_was_fulfilled=order_was_fulfilled, is_full_refund=is_full_refund)
    if consequence != SUSPEND_ENTITLEMENTS:
        return

    subscription = db_session.execute(
        select(Subscription).where(Subscription.sales_order_id == order.id)
    ).scalars().first()
    if subscription is not None and subscription.status == "ACTIVE":
        transition_subscription(subscription, "SUSPENDED", actor_staff_user_id, reason="Full refund confirmed against the fulfilling invoice")
        audit_record(
            actor_staff_user_id=actor_staff_user_id,
            actor_role_snapshot=None,
            action_code="ENTITLEMENT_CONSEQUENCE_APPLIED",
            entity_type="subscription",
            entity_public_id=str(subscription.id),
            after_state={"consequence": consequence, "status": "SUSPENDED"},
        )


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
