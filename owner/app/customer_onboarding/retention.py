"""Retention of identity documents: "while a customer, plus one year" (legal review v2, (d)-4/10).

THE PROMISE (shop agreement cl. 10 / privacy notice): the photos of the owner's ID and
commercial registration are kept while the shop is a customer and for ONE YEAR after it
stops being one; the seller's own ID photo while they work for us and one year after.
Before this module nothing ever set that date: `documents.keep_for_customer_lifetime()`
cleared `purge_after` at conversion and `POST_CUSTOMER_RETENTION_DAYS` was defined and
unused, so a former customer's photos were kept for ever.

HOW IT IS DECIDED (a pure function of the current state, re-evaluated every night, so a
missed event can never leave a date wrong and nothing depends on a hook being called):

  * A customer has "paid history" when one of their subscriptions is past the trial: status
    ACTIVE, PAST_DUE, SUSPENDED, EXPIRED, CANCELLED or COMPLETED and not an unconverted trial.
    No paid history -> this module does nothing (the trial rules in trial.py own the date:
    90 days after the trial ends).
  * LIVE = an ACTIVE or PAST_DUE subscription (PAST_DUE is still inside the grace period), or
    a trial that has not ended. A live customer's documents have NO purge date (cleared if one
    had been set -- "clear it again when they pay/renew").
  * ENDED = paid history and nothing live: lapsed past grace and not renewed (EXPIRED),
    cancelled (CANCELLED), or refunded (a refund that stops the licence SUSPENDS the
    subscription). The date is the last paid-until day (the subscription's end date; for a
    suspension the day it was suspended) and purge_after = that + 365 days.

`flask documents purge-due` runs: set/clear the dates, delete what is due (audited), and drop
the customer link from trial-device marks of purged customers. Idempotent; safe nightly.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select, update

from app.audit.services import record as audit_record
from app.customer_onboarding import documents
from app.customer_onboarding.documents import POST_CUSTOMER_RETENTION_DAYS
from app.customer_onboarding.trial import _DEAD_PILOT_STATUSES, is_unconverted_trial, trial_pilot_for_subscription
from app.extensions import db_session
from app.models.customer_onboarding import CustomerDocument, TrialDeviceMark
from app.models.subscriptions import Subscription, SubscriptionStatusHistory
from app.operational_reports.periods import jordan_today

PAID_STATUSES = ("ACTIVE", "PAST_DUE", "SUSPENDED", "EXPIRED", "CANCELLED", "COMPLETED")
LIVE_STATUSES = ("ACTIVE", "PAST_DUE")


def _midnight_utc(day: date) -> datetime:
    return datetime.combine(day, datetime.min.time(), tzinfo=timezone.utc)


def _subscriptions_of(customer_id) -> list[Subscription]:
    return list(db_session.execute(select(Subscription).where(Subscription.customer_id == customer_id)).scalars())


def _trial_is_running(subscription: Subscription, today: date) -> bool:
    """A trial that has not started yet, or has not reached its last day."""
    if subscription.status != "PILOT":
        return False
    pilot = trial_pilot_for_subscription(subscription.id)
    if pilot is None or pilot.status in _DEAD_PILOT_STATUSES:
        return False
    return pilot.pilot_end is None or pilot.pilot_end >= today


def customer_is_live(customer_id, today: date | None = None) -> bool:
    today = today or jordan_today()
    return any(
        sub.status in LIVE_STATUSES or _trial_is_running(sub, today) for sub in _subscriptions_of(customer_id)
    )


def _suspended_on(subscription: Subscription) -> date | None:
    moment = db_session.execute(
        select(SubscriptionStatusHistory.created_at).where(
            SubscriptionStatusHistory.subscription_id == subscription.id, SubscriptionStatusHistory.to_status == "SUSPENDED"
        ).order_by(SubscriptionStatusHistory.created_at.desc())
    ).scalars().first()
    return moment.astimezone(timezone.utc).date() if moment is not None else None


def _ended_on(subscription: Subscription) -> date:
    if subscription.status == "SUSPENDED":
        found = _suspended_on(subscription)
        if found is not None:
            return found
    if subscription.status == "CANCELLED":
        return subscription.end_date or subscription.cancellation_date or subscription.updated_at.date()
    return subscription.end_date or subscription.updated_at.date()


def customer_state(customer_id, today: date | None = None) -> tuple[str, date | None]:
    """('NO_PAID_HISTORY', None) | ('LIVE', None) | ('ENDED', last paid-until day)."""
    today = today or jordan_today()
    subscriptions = _subscriptions_of(customer_id)
    paid = [s for s in subscriptions if s.status in PAID_STATUSES and not is_unconverted_trial(s.id)]
    if not paid:
        return "NO_PAID_HISTORY", None
    if any(s.status in LIVE_STATUSES or _trial_is_running(s, today) for s in subscriptions):
        return "LIVE", None
    return "ENDED", max(_ended_on(s) for s in paid)


def apply_post_customer_retention(*, today: date | None = None) -> dict:
    """Sets or clears purge_after on every unpurged ID / registration document of a customer
    with paid history (see the module docstring). Returns {"set": n, "cleared": n} in customers."""
    today = today or jordan_today()
    customer_ids = db_session.execute(
        select(CustomerDocument.customer_id).where(
            CustomerDocument.kind.in_(documents.IDENTITY_KINDS), CustomerDocument.purged_at.is_(None)
        ).distinct()
    ).scalars().all()
    changed = {"set": 0, "cleared": 0}
    audits = []
    for customer_id in customer_ids:
        state, ended_on = customer_state(customer_id, today)
        if state == "NO_PAID_HISTORY":
            continue
        target = None if state == "LIVE" else _midnight_utc(ended_on) + timedelta(days=POST_CUSTOMER_RETENTION_DAYS)
        rows = documents.documents_of(customer_id, kinds=documents.IDENTITY_KINDS)
        moved = [row for row in rows if row.purge_after != target]
        for row in moved:
            row.purge_after = target
        if moved:
            changed["cleared" if target is None else "set"] += 1
            audits.append((customer_id, target, len(moved)))
    db_session.commit()
    for customer_id, target, n in audits:
        audit_record(
            actor_staff_user_id=None, actor_role_snapshot=None,
            action_code="CUSTOMER_DOCUMENT_RETENTION_CLEARED" if target is None else "CUSTOMER_DOCUMENT_RETENTION_SET",
            entity_type="customer", entity_public_id=str(customer_id),
            after_state={"documents": n, "purge_after": target.isoformat() if target else None},
        )
    return changed


def unlink_trial_marks_of_purged_customers(*, today: date | None = None) -> int:
    """A customer whose ID / registration photos have been purged AND who is no longer a
    customer loses the LINK between them and their trial-device marks: the device
    fingerprint stays (fraud prevention, disclosed), the person goes. A customer who merely
    refused to let us keep the photos (documents purged, still a customer) keeps the link.
    Stateless and idempotent, so a crashed run is simply finished by the next one."""
    today = today or jordan_today()
    candidates = db_session.execute(
        select(CustomerDocument.customer_id).where(
            CustomerDocument.kind.in_(documents.IDENTITY_KINDS), CustomerDocument.purged_at.is_not(None)
        ).distinct()
    ).scalars().all()
    unlinked = 0
    for customer_id in candidates:
        if documents.documents_of(customer_id, kinds=documents.IDENTITY_KINDS) or customer_is_live(customer_id, today):
            continue
        result = db_session.execute(
            update(TrialDeviceMark).where(TrialDeviceMark.customer_id == customer_id).values(customer_id=None)
        )
        if result.rowcount:
            unlinked += result.rowcount
            audit_after = {"customer_id": str(customer_id), "marks_unlinked": result.rowcount}
            db_session.commit()
            audit_record(
                actor_staff_user_id=None, actor_role_snapshot=None, action_code="TRIAL_DEVICE_MARKS_UNLINKED",
                entity_type="customer", entity_public_id=str(customer_id), after_state=audit_after,
            )
    db_session.commit()
    return unlinked


def purge_due(*, now: datetime | None = None) -> dict:
    """The whole nightly job, in the one order that is correct: dates first, then deletion,
    then the unlinking that depends on what was deleted."""
    now = now or datetime.now(timezone.utc)
    from app.seller_id import retention as seller_retention

    retention = apply_post_customer_retention(today=jordan_today())
    seller_dates = seller_retention.apply_seller_photo_retention(now=now)
    return {
        "customer_retention_dates": retention,
        "seller_photo_retention_dates": seller_dates,
        "customer_documents_purged": documents.purge_expired_documents(now=now),
        "seller_photos_purged": seller_retention.purge_expired_seller_photos(now=now),
        "trial_marks_unlinked": unlink_trial_marks_of_purged_customers(today=jordan_today()),
    }
