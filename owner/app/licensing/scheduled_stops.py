"""Step 0 (2026-10-24) -- "a refunded licence keeps working 2 days, then
stops" (the owner's rule), as a stored moment the licence checks honour.

Before this, a full refund of a fulfilled sale suspended the SUBSCRIPTION at
the moment the refund was confirmed (refunds.py, measured on 1025d626:
'SUSPENDED' right after the refund) and left the licence ISSUED -- the
opposite of the rule on both counts: the shop was cut off at once, and the
licence itself (what the product's check-in actually reads) never stopped.

Now confirm_refund writes a LicenseScheduledStop (effective_at = the
refund's paid_at + 2 days) in the refund's own transaction, and
apply_due_licence_stops() moves the licence -- and its subscription -- to
SUSPENDED once that moment has passed. SUSPENDED, not REVOKED: it is the
existing state machine's reversible stop (licensing/services.py: SUSPENDED
-> ACTIVE by licenses.reactivate), the same choice the refund policy
already made (entitlement_consequence.py: "never selects REVOKE
automatically"), and the product already goes RESTRICTED on it
(commercial_runtime/licensing_contracts/policy_evaluator.py) and check-in
already refuses it (LICENSE_SUSPENDED).

Who applies it, so nothing depends on a timer:
  - the product's check-in and activation for THAT licence, before they read
    its status (licensing_service/checkin.py, activation.py) -- the shop
    learns at its first check-in after the moment even if no job ran;
  - `flask commercial apply-licence-stops --apply` for the rest (the Owner
    screens' view of the licence), safe to run as often as wanted.
applied_at makes each stop happen once. Lock order: the stop row, then the
subscription row, then the licence row -- the same subscription-before-
licence order commercial_ops/renewal_requests.py::apply_renewal_request
takes, so the two can never deadlock.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.audit.services import record as audit_record
from app.extensions import db_session
from app.models.base import utcnow
from app.models.licensing import License, LicenseScheduledStop, LicenseStatusHistory
from app.models.subscriptions import Subscription, SubscriptionStatusHistory

STOP_AFTER_REFUND = timedelta(days=2)
STOP_STATUS = "SUSPENDED"
_STOPPABLE_LICENCE_STATUSES = ("ISSUED", "ACTIVE")
_STOPPABLE_SUBSCRIPTION_STATUSES = ("ACTIVE", "PAST_DUE")


def schedule_stops_after_refund(refund, subscription: Subscription, *, actor_staff_user_id) -> list[LicenseScheduledStop]:
    """Flush only -- confirm_refund commits it with the refund. One stop per
    live licence of the subscription; the (refund, licence) unique constraint
    makes a retry a no-op."""
    # UTC first: paid_at read back from Postgres carries the session zone and
    # adding days to a zone-aware value is wall-clock arithmetic (DST).
    effective_at = refund.paid_at.astimezone(timezone.utc) + STOP_AFTER_REFUND
    licences = db_session.execute(
        select(License).where(License.subscription_id == subscription.id,
                              License.status.in_(_STOPPABLE_LICENCE_STATUSES))
    ).scalars().all()
    stops = []
    for licence in licences:
        existing = db_session.execute(
            select(LicenseScheduledStop).where(
                LicenseScheduledStop.source_refund_id == refund.id, LicenseScheduledStop.license_id == licence.id
            )
        ).scalars().first()
        if existing is not None:
            continue
        stop = LicenseScheduledStop(
            license_id=licence.id, subscription_id=subscription.id, effective_at=effective_at, to_status=STOP_STATUS,
            reason=f"Full refund {refund.id} confirmed; the licence keeps working 2 days, then stops.",
            source_refund_id=refund.id, created_by_staff_user_id=actor_staff_user_id,
        )
        db_session.add(stop)
        stops.append(stop)
    db_session.flush()
    return stops


def pending_stop_for(license_id: uuid.UUID) -> LicenseScheduledStop | None:
    """The earliest stop not yet applied for this licence (for screens)."""
    return db_session.execute(
        select(LicenseScheduledStop)
        .where(LicenseScheduledStop.license_id == license_id, LicenseScheduledStop.applied_at.is_(None),
               LicenseScheduledStop.cancelled_at.is_(None))
        .order_by(LicenseScheduledStop.effective_at)
    ).scalars().first()


def count_due_licence_stops(*, now: datetime | None = None) -> int:
    now = now or utcnow()
    return len(db_session.execute(
        select(LicenseScheduledStop.id).where(
            LicenseScheduledStop.applied_at.is_(None), LicenseScheduledStop.cancelled_at.is_(None),
            LicenseScheduledStop.effective_at <= now,
        )
    ).all())


def apply_due_licence_stops(*, now: datetime | None = None, license_id: uuid.UUID | None = None,
                            actor_staff_user_id=None) -> int:
    """Apply every stop whose moment has passed (only this licence's, when
    given). Commits when it applied something; returns how many it applied.
    A stop whose licence is already stopped (or was replaced) is marked
    applied without touching it."""
    now = now or utcnow()
    stmt = select(LicenseScheduledStop).where(
        LicenseScheduledStop.applied_at.is_(None), LicenseScheduledStop.cancelled_at.is_(None),
        LicenseScheduledStop.effective_at <= now,
    )
    if license_id is not None:
        stmt = stmt.where(LicenseScheduledStop.license_id == license_id)
    stops = db_session.execute(
        stmt.order_by(LicenseScheduledStop.effective_at, LicenseScheduledStop.id).with_for_update()
        .execution_options(populate_existing=True)
    ).scalars().all()
    if not stops:
        return 0
    changes = []
    try:
        for stop in stops:
            subscription = None
            if stop.subscription_id is not None:
                subscription = db_session.execute(
                    select(Subscription).where(Subscription.id == stop.subscription_id).with_for_update()
                    .execution_options(populate_existing=True)
                ).scalars().one()
            licence = db_session.execute(
                select(License).where(License.id == stop.license_id).with_for_update()
                .execution_options(populate_existing=True)
            ).scalars().one()
            if licence.status in _STOPPABLE_LICENCE_STATUSES:
                changes.append(("license", licence.id, licence.status, stop))
                db_session.add(LicenseStatusHistory(
                    license_id=licence.id, from_status=licence.status, to_status=stop.to_status,
                    changed_by_staff_user_id=actor_staff_user_id, reason=stop.reason,
                ))
                licence.status = stop.to_status
            if subscription is not None and subscription.status in _STOPPABLE_SUBSCRIPTION_STATUSES:
                changes.append(("subscription", subscription.id, subscription.status, stop))
                db_session.add(SubscriptionStatusHistory(
                    subscription_id=subscription.id, from_status=subscription.status, to_status=stop.to_status,
                    changed_by_staff_user_id=actor_staff_user_id, reason=stop.reason,
                ))
                subscription.status = stop.to_status
            stop.applied_at = now
        db_session.commit()
    except BaseException:
        db_session.rollback()
        raise
    for entity_type, entity_id, from_status, stop in changes:
        audit_record(
            actor_staff_user_id=actor_staff_user_id, actor_role_snapshot=None,
            action_code="LICENSE_STATUS_CHANGED" if entity_type == "license" else "SUBSCRIPTION_STATUS_CHANGED",
            entity_type=entity_type, entity_public_id=str(entity_id),
            before_state={"status": from_status}, after_state={"status": stop.to_status},
            reason=stop.reason,
        )
    for stop in stops:
        audit_record(
            actor_staff_user_id=actor_staff_user_id, actor_role_snapshot=None, action_code="LICENSE_SCHEDULED_STOP_APPLIED",
            entity_type="license", entity_public_id=str(stop.license_id),
            after_state={"stop_id": str(stop.id), "effective_at": stop.effective_at.isoformat(),
                         "source_refund_id": str(stop.source_refund_id)},
        )
    return len(stops)
