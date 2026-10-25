"""A staff member's devices: turn notifications on, off, and remove them.

A subscription is the browser's PushSubscription (endpoint + two keys). One
device has one endpoint per server key, so the endpoint is the identity:
subscribing again from the same phone updates that row instead of adding a
second one, and if the phone is now used by somebody else (logged out, the
next person logged in and turned notifications on), the row moves to them and
anything still queued for the previous person on it is cancelled -- the next
person must not receive the previous person's notifications.

At most MAX_DEVICES_PER_STAFF devices per person: a new one beyond that
removes that person's least recently refreshed device, so a page that keeps
re-subscribing (or a script) cannot grow the table without bound.

Removing a device first finishes its QUEUED deliveries as CANCELLED, then
deletes the row (its finished deliveries keep their history, device FK SET
NULL). The table refuses a QUEUED delivery without a device
(ck_owner_push_deliveries_queued_has_device), so a removal path that forgot
the first step fails loudly instead of leaving orphans.
"""
from __future__ import annotations

import uuid

from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError

from app.extensions import db_session
from app.models.base import utcnow
from app.models.push import PushDelivery, PushSubscription
from app.push.webpush import validate_endpoint, validate_keys

MAX_DEVICES_PER_STAFF = 10
_USER_AGENT_MAX = 255


def cancel_queued_for_devices(subscription_ids, reason: str) -> int:
    ids = list(subscription_ids)
    if not ids:
        return 0
    result = db_session.execute(
        update(PushDelivery)
        .where(PushDelivery.subscription_id.in_(ids), PushDelivery.status == "QUEUED")
        .values(status="CANCELLED", last_error=reason, finished_at=utcnow())
    )
    return result.rowcount or 0


def _remove_devices(subscription_ids, reason: str) -> int:
    ids = list(subscription_ids)
    if not ids:
        return 0
    cancel_queued_for_devices(ids, reason)
    result = db_session.execute(delete(PushSubscription).where(PushSubscription.id.in_(ids)))
    return result.rowcount or 0


def save_subscription(*, staff_user_id: uuid.UUID, endpoint: str, p256dh: str, auth: str,
                      locale: str | None, user_agent: str | None) -> tuple[PushSubscription, bool]:
    """Create or refresh this device's row for this person. Commits. Returns
    (row, is_new_for_this_person) -- False for the routine refresh the page
    does on every visit, so only a real change is audited.
    Raises webpush.InvalidSubscription for anything this server will not send to."""
    endpoint = validate_endpoint(endpoint)
    p256dh, auth = validate_keys(p256dh, auth)
    user_agent = (user_agent or "")[:_USER_AGENT_MAX] or None
    for attempt in (1, 2):
        try:
            row = db_session.execute(
                select(PushSubscription).where(PushSubscription.endpoint == endpoint).with_for_update()
            ).scalars().first()
            is_new = row is None or row.staff_user_id != staff_user_id
            if row is None:
                row = PushSubscription(staff_user_id=staff_user_id, endpoint=endpoint)
                db_session.add(row)
            elif row.staff_user_id != staff_user_id:
                cancel_queued_for_devices([row.id], "DEVICE_CHANGED_HANDS")
                row.staff_user_id = staff_user_id
            row.p256dh, row.auth, row.locale, row.user_agent = p256dh, auth, locale, user_agent
            row.updated_at = utcnow()
            db_session.flush()
            _enforce_device_cap(staff_user_id)
            db_session.commit()
            return row, is_new
        except IntegrityError:
            # Two requests from the same device raced to insert it: the other
            # one won, so this one becomes an update.
            db_session.rollback()
            if attempt == 2:
                raise
    raise AssertionError("unreachable")


def _enforce_device_cap(staff_user_id) -> None:
    ids = db_session.execute(
        select(PushSubscription.id)
        .where(PushSubscription.staff_user_id == staff_user_id)
        .order_by(PushSubscription.updated_at.desc(), PushSubscription.id)
    ).scalars().all()
    _remove_devices(ids[MAX_DEVICES_PER_STAFF:], "DEVICE_LIMIT")


def remove_subscription(*, staff_user_id: uuid.UUID, endpoint: str) -> bool:
    """Turn notifications off on this device (only the caller's own row). Commits."""
    row_id = db_session.execute(
        select(PushSubscription.id).where(
            PushSubscription.endpoint == (endpoint or ""), PushSubscription.staff_user_id == staff_user_id
        )
    ).scalar_one_or_none()
    if row_id is None:
        return False
    _remove_devices([row_id], "UNSUBSCRIBED")
    db_session.commit()
    return True


def remove_device(subscription_id, reason: str) -> None:
    """The push service said this device is gone (404/410). Does not commit."""
    _remove_devices([subscription_id], reason)


def remove_all_for_staff(staff_user_id: uuid.UUID) -> int:
    """Every device of this person, removed. Does NOT commit: called inside the
    deactivation's own transaction (staff/services.py::disable_staff,
    employees/services.py suspend/terminate), so the account is never left
    disabled with its phones still subscribed, or the other way round."""
    ids = db_session.execute(
        select(PushSubscription.id).where(PushSubscription.staff_user_id == staff_user_id)
    ).scalars().all()
    return _remove_devices(ids, "STAFF_DEACTIVATED")


def count_devices(staff_user_id: uuid.UUID) -> int:
    return len(db_session.execute(
        select(PushSubscription.id).where(PushSubscription.staff_user_id == staff_user_id)
    ).scalars().all())
