"""Expiry/past-due scan job (Phase 8 Parts G/I/J/R, Milestone 3).

Report-only by default (Part Q's general rule, applied here too): callers
must explicitly pass `dry_run=False` to write anything. Dry-run mode still
computes and returns the full set of findings, so a CLI operator (or a
test) can inspect exactly what WOULD happen before authorizing it.

Only ever calls the EXISTING `transition_subscription()`
(owner/app/subscriptions/services.py) for status changes -- never
reimplements the transition logic here, and never widens what that
function's shared VALID_TRANSITIONS table permits (see that table's own
comment on why `EXPIRED` stays terminal there, and
commercial_ops/renewal_requests.py's parallel comment). Every transition
this job performs (ACTIVE->PAST_DUE, ACTIVE->EXPIRED, PAST_DUE->EXPIRED) is
already permitted in that table for every caller, including the loosely-
gated generic route -- which is fine specifically because these are
date-driven, non-discretionary, RESTRICTING-only transitions (a term
ending is an objective fact, not a trust decision), unlike SUSPENSION or
EXPIRED->ACTIVE revival, which stay staff-only.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from sqlalchemy import select

from app.commercial_ops.commercial_policy import create_notification, resolve_policy_for_subscription
from app.extensions import db_session
from app.models.base import utcnow
from app.models.subscriptions import Subscription
from app.subscriptions.services import transition_subscription

_ELIGIBLE_STATUSES = ("ACTIVE", "PAST_DUE")


@dataclass
class ExpiryScanFinding:
    subscription_id: str
    action: str
    detail: dict


@dataclass
class ExpiryScanResult:
    as_of: date
    dry_run: bool
    scanned_count: int = 0
    notifications_created: int = 0
    notifications_deduped: int = 0
    transitioned_to_past_due: int = 0
    transitioned_to_expired: int = 0
    findings: list[ExpiryScanFinding] = field(default_factory=list)


def _notification_type_for_offset(offset: int) -> str:
    if offset < 0:
        return "SUBSCRIPTION_EXPIRED"
    if offset == 0:
        return "SUBSCRIPTION_EXPIRING_TODAY"
    return f"SUBSCRIPTION_EXPIRING_{offset}_DAYS"


def run_expiry_scan(*, as_of: date | None = None, dry_run: bool = True, actor_staff_user_id=None) -> ExpiryScanResult:
    as_of = as_of or utcnow().date()
    result = ExpiryScanResult(as_of=as_of, dry_run=dry_run)

    subscriptions = db_session.execute(
        select(Subscription).where(
            Subscription.status.in_(_ELIGIBLE_STATUSES), Subscription.end_date.isnot(None)
        )
    ).scalars().all()

    for subscription in subscriptions:
        result.scanned_count += 1
        policy = resolve_policy_for_subscription(subscription, as_of=as_of)
        if policy is None:
            result.findings.append(ExpiryScanFinding(str(subscription.id), "SKIPPED_NO_POLICY", {}))
            continue

        days_until_end = (subscription.end_date - as_of).days

        # Part G: warning notifications. One per crossed offset, deduped by
        # (subscription, offset) -- a late or skipped scan run still
        # catches up on every offset it missed without ever duplicating
        # one it already created (the dedup_key UNIQUE constraint is the
        # actual guarantee; see create_notification()'s own docstring).
        for offset in policy.warning_offsets_days:
            if days_until_end > offset:
                continue
            dedup_key = f"SUBSCRIPTION_EXPIRY_WARNING:{subscription.id}:{offset}"
            if dry_run:
                result.findings.append(
                    ExpiryScanFinding(
                        str(subscription.id), "WARNING_NOTIFICATION", {"offset_days": offset, "dedup_key": dedup_key}
                    )
                )
                continue
            _notification, created = create_notification(
                notification_type=_notification_type_for_offset(offset),
                severity="CRITICAL" if offset <= 1 else "WARNING",
                title=(
                    f"Subscription expires in {offset} day(s)" if offset > 0 else "Subscription expiring/expired"
                ),
                message=(
                    f"Subscription {subscription.id} (product {subscription.product_id}) "
                    f"{'expires' if offset >= 0 else 'expired'} on {subscription.end_date.isoformat()}."
                ),
                dedup_key=dedup_key,
                customer_id=subscription.customer_id,
                subscription_id=subscription.id,
                assigned_role_code=(policy.notify_role_codes[0] if policy.notify_role_codes else None),
                source_policy_code=policy.policy_code,
            )
            if created:
                result.notifications_created += 1
            else:
                result.notifications_deduped += 1

        # Part I: date-driven, non-discretionary status transitions.
        if subscription.status == "ACTIVE" and days_until_end < 0:
            days_past_end = -days_until_end
            if days_past_end >= policy.past_due_start_days:
                target_status = "EXPIRED" if policy.past_due_start_days == 0 else "PAST_DUE"
                if dry_run:
                    result.findings.append(
                        ExpiryScanFinding(
                            str(subscription.id), f"TRANSITION_TO_{target_status}", {"days_past_end": days_past_end}
                        )
                    )
                else:
                    transition_subscription(
                        subscription, target_status, actor_staff_user_id,
                        reason="Automated expiry scan (date-driven).",
                    )
                    if target_status == "PAST_DUE":
                        result.transitioned_to_past_due += 1
                    else:
                        result.transitioned_to_expired += 1

        elif subscription.status == "PAST_DUE" and policy.auto_expire_after_grace:
            days_past_due = -days_until_end - policy.past_due_start_days
            if days_past_due >= policy.payment_grace_days:
                if dry_run:
                    result.findings.append(
                        ExpiryScanFinding(str(subscription.id), "TRANSITION_TO_EXPIRED", {"days_past_due": days_past_due})
                    )
                else:
                    transition_subscription(
                        subscription, "EXPIRED", actor_staff_user_id,
                        reason="Automated expiry scan: payment grace exhausted.",
                    )
                    result.transitioned_to_expired += 1

    return result
