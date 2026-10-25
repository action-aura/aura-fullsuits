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
applied_at makes each stop happen once.

Step 0 fix round (2026-10-25): two more cases the refund had no consequence
for (verifier probe v9):
  - a PARTIAL refund of PACKAGE money (250 of a 300 Package 1 + pair) leaves
    the package no longer fully paid -- the ladder already stopped counting
    the sale -- so it schedules the same stop;
  - a refund of ONLY the extra-device (pair) money keeps the licence and
    takes those devices away (License.device_limit and the subscription's
    device_allowance -- what activation and the product's assertion read)
    after the same 2 days: a row with devices_removed and no to_status.
Lock order: the stop row, then the
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


def schedule_device_removal_after_refund(refund, subscription: Subscription, *, devices: int,
                                        actor_staff_user_id) -> list[LicenseScheduledStop]:
    """Flush only, like schedule_stops_after_refund: `devices` taken off each
    live licence of the subscription at the refund's paid_at + 2 days."""
    if devices <= 0:
        return []
    effective_at = refund.paid_at.astimezone(timezone.utc) + STOP_AFTER_REFUND
    licences = db_session.execute(
        select(License).where(License.subscription_id == subscription.id,
                              License.status.in_(_STOPPABLE_LICENCE_STATUSES))
    ).scalars().all()
    rows = []
    for licence in licences:
        existing = db_session.execute(
            select(LicenseScheduledStop).where(
                LicenseScheduledStop.source_refund_id == refund.id, LicenseScheduledStop.license_id == licence.id
            )
        ).scalars().first()
        if existing is not None:
            continue
        row = LicenseScheduledStop(
            license_id=licence.id, subscription_id=subscription.id, effective_at=effective_at, to_status=None,
            devices_removed=devices,
            reason=f"Refund {refund.id} gave back the extra-device money; {devices} devices are removed 2 days later.",
            source_refund_id=refund.id, created_by_staff_user_id=actor_staff_user_id,
        )
        db_session.add(row)
        rows.append(row)
    db_session.flush()
    return rows


def devices_already_scheduled_off(subscription_id: uuid.UUID, refund_ids: list) -> int:
    """Devices this subscription's licences already lose (or lost) for these
    refunds -- per licence, so the largest of them."""
    if not refund_ids:
        return 0
    rows = db_session.execute(
        select(LicenseScheduledStop.license_id, LicenseScheduledStop.devices_removed).where(
            LicenseScheduledStop.subscription_id == subscription_id,
            LicenseScheduledStop.source_refund_id.in_(refund_ids),
            LicenseScheduledStop.devices_removed.is_not(None),
            LicenseScheduledStop.cancelled_at.is_(None),
        )
    ).all()
    per_licence: dict = {}
    for license_id, devices in rows:
        per_licence[license_id] = per_licence.get(license_id, 0) + devices
    return max(per_licence.values(), default=0)


def pending_stop_for_subscription(subscription_id: uuid.UUID) -> LicenseScheduledStop | None:
    """The earliest stop or device removal not yet applied for any licence
    of this subscription (for its screen)."""
    return db_session.execute(
        select(LicenseScheduledStop)
        .where(LicenseScheduledStop.subscription_id == subscription_id, LicenseScheduledStop.applied_at.is_(None),
               LicenseScheduledStop.cancelled_at.is_(None))
        .order_by(LicenseScheduledStop.effective_at)
    ).scalars().first()


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
            if stop.devices_removed:
                _remove_devices(stop, licence, subscription, changes)
                stop.applied_at = now
                continue
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
        if entity_type.endswith("_devices"):
            audit_record(
                actor_staff_user_id=actor_staff_user_id, actor_role_snapshot=None,
                action_code="LICENSE_DEVICES_REMOVED_AFTER_REFUND", entity_type=entity_type.removesuffix("_devices"),
                entity_public_id=str(entity_id), before_state={"devices": from_status[0]},
                after_state={"devices": from_status[1]}, reason=stop.reason,
            )
            continue
        audit_record(
            actor_staff_user_id=actor_staff_user_id, actor_role_snapshot=None,
            action_code="LICENSE_STATUS_CHANGED" if entity_type == "license" else "SUBSCRIPTION_STATUS_CHANGED",
            entity_type=entity_type, entity_public_id=str(entity_id),
            before_state={"status": from_status}, after_state={"status": stop.to_status},
            reason=stop.reason,
        )
    for stop in stops:
        if stop.devices_removed:
            continue  # audited above, per licence/subscription it changed
        audit_record(
            actor_staff_user_id=actor_staff_user_id, actor_role_snapshot=None, action_code="LICENSE_SCHEDULED_STOP_APPLIED",
            entity_type="license", entity_public_id=str(stop.license_id),
            after_state={"stop_id": str(stop.id), "effective_at": stop.effective_at.isoformat(),
                         "source_refund_id": str(stop.source_refund_id)},
        )
    return len(stops)


def _remove_devices(stop: LicenseScheduledStop, licence: License, subscription: Subscription | None, changes: list) -> None:
    """A pair-only refund's removal: the licence's device_limit (what
    activation enforces and the product's assertion carries) and the
    subscription's device_allowance each lose the refunded devices, never
    below 1. Only a live licence; a stopped or replaced one is left as it is."""
    if licence.status in _STOPPABLE_LICENCE_STATUSES and licence.device_limit is not None:
        after = max(1, licence.device_limit - stop.devices_removed)
        if after != licence.device_limit:
            changes.append(("license_devices", licence.id, (licence.device_limit, after), stop))
            licence.device_limit = after
    if subscription is not None and subscription.device_allowance is not None:
        after = max(1, subscription.device_allowance - stop.devices_removed)
        if after != subscription.device_allowance:
            changes.append(("subscription_devices", subscription.id, (subscription.device_allowance, after), stop))
            subscription.device_allowance = after
