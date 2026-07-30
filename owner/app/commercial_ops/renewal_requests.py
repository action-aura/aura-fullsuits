"""Renewal request workflow engine (Phase 8 Parts C/E/K, Milestone 2).

State machine for the request/quote/confirm/pay/approve/apply pipeline in
front of the existing `RenewalRecord` "apply" audit row (see
owner/app/subscriptions/services.py's `record_renewal()`, which
`apply_renewal_request()` below does NOT call -- it inlines the equivalent
logic so the whole apply operation is exactly one transaction, committed
once, per spec Part E step 22).

Simple status moves (DRAFT->QUOTED, ...->REJECTED/CANCELLED/VOIDED) go
through the generic `transition_renewal_request()`, mirroring the
established pattern in `transition_subscription()`/`transition_license()`.
`approve_renewal_request()` and `apply_renewal_request()` are their own
functions because each does more than a single-column status change.

No license-key re-entry anywhere in this module (Part K): applying a
renewal only ever touches Subscription/RenewalRequest/RenewalRecord rows.
The existing installation keeps checking in against its already-issued
license exactly as before; the NEXT check-in after this transaction commits
sees the renewed term because `checkin.py` always resolves entitlements and
builds the assertion payload fresh from current database state on every
call (never cached) -- there is no separate "mark for refresh" step needed
at the Owner-database level for this to work correctly. (A distinct,
later concern -- telling the product to check in *sooner* than its normal
interval, a UX/urgency signal via the assertion's
`assertion_refresh_required` field -- is Milestone 7/Part W's job, not
this one's.)
"""
from __future__ import annotations

from datetime import date

from sqlalchemy import select

from app.audit.services import record as audit_record
from app.extensions import db_session
from app.models.base import utcnow
from app.models.commercial_ops import RenewalRequest, RenewalRequestStatusHistory
from app.models.licensing import License
from app.models.subscriptions import RenewalRecord, Subscription, SubscriptionStatusHistory

VALID_TRANSITIONS: dict[str, set[str]] = {
    "DRAFT": {"QUOTED", "CANCELLED"},
    "QUOTED": {"AWAITING_CONFIRMATION", "CANCELLED"},
    "AWAITING_CONFIRMATION": {"AWAITING_PAYMENT", "REJECTED", "CANCELLED"},
    "AWAITING_PAYMENT": {"PAYMENT_RECORDED", "CANCELLED", "VOIDED"},
    "PAYMENT_RECORDED": {"APPROVED", "REJECTED", "CANCELLED"},
    "APPROVED": {"APPLIED", "CANCELLED", "VOIDED"},
    "APPLIED": set(),
    "REJECTED": set(),
    "CANCELLED": set(),
    "VOIDED": set(),
}

# Subscription statuses a renewal is allowed to revive back to ACTIVE. The
# sole authority for this decision -- deliberately NOT cross-checked
# against subscriptions.services.VALID_TRANSITIONS (see that table's own
# comment on its "EXPIRED" entry for why: that table is also consulted by
# the generic, loosely-gated POST /subscriptions/<id>/transition route, so
# widening it to permit a revival would open the same move up outside this
# module's own approval/payment/recent-auth-gated pipeline).
_REVIVABLE_SUBSCRIPTION_STATUSES = frozenset({"EXPIRED", "PAST_DUE", "SUSPENDED"})

# Milestone 4, Part M: a PILOT subscription "graduating" to a paid ACTIVE
# term via an applied renewal -- conceptually different from reviving a
# lapsed/blocked subscription (a pilot was never inactive), but the same
# apply-time bookkeeping. PILOT -> ACTIVE was already permitted in the
# shared subscriptions.services.VALID_TRANSITIONS table before Phase 8 (not
# something this module widens), so this constant exists purely for
# clarity of intent, not as a security boundary the way
# _REVIVABLE_SUBSCRIPTION_STATUSES is.
_GRADUATING_SUBSCRIPTION_STATUSES = frozenset({"PILOT"})


class InvalidRenewalTransitionError(ValueError):
    pass


class RenewalApplicationError(ValueError):
    pass


class RenewalConcurrencyError(RenewalApplicationError):
    """A concurrent modification was detected -- another renewal (or other
    commercial change) already moved this subscription's term underneath
    this request (Part E: "two simultaneous renewal approvals cannot
    double-extend the subscription")."""


def create_renewal_request(
    *,
    subscription: Subscription,
    date_rule: str,
    proposed_term_start: date,
    proposed_term_end: date,
    currency: str,
    actor_staff_user_id,
    requested_plan_id=None,
    billing_interval: str | None = None,
    current_plan_price_id=None,
    proposed_plan_price_id=None,
    commercial_amount=None,
    adjustment_amount=None,
    device_allowance_after: int | None = None,
    sales_owner_staff_user_id=None,
    reason: str | None = None,
    notes: str | None = None,
    idempotency_key: str | None = None,
) -> RenewalRequest:
    """Snapshots the subscription's CURRENT term/plan/device-allowance at
    request-creation time (Part C: the renewal record must preserve
    "current term start/end", not just the proposed values) -- this
    snapshot is what `apply_renewal_request()` later rechecks against the
    subscription's live state to detect a stale request."""
    if idempotency_key:
        existing = db_session.execute(
            select(RenewalRequest).where(RenewalRequest.idempotency_key == idempotency_key)
        ).scalars().first()
        if existing is not None:
            return existing

    renewal = RenewalRequest(
        subscription_id=subscription.id,
        customer_id=subscription.customer_id,
        product_id=subscription.product_id,
        current_plan_id=subscription.plan_id,
        requested_plan_id=requested_plan_id,
        current_term_start=subscription.start_date,
        current_term_end=subscription.end_date,
        proposed_term_start=proposed_term_start,
        proposed_term_end=proposed_term_end,
        date_rule=date_rule,
        billing_interval=billing_interval,
        currency=currency,
        current_plan_price_id=current_plan_price_id,
        proposed_plan_price_id=proposed_plan_price_id,
        commercial_amount=commercial_amount,
        adjustment_amount=adjustment_amount,
        device_allowance_before=subscription.device_allowance,
        device_allowance_after=device_allowance_after,
        sales_owner_staff_user_id=sales_owner_staff_user_id,
        created_by_staff_user_id=actor_staff_user_id,
        reason=reason,
        notes=notes,
        status="DRAFT",
        idempotency_key=idempotency_key,
    )
    db_session.add(renewal)
    db_session.commit()
    audit_record(
        actor_staff_user_id=actor_staff_user_id,
        actor_role_snapshot=None,
        action_code="RENEWAL_REQUEST_CREATED",
        entity_type="renewal_request",
        entity_public_id=str(renewal.id),
        after_state={"status": "DRAFT", "subscription_id": str(subscription.id)},
    )
    return renewal


def transition_renewal_request(
    renewal: RenewalRequest, to_status: str, actor_staff_user_id, reason: str | None = None
) -> None:
    """Generic status move for every transition except APPROVE and APPLY,
    which each need to set additional fields atomically -- see
    `approve_renewal_request()` / `apply_renewal_request()`."""
    allowed = VALID_TRANSITIONS.get(renewal.status, set())
    if to_status not in allowed:
        raise InvalidRenewalTransitionError(
            f"Cannot transition renewal request from {renewal.status} to {to_status}."
        )
    if to_status == "APPROVED":
        raise InvalidRenewalTransitionError("Use approve_renewal_request() to reach APPROVED.")
    from_status = renewal.status
    renewal.status = to_status
    if to_status == "CANCELLED":
        renewal.cancelled_at = utcnow()
    db_session.add(
        RenewalRequestStatusHistory(
            renewal_request_id=renewal.id,
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
        action_code="RENEWAL_REQUEST_STATUS_CHANGED",
        entity_type="renewal_request",
        entity_public_id=str(renewal.id),
        before_state={"status": from_status},
        after_state={"status": to_status},
        reason=reason,
    )


def approve_renewal_request(renewal: RenewalRequest, actor_staff_user_id, *, reason: str | None = None) -> None:
    """Part Y: "no renewal can be self-approved where separation is
    required" -- enforced unconditionally here: whoever created a renewal
    request may never also be the one who approves it."""
    if renewal.created_by_staff_user_id is not None and str(renewal.created_by_staff_user_id) == str(
        actor_staff_user_id
    ):
        raise RenewalApplicationError("SELF_APPROVAL_NOT_ALLOWED")
    allowed = VALID_TRANSITIONS.get(renewal.status, set())
    if "APPROVED" not in allowed:
        raise InvalidRenewalTransitionError(f"Cannot approve a renewal request in status {renewal.status}.")

    from_status = renewal.status
    renewal.status = "APPROVED"
    renewal.approved_by_staff_user_id = actor_staff_user_id
    renewal.approved_at = utcnow()
    db_session.add(
        RenewalRequestStatusHistory(
            renewal_request_id=renewal.id,
            from_status=from_status,
            to_status="APPROVED",
            changed_by_staff_user_id=actor_staff_user_id,
            reason=reason,
        )
    )
    db_session.commit()
    audit_record(
        actor_staff_user_id=actor_staff_user_id,
        actor_role_snapshot=None,
        action_code="RENEWAL_REQUEST_APPROVED",
        entity_type="renewal_request",
        entity_public_id=str(renewal.id),
        before_state={"status": from_status},
        after_state={"status": "APPROVED"},
        reason=reason,
    )


def apply_renewal_request(renewal_request_id, actor_staff_user_id) -> RenewalRequest:
    """The atomic renewal-application transaction (spec Part E, adapted to
    this codebase's actual domain model). Everything below happens in ONE
    transaction, committed exactly once at the end; any exception rolls
    back the whole operation via the caller's normal session-rollback
    handling.

    Concurrency safety has two independent layers:

    1. `SELECT ... FOR UPDATE` on both the RenewalRequest and Subscription
       rows -- a second concurrent `apply_renewal_request()` call for the
       SAME renewal request blocks on the row lock until the first
       transaction commits or rolls back, then re-reads the now-committed
       row and fails the status check (no longer APPROVED).
    2. A re-check that `renewal.current_term_end` still equals
       `subscription.end_date` -- catches a DIFFERENT renewal request
       (for the same subscription) having already applied and moved the
       term since this one was created, which the row lock alone would not
       catch (different RenewalRequest primary key, no lock conflict
       between them).
    """
    renewal = db_session.execute(
        select(RenewalRequest).where(RenewalRequest.id == renewal_request_id).with_for_update()
    ).scalars().first()
    if renewal is None:
        raise RenewalApplicationError("RENEWAL_REQUEST_NOT_FOUND")
    if renewal.status != "APPROVED":
        raise InvalidRenewalTransitionError(
            f"Cannot apply a renewal request in status {renewal.status}; must be APPROVED."
        )

    subscription = db_session.execute(
        select(Subscription).where(Subscription.id == renewal.subscription_id).with_for_update()
    ).scalars().first()
    if subscription is None:
        raise RenewalApplicationError("SUBSCRIPTION_NOT_FOUND")

    if renewal.current_term_end != subscription.end_date:
        raise RenewalConcurrencyError(
            "SUBSCRIPTION_TERM_CHANGED_SINCE_REQUEST: another renewal or commercial change has already "
            "moved this subscription's term since this request was created. Reject or recreate this "
            "request against the subscription's current term before applying."
        )

    previous_end_date = subscription.end_date
    previous_plan_id = subscription.plan_id
    previous_subscription_status = subscription.status
    device_limit_changes: list[tuple] = []

    subscription.end_date = renewal.proposed_term_end
    if subscription.start_date is None:
        subscription.start_date = renewal.proposed_term_start
    subscription.renewal_date = renewal.proposed_term_end
    if renewal.requested_plan_id:
        subscription.plan_id = renewal.requested_plan_id
    if renewal.device_allowance_after is not None:
        subscription.device_allowance = renewal.device_allowance_after
        # Phase 8V-P2: found by the Phase 8V-P physical validation session
        # (Scenario 7). `License.device_limit` -- the field every real
        # device-limit check actually enforces (activation.py,
        # device_slot_ops.resolve_effective_device_limit()) -- must mirror
        # the subscription's new commercial device allowance the moment a
        # renewal is applied, exactly like plan_id and end_date already do
        # above. This deliberately does NOT touch any Installation row: an
        # existing active device is never silently kicked off by a lowered
        # allowance -- new-activation blocking and overage remediation are
        # `scan_over_limit_licenses()`'s job (device_slot_ops.py), run
        # separately and already correct. Locked with FOR UPDATE, same as
        # activation.py's own License lock, so a concurrent activation
        # attempt against this exact license serializes against this write
        # rather than racing it.
        # audit_record() commits on its own (Part T) -- it must NOT be
        # called yet, this whole function commits exactly once, below. Just
        # collect what changed here; the audit rows are written after that
        # single commit, alongside the existing RENEWAL_APPLIED entry.
        licenses_for_subscription = db_session.execute(
            select(License).where(License.subscription_id == subscription.id).with_for_update()
        ).scalars().all()
        for license_row in licenses_for_subscription:
            if license_row.device_limit == renewal.device_allowance_after:
                continue
            device_limit_changes.append((license_row.id, license_row.device_limit, renewal.device_allowance_after))
            license_row.device_limit = renewal.device_allowance_after

    if previous_subscription_status in _REVIVABLE_SUBSCRIPTION_STATUSES | _GRADUATING_SUBSCRIPTION_STATUSES:
        # Deliberately NOT consulted against subscriptions.services.
        # VALID_TRANSITIONS here (security fix -- see that table's own
        # comment on its "EXPIRED" entry): this module's own
        # _REVIVABLE_SUBSCRIPTION_STATUSES allowlist is the sole authority
        # for which prior states a renewal may revive, precisely so that
        # widening the SHARED table (consulted by the loosely-gated generic
        # transition route) can never accidentally open this path up
        # outside the renewal-approval pipeline. _GRADUATING_SUBSCRIPTION_
        # STATUSES (PILOT) is included here too since a pilot conversion
        # needs the exact same "flip to ACTIVE + write history" bookkeeping
        # as a revival, even though PILOT -> ACTIVE was never restricted.
        db_session.add(
            SubscriptionStatusHistory(
                subscription_id=subscription.id,
                from_status=previous_subscription_status,
                to_status="ACTIVE",
                changed_by_staff_user_id=actor_staff_user_id,
                reason=f"Renewal request {renewal.id} applied.",
            )
        )
        subscription.status = "ACTIVE"

    # Preserve previous-term history. Inlined rather than calling
    # record_renewal() (owner/app/subscriptions/services.py), which commits
    # on its own -- this transaction must commit exactly once (Part E step
    # 22).
    renewal_record = RenewalRecord(
        subscription_id=subscription.id,
        previous_end_date=previous_end_date,
        new_end_date=renewal.proposed_term_end,
        previous_plan_id=previous_plan_id,
        new_plan_id=renewal.requested_plan_id or previous_plan_id,
        reason=renewal.reason,
        approved_by_staff_user_id=renewal.approved_by_staff_user_id,
        related_payment_record_id=renewal.related_payment_record_id,
    )
    db_session.add(renewal_record)
    db_session.flush()  # need renewal_record.id before linking it below

    renewal.applied_renewal_record_id = renewal_record.id
    renewal.applied_by_staff_user_id = actor_staff_user_id
    renewal.applied_at = utcnow()
    renewal.status = "APPLIED"
    db_session.add(
        RenewalRequestStatusHistory(
            renewal_request_id=renewal.id,
            from_status="APPROVED",
            to_status="APPLIED",
            changed_by_staff_user_id=actor_staff_user_id,
            reason="Renewal applied.",
        )
    )

    db_session.commit()

    audit_record(
        actor_staff_user_id=actor_staff_user_id,
        actor_role_snapshot=None,
        action_code="RENEWAL_APPLIED",
        entity_type="renewal_request",
        entity_public_id=str(renewal.id),
        before_state={
            "end_date": str(previous_end_date),
            "plan_id": str(previous_plan_id),
            "subscription_status": previous_subscription_status,
        },
        after_state={
            "end_date": str(renewal.proposed_term_end),
            "plan_id": str(subscription.plan_id),
            "subscription_status": subscription.status,
        },
    )
    for license_id, previous_device_limit, new_device_limit in device_limit_changes:
        audit_record(
            actor_staff_user_id=actor_staff_user_id,
            actor_role_snapshot=None,
            action_code="LICENSE_DEVICE_LIMIT_SYNCED",
            entity_type="license",
            entity_public_id=str(license_id),
            before_state={"device_limit": previous_device_limit},
            after_state={"device_limit": new_device_limit},
            reason=f"Renewal request {renewal.id} applied.",
        )
    return renewal
