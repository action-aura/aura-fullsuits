"""Subscription lifecycle, renewal, and payment-record services (Part L/M)."""
from __future__ import annotations

from datetime import date

from app.audit.services import record as audit_record
from app.extensions import db_session
from app.models.subscriptions import PaymentRecord, RenewalRecord, Subscription, SubscriptionStatusHistory

VALID_TRANSITIONS: dict[str, set[str]] = {
    "DRAFT": {"PILOT", "ACTIVE", "CANCELLED"},
    "PILOT": {"ACTIVE", "COMPLETED", "CANCELLED"},
    "ACTIVE": {"PAST_DUE", "SUSPENDED", "EXPIRED", "CANCELLED"},
    "PAST_DUE": {"ACTIVE", "SUSPENDED", "EXPIRED", "CANCELLED"},
    "SUSPENDED": {"ACTIVE", "CANCELLED", "EXPIRED"},
    "EXPIRED": set(),
    "CANCELLED": set(),
    "COMPLETED": set(),
}

PAYMENT_STATUSES = ("PENDING", "CONFIRMED", "FAILED", "REFUNDED", "VOIDED")


class InvalidTransitionError(ValueError):
    pass


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


def correct_payment(payment: PaymentRecord, new_status: str, note: str, actor_staff_user_id) -> None:
    if new_status not in PAYMENT_STATUSES:
        raise ValueError(f"Invalid payment status: {new_status}")
    before = payment.status
    payment.status = new_status
    payment.verified_by_staff_user_id = actor_staff_user_id
    payment.internal_note = note
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
