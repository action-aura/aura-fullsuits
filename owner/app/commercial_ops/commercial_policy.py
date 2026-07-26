"""Commercial policy resolution and internal notification services (Phase 8
Parts G/H/I, Milestone 3).

See `app/models/commercial_ops.py`'s `CommercialPolicy`/`InternalNotification`
docstrings for what these are and why the commercial policy here is a
completely separate concept from the product-side TECHNICAL offline policy
(`owner_offline_policies`, Phase 6/7).
"""
from __future__ import annotations

from datetime import date

from sqlalchemy import select

from app.audit.services import record as audit_record
from app.extensions import db_session
from app.models.base import utcnow
from app.models.commercial_ops import NOTIFICATION_STATUSES, CommercialPolicy, InternalNotification
from app.models.subscriptions import Subscription

DEFAULT_POLICY_CODE = "default-commercial-policy-v1"


class CommercialPolicyError(ValueError):
    pass


class NotificationError(ValueError):
    pass


def resolve_policy_for_subscription(subscription: Subscription, *, as_of: date | None = None) -> CommercialPolicy | None:
    """Product-specific active policy first, falling back to the global
    default (`product_id IS NULL`). Returns None if neither exists --
    callers (expiry_scan.py) must treat that as "nothing to do for this
    subscription," never as license to guess a policy (deny-by-default)."""
    as_of = as_of or utcnow().date()
    stmt = (
        select(CommercialPolicy)
        .where(
            CommercialPolicy.product_id == subscription.product_id,
            CommercialPolicy.effective_date <= as_of,
        )
        .where((CommercialPolicy.retired_date.is_(None)) | (CommercialPolicy.retired_date > as_of))
        .order_by(CommercialPolicy.effective_date.desc())
    )
    specific = db_session.execute(stmt).scalars().first()
    if specific is not None:
        return specific

    default_stmt = (
        select(CommercialPolicy)
        .where(
            CommercialPolicy.product_id.is_(None),
            CommercialPolicy.effective_date <= as_of,
        )
        .where((CommercialPolicy.retired_date.is_(None)) | (CommercialPolicy.retired_date > as_of))
        .order_by(CommercialPolicy.effective_date.desc())
    )
    return db_session.execute(default_stmt).scalars().first()


def create_commercial_policy(
    *,
    policy_code: str,
    warning_offsets_days: list[int],
    notify_role_codes: list[str],
    effective_date: date,
    actor_staff_user_id,
    product_id=None,
    requires_customer_contact: bool = False,
    past_due_start_days: int = 0,
    payment_grace_days: int = 0,
    auto_expire_after_grace: bool = True,
    policy_version: int = 1,
) -> CommercialPolicy:
    if any(d < 0 for d in warning_offsets_days):
        raise CommercialPolicyError("warning_offsets_days must all be non-negative.")
    if sorted(warning_offsets_days, reverse=True) != list(warning_offsets_days):
        raise CommercialPolicyError("warning_offsets_days must be sorted descending, e.g. [30, 14, 7, 3, 1, 0].")
    if past_due_start_days < 0 or payment_grace_days < 0:
        raise CommercialPolicyError("past_due_start_days and payment_grace_days must be non-negative.")

    policy = CommercialPolicy(
        policy_code=policy_code,
        product_id=product_id,
        policy_version=policy_version,
        warning_offsets_days=list(warning_offsets_days),
        notify_role_codes=list(notify_role_codes),
        requires_customer_contact=requires_customer_contact,
        past_due_start_days=past_due_start_days,
        payment_grace_days=payment_grace_days,
        auto_expire_after_grace=auto_expire_after_grace,
        effective_date=effective_date,
        created_by_staff_user_id=actor_staff_user_id,
    )
    db_session.add(policy)
    db_session.commit()
    audit_record(
        actor_staff_user_id=actor_staff_user_id,
        actor_role_snapshot=None,
        action_code="COMMERCIAL_POLICY_CREATED",
        entity_type="commercial_policy",
        entity_public_id=str(policy.id),
        after_state={"policy_code": policy_code, "product_id": str(product_id) if product_id else None},
    )
    return policy


def create_notification(
    *,
    notification_type: str,
    severity: str,
    title: str,
    message: str,
    dedup_key: str,
    customer_id=None,
    subscription_id=None,
    license_id=None,
    installation_id=None,
    assigned_role_code: str | None = None,
    due_at=None,
    source_policy_code: str | None = None,
) -> tuple[InternalNotification, bool]:
    """Idempotent create: if a notification with this `dedup_key` already
    exists, returns it unchanged (created=False) rather than raising or
    duplicating. This is THE mechanism that makes repeated scheduler runs
    safe (Part H/R) -- the UNIQUE constraint on `dedup_key` is the actual
    guarantee; this function's existing-row check is just what makes a
    duplicate call return cleanly instead of hitting an IntegrityError."""
    existing = db_session.execute(
        select(InternalNotification).where(InternalNotification.dedup_key == dedup_key)
    ).scalars().first()
    if existing is not None:
        return existing, False

    notification = InternalNotification(
        notification_type=notification_type,
        severity=severity,
        title=title,
        message=message,
        dedup_key=dedup_key,
        customer_id=customer_id,
        subscription_id=subscription_id,
        license_id=license_id,
        installation_id=installation_id,
        assigned_role_code=assigned_role_code,
        due_at=due_at,
        source_policy_code=source_policy_code,
        status="OPEN",
    )
    db_session.add(notification)
    db_session.commit()
    return notification, True


def acknowledge_notification(notification: InternalNotification, actor_staff_user_id) -> None:
    if notification.status not in ("OPEN", "IN_PROGRESS"):
        raise NotificationError(f"Cannot acknowledge a notification in status {notification.status}.")
    notification.status = "ACKNOWLEDGED"
    notification.acknowledged_at = utcnow()
    notification.acknowledged_by_staff_user_id = actor_staff_user_id
    db_session.commit()
    audit_record(
        actor_staff_user_id=actor_staff_user_id,
        actor_role_snapshot=None,
        action_code="NOTIFICATION_ACKNOWLEDGED",
        entity_type="internal_notification",
        entity_public_id=str(notification.id),
        after_state={"status": "ACKNOWLEDGED"},
    )


def resolve_notification(notification: InternalNotification, actor_staff_user_id, resolution: str) -> None:
    if notification.status in ("RESOLVED", "DISMISSED"):
        raise NotificationError(f"Notification already {notification.status}.")
    notification.status = "RESOLVED"
    notification.resolved_at = utcnow()
    notification.resolved_by_staff_user_id = actor_staff_user_id
    notification.resolution = resolution
    db_session.commit()
    audit_record(
        actor_staff_user_id=actor_staff_user_id,
        actor_role_snapshot=None,
        action_code="NOTIFICATION_RESOLVED",
        entity_type="internal_notification",
        entity_public_id=str(notification.id),
        after_state={"status": "RESOLVED"},
        reason=resolution,
    )


def assign_notification(notification: InternalNotification, actor_staff_user_id, assignee_staff_user_id) -> None:
    notification.assigned_staff_user_id = assignee_staff_user_id
    if notification.status == "OPEN":
        notification.status = "IN_PROGRESS"
    db_session.commit()
    audit_record(
        actor_staff_user_id=actor_staff_user_id,
        actor_role_snapshot=None,
        action_code="NOTIFICATION_ASSIGNED",
        entity_type="internal_notification",
        entity_public_id=str(notification.id),
        after_state={"assigned_staff_user_id": str(assignee_staff_user_id)},
    )
