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
from app.extensions import db_session
from app.models.subscriptions import PaymentRecord
from app.subscriptions.services import correct_payment, record_payment

PAYMENT_METHODS = ("CASH", "BANK_TRANSFER", "CARD_OFFLINE", "CHEQUE", "OTHER")


def submit_payment(
    *,
    customer_id: uuid.UUID,
    amount: Decimal,
    currency: str,
    method: str,
    payment_date: date,
    commercial_invoice_id: uuid.UUID | None = None,
    reference: str | None = None,
    actor_staff_user_id: uuid.UUID,
) -> PaymentRecord:
    """Sales-employee-facing 'I received this payment, please confirm it'
    action -- Non-Negotiable Rule 4: recording is not confirming. Thin
    wrapper over the existing record_payment(), never duplicating its
    storage logic."""
    if amount <= 0:
        raise CommercialSalesError("NEGATIVE_DOCUMENT_TOTAL")
    validate_currency(currency)
    if method not in PAYMENT_METHODS:
        raise CommercialSalesError("INVALID_PAYMENT_METHOD")

    return record_payment(
        {
            "customer_id": customer_id,
            "amount": amount,
            "currency": currency,
            "method": method,
            "payment_date": payment_date,
            "commercial_invoice_id": commercial_invoice_id,
            "reference": reference,
            "status": "PENDING",
        },
        actor_staff_user_id,
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
    re-applied (PAYMENT_ALREADY_CONFIRMED)."""
    if payment.status == "CONFIRMED":
        raise CommercialSalesError("PAYMENT_ALREADY_CONFIRMED")
    if payment.status != "PENDING":
        raise CommercialSalesError("PAYMENT_NOT_CONFIRMED")

    # Maker-checker: the employee who submitted a payment cannot also be
    # the one who confirms it -- Non-Negotiable Rule 4's real teeth,
    # checked here regardless of whether the caller's payments.confirm
    # permission grant alone would have allowed it (UI hiding is not
    # authorization, same principle as the approval self-approval block).
    if payment.recorded_by_staff_user_id == actor_staff_user_id:
        raise CommercialSalesError("SELF_CONFIRMATION_FORBIDDEN")

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
    if payment.status != "PENDING":
        raise CommercialSalesError("PAYMENT_NOT_CONFIRMED")
    if not reason or not reason.strip():
        raise CommercialSalesError("REASON_REQUIRED")

    # AUDIT-033: reject_payment_route is gated on payments.confirm, same as
    # confirm_payment_route -- and FINANCE holds both payments.create and
    # payments.confirm (payments.create is deliberately dual-granted to
    # SALES and FINANCE, per payment-maker-checker-policy.md's Milestone 18
    # addendum). Without this check, one FINANCE account could record a
    # payment it received directly (e.g. a bank transfer) and unilaterally
    # reject it too -- money off the books with a single signature, no
    # second person involved. Mirrors confirm_payment()'s unconditional
    # SELF_CONFIRMATION_FORBIDDEN block; reusing the same error code rather
    # than adding a new one that would need parallel i18n_labels.py/.po
    # catalog entries for a rule that is really the same maker-checker
    # guarantee applied to the other terminal transition out of PENDING.
    if payment.recorded_by_staff_user_id == actor_staff_user_id:
        raise CommercialSalesError("SELF_CONFIRMATION_FORBIDDEN")

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
