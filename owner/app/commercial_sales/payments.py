"""Phase 9.5D Milestone 10 -- Payment confirmation orchestration.

Milestone 1's audit found the real gap this module closes: PaymentRecord
storage (app/subscriptions/services.py::record_payment/correct_payment)
is FULLY IMPLEMENTED AND REUSABLE, but no function anywhere confirms a
payment -- payments.confirm is a seeded, FINANCE-assigned permission
with zero code checking it. This module builds the missing orchestration
on top of the existing storage functions, never duplicating them. See
docs/owner/phase9_5d/payment-recording-and-confirmation-contract.md,
payment-maker-checker-policy.md.
"""
from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from app.audit.services import record as audit_record
from app.commercial_sales.errors import CommercialSalesError
from app.commercial_sales.calculator import validate_currency
from app.commercial_sales.sale_attribution import sale_owner_staff_user_id_for_payment
from app.extensions import db_session
from app.commercial_sales.packages import check_payment_amount
from app.models.subscriptions import PaymentRecord, Subscription
from app.subscriptions.services import correct_payment, lock_payment_for_update, record_payment, release_lock_and_raise

PAYMENT_METHODS = ("CASH", "BANK_TRANSFER", "CARD_OFFLINE", "CHEQUE", "OTHER")


def submit_payment(
    *,
    customer_id: uuid.UUID,
    amount: Decimal,
    currency: str,
    method: str,
    payment_date: date,
    commercial_invoice_id: uuid.UUID | None = None,
    subscription_id: uuid.UUID | None = None,
    reference: str | None = None,
    actor_staff_user_id: uuid.UUID,
) -> PaymentRecord:
    """Sales-employee-facing 'I received this payment, please confirm it'
    action -- Non-Negotiable Rule 4: recording is not confirming. Thin
    wrapper over the existing record_payment(), never duplicating its
    storage logic.

    `subscription_id` (optional): the payment is for that existing subscription
    -- the same link the subscriptions screen makes, so the payment counts toward
    it (next payment due, the renewal commission). For a monthly package the
    amount is then set against what is due (packages.check_payment_amount): a
    different amount is NEVER refused (part payments and several months at once
    are legitimate) but the gap is written into this payment's audit entry.
    Callers that want to show a warning read it with the same helper."""
    if amount <= 0:
        raise CommercialSalesError("NEGATIVE_DOCUMENT_TOTAL")
    validate_currency(currency)
    if method not in PAYMENT_METHODS:
        raise CommercialSalesError("INVALID_PAYMENT_METHOD")
    # Step 0 fix round: record_payment now refuses a date after Jordan's
    # today (PaymentDateInFutureError, a plain ValueError); refused here first
    # with this domain's own code so the form and the API show it, not a 500.
    from app.operational_reports.periods import jordan_today

    if payment_date is not None and payment_date > jordan_today():
        raise CommercialSalesError("PAYMENT_DATE_IN_FUTURE")

    subscription = None
    if subscription_id is not None:
        subscription = db_session.get(Subscription, subscription_id)
        if subscription is None:
            raise CommercialSalesError("SUBSCRIPTION_NOT_FOUND")
        if subscription.customer_id != customer_id:
            raise CommercialSalesError("SUBSCRIPTION_CUSTOMER_MISMATCH")
    # payment_date matters: contract years run by DATE, so a back-dated payment
    # (say, last year's month 13) must be judged against ITS year's activation
    # -- not today's -- or the screen and this audit entry would disagree.
    check = check_payment_amount(subscription, amount, currency, payment_date=payment_date)

    return record_payment(
        {
            "customer_id": customer_id,
            "subscription_id": subscription_id,
            "amount": amount,
            "currency": currency,
            "method": method,
            "payment_date": payment_date,
            "commercial_invoice_id": commercial_invoice_id,
            "reference": reference,
            "status": "PENDING",
        },
        actor_staff_user_id,
        audit_extra={"due_check": check.audit_state()} if check is not None else None,
    )


def confirm_payment(
    payment: PaymentRecord, *, actor_staff_user_id: uuid.UUID, note: str | None = None
) -> PaymentRecord:
    """The real, previously-missing orchestration. Non-Negotiable Rule 4:
    only an authorized Finance/management user may confirm (enforced by
    the payments.confirm permission at the route layer, Milestone 16/18 --
    this function enforces the maker-checker half that a permission grant
    alone cannot express).

    Idempotent by natural consequence of the status check: replaying a
    confirm on an already-CONFIRMED payment is rejected, not silently
    re-applied (PAYMENT_ALREADY_CONFIRMED).

    Step 0 fix round 3: the check runs on the row re-read UNDER A LOCK
    (lock_payment_for_update), held until correct_payment commits. Before,
    it ran on the caller's in-memory row: two managers who opened the same
    PENDING payment both confirmed it (two history rows, the first
    checker's verified_by overwritten), and a confirmation could overwrite
    a reject that had already been told it succeeded. Now the second
    decision waits for the first and is refused on the status it left."""
    payment = lock_payment_for_update(payment)
    if payment.status == "CONFIRMED":
        release_lock_and_raise(CommercialSalesError("PAYMENT_ALREADY_CONFIRMED"))
    if payment.status != "PENDING":
        release_lock_and_raise(CommercialSalesError("PAYMENT_NOT_CONFIRMED"))

    # Maker-checker: the employee who submitted a payment cannot also be
    # the one who confirms it -- Non-Negotiable Rule 4's real teeth,
    # checked here regardless of whether the caller's payments.confirm
    # permission grant alone would have allowed it (UI hiding is not
    # authorization, same principle as the approval self-approval block).
    if payment.recorded_by_staff_user_id == actor_staff_user_id:
        release_lock_and_raise(CommercialSalesError("SELF_CONFIRMATION_FORBIDDEN"))
    # Step 0 (owner rule 2026-10-24): nor may the seller -- a manager can
    # record a sale on a seller's behalf. correct_payment re-checks this in
    # the storage layer; checked here first for the stable, localized code.
    if sale_owner_staff_user_id_for_payment(payment) == actor_staff_user_id:
        release_lock_and_raise(CommercialSalesError("SELLER_CONFIRMATION_FORBIDDEN"))

    correct_payment(payment, "CONFIRMED", note, actor_staff_user_id)

    audit_record(
        actor_staff_user_id=actor_staff_user_id,
        actor_role_snapshot=None,
        action_code="PAYMENT_CONFIRMED",
        entity_type="payment_record",
        entity_public_id=str(payment.id),
        after_state={"status": "CONFIRMED"},
    )
    # Milestone 15 (commission ledger) will add a commission-eligibility
    # trigger call here once it exists -- per
    # docs/owner/phase9_5a/commission-domain-design.md's lifecycle
    # ("Payment confirmed -> eligibility evaluated"). Not called yet:
    # calling a function that doesn't exist would be worse than an
    # honestly-absent forward reference.
    return payment


def reject_payment(payment: PaymentRecord, *, reason: str, actor_staff_user_id: uuid.UUID) -> PaymentRecord:
    # Decided on the locked, re-read row (see confirm_payment): a Reject
    # pressed on a page opened while the payment was PENDING must not turn
    # a payment someone has since confirmed -- and allocated, and issued a
    # licence for -- into FAILED. Measured on 580cd65a: exactly that, with
    # the licence and the seller's commission left standing.
    payment = lock_payment_for_update(payment)
    if payment.status != "PENDING":
        release_lock_and_raise(CommercialSalesError("PAYMENT_NOT_CONFIRMED"))
    if not reason or not reason.strip():
        release_lock_and_raise(CommercialSalesError("REASON_REQUIRED"))

    correct_payment(payment, "FAILED", reason, actor_staff_user_id)

    audit_record(
        actor_staff_user_id=actor_staff_user_id,
        actor_role_snapshot=None,
        action_code="PAYMENT_REJECTED",
        entity_type="payment_record",
        entity_public_id=str(payment.id),
        reason=reason,
        after_state={"status": "FAILED"},
    )
    return payment
