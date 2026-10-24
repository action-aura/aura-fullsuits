"""Subscription lifecycle, renewal, and payment-record services (Part L/M)."""
from __future__ import annotations

from datetime import date

from sqlalchemy import select

from app.audit.services import record as audit_record
from app.commercial_sales.sale_attribution import sale_owner_staff_user_id_for_payment
from app.extensions import db_session
from app.models.commercial_ops import PaymentCorrectionHistory
from app.models.commercial_sales import PaymentAllocation
from app.models.subscriptions import PaymentRecord, RenewalRecord, Subscription, SubscriptionStatusHistory

VALID_TRANSITIONS: dict[str, set[str]] = {
    "DRAFT": {"PILOT", "ACTIVE", "CANCELLED"},
    "PILOT": {"ACTIVE", "COMPLETED", "CANCELLED"},
    "ACTIVE": {"PAST_DUE", "SUSPENDED", "EXPIRED", "CANCELLED"},
    "PAST_DUE": {"ACTIVE", "SUSPENDED", "EXPIRED", "CANCELLED"},
    "SUSPENDED": {"ACTIVE", "CANCELLED", "EXPIRED"},
    # EXPIRED stays terminal HERE deliberately (security fix, Phase 8
    # Milestone 2 follow-up): this table is consulted by
    # transition_subscription(), which the generic POST
    # /subscriptions/<id>/transition route calls with a caller-supplied
    # to_status, gated only by subscriptions.update -- no recent-auth/MFA,
    # no separation-of-duties, no payment check. Widening this entry to
    # allow EXPIRED -> ACTIVE (as an earlier version of this commit did)
    # would let any staff member with that one permission revive an
    # expired subscription for free through that route, completely
    # bypassing the whole renewal-approval pipeline
    # (owner/app/commercial_ops/renewal_requests.py) Milestone 2 built
    # specifically to gate that action. Reviving an EXPIRED subscription is
    # ONLY possible through apply_renewal_request(), which does not call
    # transition_subscription() or consult this table at all -- it checks
    # its own private _REVIVABLE_SUBSCRIPTION_STATUSES allowlist and writes
    # the SubscriptionStatusHistory row directly, inside the same guarded,
    # audited, MFA-gated transaction that also fixes up the term dates (an
    # EXPIRED subscription revived without new term dates would just be
    # ACTIVE with a past end_date, a broken state this table's generic
    # caller has no way to prevent).
    "EXPIRED": set(),
    "CANCELLED": set(),
    "COMPLETED": set(),
}

PAYMENT_STATUSES = ("PENDING", "CONFIRMED", "FAILED", "REFUNDED", "VOIDED")


class InvalidTransitionError(ValueError):
    pass


class PaymentSelfVerificationError(ValueError):
    """A payment would reach CONFIRMED without a second person.

    commercial_sales/payments.py::confirm_payment enforced the two-person
    rule (SELF_CONFIRMATION_FORBIDDEN), but record_payment and
    correct_payment below are the only two functions that ever write
    PaymentRecord.status, and the older subscriptions routes call them
    directly -- so the rule had to live HERE to bind every caller. Before
    this (Step 0, 2026-10-24, tests/test_payment_two_person_rule.py):
    POST /subscriptions/payments with status=CONFIRMED let a SALES user
    (payments.create) record money as confirmed from birth; and
    POST /subscriptions/payments/<id>/correct let the recorder confirm their
    own payment, or overwrite the real checker's verified_by with their own
    id. CONFIRMED is what cash closing, dashboards, reports and invoice
    allocation count as real money.

    A ValueError subclass so existing `except ValueError` callers still
    treat it as a refused write, never as a crash."""


class PaymentAllocatedError(ValueError):
    """A CONFIRMED payment would stop being CONFIRMED while a live (not
    reversed) allocation of it stands.

    Step 0 fix round 4 (2026-10-24). The allocation is what posted the
    seller's commission and let the sale issue its licence; the legacy
    POST /subscriptions/payments/<id>/correct route could still set such a
    payment FAILED/VOIDED/REFUNDED/PENDING, leaving the allocation, the
    commission and the licence standing on money the books now say never
    arrived (measured on 69608e4b, tests/test_payment_allocation_ownership.py).
    Unwinding goes through the invoice page: reverse the allocation first.

    A ValueError subclass, like PaymentSelfVerificationError, so existing
    `except ValueError` callers treat it as a refused write."""

    code = "PAYMENT_ALLOCATED"


def transition_subscription(subscription: Subscription, to_status: str, actor_staff_user_id, reason: str | None = None) -> None:
    allowed = VALID_TRANSITIONS.get(subscription.status, set())
    if to_status not in allowed:
        raise InvalidTransitionError(f"Cannot transition subscription from {subscription.status} to {to_status}.")
    from_status = subscription.status
    subscription.status = to_status
    if to_status == "CANCELLED":
        from app.models.base import utcnow

        subscription.cancellation_date = utcnow().date()
        subscription.cancellation_reason = reason
    db_session.add(
        SubscriptionStatusHistory(
            subscription_id=subscription.id,
            from_status=from_status,
            to_status=to_status,
            changed_by_staff_user_id=actor_staff_user_id,
            reason=reason,
        )
    )
    db_session.commit()
    audit_record(
        actor_staff_user_id=actor_staff_user_id,
        actor_role_snapshot=None,
        action_code="SUBSCRIPTION_STATUS_CHANGED",
        entity_type="subscription",
        entity_public_id=str(subscription.id),
        before_state={"status": from_status},
        after_state={"status": to_status},
        reason=reason,
    )


def create_subscription(fields: dict, actor_staff_user_id) -> Subscription:
    subscription = Subscription(status="DRAFT", created_by_staff_user_id=actor_staff_user_id, **fields)
    db_session.add(subscription)
    db_session.commit()
    audit_record(
        actor_staff_user_id=actor_staff_user_id,
        actor_role_snapshot=None,
        action_code="SUBSCRIPTION_CREATED",
        entity_type="subscription",
        entity_public_id=str(subscription.id),
        after_state={"status": subscription.status, "customer_id": str(subscription.customer_id)},
    )
    return subscription


def record_renewal(
    subscription: Subscription, new_end_date: date, new_plan_id, reason: str | None, actor_staff_user_id
) -> RenewalRecord:
    renewal = RenewalRecord(
        subscription_id=subscription.id,
        previous_end_date=subscription.end_date,
        new_end_date=new_end_date,
        previous_plan_id=subscription.plan_id,
        new_plan_id=new_plan_id or subscription.plan_id,
        reason=reason,
        approved_by_staff_user_id=actor_staff_user_id,
    )
    subscription.end_date = new_end_date
    subscription.renewal_date = new_end_date
    if new_plan_id:
        subscription.plan_id = new_plan_id
    db_session.add(renewal)
    db_session.commit()
    audit_record(
        actor_staff_user_id=actor_staff_user_id,
        actor_role_snapshot=None,
        action_code="SUBSCRIPTION_RENEWED",
        entity_type="subscription",
        entity_public_id=str(subscription.id),
        after_state={"new_end_date": str(new_end_date)},
    )
    return renewal


def record_payment(fields: dict, actor_staff_user_id) -> PaymentRecord:
    if fields.get("status") not in PAYMENT_STATUSES:
        raise ValueError(f"Invalid payment status: {fields.get('status')}")
    # Recording is never confirming (Non-Negotiable Rule 4). A payment born
    # CONFIRMED has had no checker at all -- not even the recorder in a
    # second step -- and verified_by stays NULL, so nothing downstream could
    # tell. It starts PENDING and a different person confirms it.
    if fields.get("status") == "CONFIRMED":
        raise PaymentSelfVerificationError(
            "A payment cannot be recorded as already confirmed. Record it as PENDING; "
            "a different staff member must confirm it."
        )
    payment = PaymentRecord(recorded_by_staff_user_id=actor_staff_user_id, **fields)
    db_session.add(payment)
    db_session.commit()
    audit_record(
        actor_staff_user_id=actor_staff_user_id,
        actor_role_snapshot=None,
        action_code="PAYMENT_RECORD_CREATED",
        entity_type="payment_record",
        entity_public_id=str(payment.id),
        after_state={"amount": str(payment.amount), "currency": payment.currency, "status": payment.status},
    )
    return payment


def lock_payment_for_update(payment: PaymentRecord) -> PaymentRecord:
    """Row-lock the payment for the rest of the caller's transaction and
    re-read it from the database into the SAME object (populate_existing).

    Step 0 fix round 3 (2026-10-24). Every status decision used to read
    payment.status off the caller's in-memory row -- loaded when the page
    was opened, or earlier in a long confirmation -- with no lock. Measured
    on 580cd65a (tests/test_issuance_sale_hardening.py): a payments-screen
    Reject reported success (FAILED) while the one-screen sale, holding a
    stale PENDING row, confirmed it anyway and issued the licence; two
    managers pressing Confirm both "succeeded"; allocation allocated a
    payment that had been set FAILED after it was loaded -- a licence and a
    commission against money the company did not get. Every decision that
    depends on the current status takes this lock and decides on the fresh
    row: confirm_payment, reject_payment, correct_payment (the one writer),
    allocate_payment and the one-screen sale's confirm/reject.

    Lock order (no cycles): a caller's advisory locks (the one-screen
    sale's per-order lock, fulfillment's per-order lock -- each on its own
    dedicated connection) come first; then this payment row; then the
    invoice row (allocate_payment); then the order/licence rows
    (fulfillment). Nothing that holds an invoice, order or licence row lock
    ever asks for a payment row lock, and nothing waits for an advisory lock
    while holding this one (fulfill_order runs after the payment's lock was
    released by its own commit). Re-taking it in the same transaction is a
    no-op, so confirm_payment -> correct_payment is safe."""
    return db_session.execute(
        select(PaymentRecord)
        .where(PaymentRecord.id == payment.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalars().one()


def release_lock_and_raise(exc: Exception):
    # Release the row lock now, not when the request's session is torn
    # down: a refused decision must not keep a second manager waiting.
    db_session.rollback()
    raise exc


def correct_payment(payment: PaymentRecord, new_status: str, note: str, actor_staff_user_id) -> None:
    if new_status not in PAYMENT_STATUSES:
        raise ValueError(f"Invalid payment status: {new_status}")
    # Decide on the current row, under a lock (lock_payment_for_update): the
    # checks below and the "before" written to the history row must not
    # come from a row that changed since the caller loaded it.
    payment = lock_payment_for_update(payment)
    # Confirmed money that was allocated stays confirmed until the
    # allocation is reversed (PaymentAllocatedError). Decided under the
    # payment row lock that allocate_payment also takes first, so a
    # correction and an allocation of one payment cannot both pass: the
    # second sees the first's committed result. Only LEAVING CONFIRMED is
    # refused -- re-saving it as CONFIRMED (a note fix) and correcting a
    # confirmed payment nobody allocated yet both stay allowed.
    if payment.status == "CONFIRMED" and new_status != "CONFIRMED":
        live_allocation = db_session.execute(
            select(PaymentAllocation.id).where(
                PaymentAllocation.payment_record_id == payment.id, PaymentAllocation.reversed_at.is_(None)
            )
        ).first()
        if live_allocation is not None:
            release_lock_and_raise(PaymentAllocatedError(
                "This payment is confirmed and allocated to an invoice. Reverse the allocation on the invoice "
                "page first, then correct the payment."
            ))
    # The two-person rule, for every caller (confirm_payment checks it too,
    # before calling here; the legacy correct route did not check it at
    # all). Keyed on the RESULT being CONFIRMED, not on the transition into
    # it: a recorder re-saving an already-confirmed payment (e.g. to edit
    # its note) would otherwise overwrite the real checker's
    # verified_by_staff_user_id with their own id below. Moving their own
    # payment to any other status (VOIDED for a duplicate entry, FAILED for
    # a bounced cheque) never turns a claim into counted money, so it stays
    # allowed. Same comparison confirm_payment makes.
    if new_status == "CONFIRMED" and payment.recorded_by_staff_user_id == actor_staff_user_id:
        release_lock_and_raise(PaymentSelfVerificationError(
            "The staff member who recorded a payment cannot also confirm it; a different staff member must."
        ))
    # Step 0, the owner's rule "nobody confirms their own sale" (2026-10-24):
    # the SELLER is a second person who must not be the checker either. A
    # manager may record a sale on a seller's behalf, so "not the recorder"
    # alone would let that seller -- if they also hold payments.confirm --
    # confirm the money their own commission is counted from. Checked here,
    # in the one writer of PaymentRecord.status, for the same reason as the
    # recorder check above: it binds confirm_payment, the one-screen sale
    # confirmation and the legacy correct route alike.
    if new_status == "CONFIRMED" and actor_staff_user_id is not None:
        if sale_owner_staff_user_id_for_payment(payment) == actor_staff_user_id:
            release_lock_and_raise(PaymentSelfVerificationError(
                "The seller of a sale cannot confirm its payment; a different staff member must."
            ))
    before = payment.status
    before_note = payment.internal_note
    payment.status = new_status
    payment.verified_by_staff_user_id = actor_staff_user_id
    payment.internal_note = note
    # Phase 8 Part F: "correcting a payment must create a correction record
    # or history entry... no hard overwrite of verified financial
    # metadata." payment.status/internal_note still describe the record's
    # current state (a correction legitimately changes those), but this row
    # preserves what they were immediately before this specific correction,
    # so the full correction history remains reconstructable.
    db_session.add(
        PaymentCorrectionHistory(
            payment_record_id=payment.id,
            previous_status=before,
            new_status=new_status,
            previous_internal_note=before_note,
            correction_note=note,
            corrected_by_staff_user_id=actor_staff_user_id,
        )
    )
    db_session.commit()
    audit_record(
        actor_staff_user_id=actor_staff_user_id,
        actor_role_snapshot=None,
        action_code="PAYMENT_RECORD_CORRECTED",
        entity_type="payment_record",
        entity_public_id=str(payment.id),
        before_state={"status": before},
        after_state={"status": new_status},
        reason=note,
    )
