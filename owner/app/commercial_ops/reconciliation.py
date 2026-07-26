"""Reconciliation engine (Phase 8 Part Q, Milestone 6).

Report-only by default, same convention as `expiry_scan.py`/
`device_slot_ops.scan_over_limit_licenses()`. Never mutates any commercial
record itself -- every finding here is a surfaced INCONSISTENCY or a STALLED
workflow for a human to act on, never something this engine "fixes." Three
independent checks, one pass:

1. STATE_INCONSISTENT -- reuses the existing, unmodified
   `resolve_commercial_state()` (Milestone 1) as the single source of truth
   for what "consistent" means, rather than re-deriving a second definition
   of consistency here. A License/Subscription pair whose combined status
   resolves to `CommercialState.INVALID` is, by that function's own
   deny-by-default design, already flagged as needing reconciliation --
   this engine's only job is to walk every pair and surface the ones that
   land there.
2. STALE_APPROVED_RENEWAL -- a `RenewalRequest` sitting in `APPROVED`
   (payment/separation-of-duties already cleared) but never `APPLIED`.
3. STALE_PENDING_ACTIVATION -- a `PendingActivation` sitting in
   `PENDING_REVIEW` past a reasonable window, i.e. a manual-approval queue
   item nobody has acted on.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from sqlalchemy import select

from app.commercial_ops.commercial_policy import create_notification
from app.commercial_ops.state_resolution import CommercialState, resolve_commercial_state
from app.extensions import db_session
from app.models.activation_governance import PendingActivation
from app.models.base import utcnow
from app.models.commercial_ops import RenewalRequest
from app.models.licensing import License

DEFAULT_STALE_PENDING_ACTIVATION_HOURS = 48
DEFAULT_STALE_APPROVED_RENEWAL_DAYS = 7


@dataclass
class ReconciliationFinding:
    check: str
    entity_type: str
    entity_id: str
    detail: dict


@dataclass
class ReconciliationResult:
    as_of: date
    dry_run: bool
    licenses_checked: int = 0
    renewals_checked: int = 0
    pending_activations_checked: int = 0
    notifications_created: int = 0
    notifications_deduped: int = 0
    findings: list[ReconciliationFinding] = field(default_factory=list)


def _check_state_inconsistency(result: ReconciliationResult, *, as_of: date, dry_run: bool) -> None:
    licenses = db_session.execute(select(License).where(License.status != "REPLACED")).scalars().all()
    for license_row in licenses:
        result.licenses_checked += 1
        subscription = license_row.subscription
        decision = resolve_commercial_state(
            subscription_status=subscription.status,
            license_status=license_row.status,
            subscription_end_date=subscription.end_date,
            license_valid_until=license_row.valid_until,
            as_of=as_of,
            cancellation_effective_date=subscription.cancellation_date,
        )
        if decision.state != CommercialState.INVALID:
            continue

        detail = {
            "subscription_id": str(subscription.id), "subscription_status": subscription.status,
            "license_status": license_row.status, "reason_code": decision.reason_code,
        }
        result.findings.append(ReconciliationFinding("STATE_INCONSISTENT", "license", str(license_row.id), detail))
        if dry_run:
            continue
        _, created = create_notification(
            notification_type="RECONCILIATION_STATE_INCONSISTENT",
            severity="CRITICAL",
            title=f"Inconsistent commercial state for license {license_row.id}",
            message=(
                f"License {license_row.id} (status {license_row.status}) and its subscription "
                f"{subscription.id} (status {subscription.status}) resolve to an INVALID commercial "
                f"state ({decision.reason_code}). Needs manual reconciliation."
            ),
            dedup_key=f"RECONCILE_STATE_INVALID:{license_row.id}:{subscription.status}:{license_row.status}",
            customer_id=subscription.customer_id,
            subscription_id=subscription.id,
            license_id=license_row.id,
            assigned_role_code="SUPPORT",
        )
        if created:
            result.notifications_created += 1
        else:
            result.notifications_deduped += 1


def _check_stale_approved_renewals(result: ReconciliationResult, *, now: datetime, stale_days: int, dry_run: bool) -> None:
    cutoff = now - timedelta(days=stale_days)
    stale = db_session.execute(
        select(RenewalRequest).where(RenewalRequest.status == "APPROVED", RenewalRequest.approved_at <= cutoff)
    ).scalars().all()
    for renewal in stale:
        result.renewals_checked += 1
        detail = {"subscription_id": str(renewal.subscription_id), "approved_at": renewal.approved_at.isoformat()}
        result.findings.append(ReconciliationFinding("STALE_APPROVED_RENEWAL", "renewal_request", str(renewal.id), detail))
        if dry_run:
            continue
        _, created = create_notification(
            notification_type="RECONCILIATION_STALE_APPROVED_RENEWAL",
            severity="WARNING",
            title=f"Renewal request {renewal.id} approved but not applied",
            message=(
                f"Renewal request {renewal.id} for subscription {renewal.subscription_id} has been "
                f"APPROVED since {renewal.approved_at.isoformat()} (more than {stale_days} day(s) ago) "
                f"but was never applied."
            ),
            dedup_key=f"RECONCILE_STALE_RENEWAL:{renewal.id}",
            customer_id=renewal.customer_id,
            subscription_id=renewal.subscription_id,
            assigned_role_code="FINANCE",
        )
        if created:
            result.notifications_created += 1
        else:
            result.notifications_deduped += 1


def _check_stale_pending_activations(result: ReconciliationResult, *, now: datetime, stale_hours: int, dry_run: bool) -> None:
    cutoff = now - timedelta(hours=stale_hours)
    stale = db_session.execute(
        select(PendingActivation).where(PendingActivation.status == "PENDING_REVIEW", PendingActivation.created_at <= cutoff)
    ).scalars().all()
    for pending in stale:
        result.pending_activations_checked += 1
        detail = {"installation_id": str(pending.installation_id), "created_at": pending.created_at.isoformat()}
        result.findings.append(ReconciliationFinding("STALE_PENDING_ACTIVATION", "pending_activation", str(pending.id), detail))
        if dry_run:
            continue
        _, created = create_notification(
            notification_type="RECONCILIATION_STALE_PENDING_ACTIVATION",
            severity="WARNING",
            title=f"Activation {pending.installation_id} awaiting review since {pending.created_at.isoformat()}",
            message=(
                f"Pending activation {pending.id} (installation {pending.installation_id}) has been "
                f"PENDING_REVIEW for more than {stale_hours} hour(s)."
            ),
            dedup_key=f"RECONCILE_STALE_PENDING_ACTIVATION:{pending.id}",
            installation_id=pending.installation_id,
            assigned_role_code="SUPPORT",
        )
        if created:
            result.notifications_created += 1
        else:
            result.notifications_deduped += 1


def run_reconciliation(
    *,
    as_of: date | None = None,
    now: datetime | None = None,
    dry_run: bool = True,
    stale_pending_activation_hours: int = DEFAULT_STALE_PENDING_ACTIVATION_HOURS,
    stale_approved_renewal_days: int = DEFAULT_STALE_APPROVED_RENEWAL_DAYS,
) -> ReconciliationResult:
    """`as_of` (date-precision) governs the state-inconsistency check, same
    convention as `expiry_scan.py`. `now` (datetime-precision, independent
    of `as_of`) governs the two staleness checks -- defaults to the real
    current time rather than midnight-of-`as_of`, since "approved 47 hours
    ago" needs real precision, not a date boundary."""
    as_of = as_of or utcnow().date()
    now = now or utcnow()
    result = ReconciliationResult(as_of=as_of, dry_run=dry_run)

    _check_state_inconsistency(result, as_of=as_of, dry_run=dry_run)
    _check_stale_approved_renewals(result, now=now, stale_days=stale_approved_renewal_days, dry_run=dry_run)
    _check_stale_pending_activations(result, now=now, stale_hours=stale_pending_activation_hours, dry_run=dry_run)

    return result
