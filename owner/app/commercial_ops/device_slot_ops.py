"""Device-slot administration (Phase 8 Part P, Milestone 5).

`release_device_slot()`/`replace_device_slot()` are thin, mandatory-reason
wrappers around the existing, unmodified `transition_installation()`
(`app/installations/services.py`) -- both target transitions
(`* -> DEACTIVATED`, `ACTIVE -> REPLACED`) were already declared in that
function's `VALID_TRANSITIONS` table before Phase 8. They exist as their
own functions for the same reason `cancel_pilot()`/
`revoke_emergency_extension()` do (Milestone 4): a mandatory-reason gate
plus a single, intention-revealing entry point for the staff UI, not a
new state-machine capability.

`DeviceSlotException` (temporary over-limit allowance) and the over-limit
scan are new capability -- see their own docstrings/comments below.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from sqlalchemy import select

from app.audit.services import record as audit_record
from app.commercial_ops.commercial_policy import create_notification
from app.extensions import db_session
from app.installations.services import count_slot_consuming_installations, transition_installation
from app.models.activation_governance import DeviceSlotException
from app.models.base import utcnow
from app.models.installations import Installation
from app.models.licensing import License

MAX_DEVICE_SLOT_EXCEPTION_DAYS = 90

_LICENSE_STATUSES_ELIGIBLE_FOR_SCAN = ("ISSUED", "ACTIVE")


class DeviceSlotError(ValueError):
    pass


def release_device_slot(installation: Installation, *, reason: str, actor_staff_user_id) -> None:
    if not reason or not reason.strip():
        raise DeviceSlotError("A reason is required to release a device slot.")
    transition_installation(installation, "DEACTIVATED", actor_staff_user_id, reason=reason)


def replace_device_slot(old_installation: Installation, *, reason: str, actor_staff_user_id) -> None:
    if not reason or not reason.strip():
        raise DeviceSlotError("A reason is required to replace a device slot.")
    transition_installation(old_installation, "REPLACED", actor_staff_user_id, reason=reason)


def create_device_slot_exception(
    *,
    license_row: License,
    extra_slots: int,
    reason: str,
    expires_at: datetime,
    actor_staff_user_id,
    starts_at: datetime | None = None,
) -> DeviceSlotException:
    if extra_slots <= 0:
        raise DeviceSlotError("extra_slots must be positive.")
    if not reason or not reason.strip():
        raise DeviceSlotError("A reason is required to create a device slot exception.")
    starts_at = starts_at or utcnow()
    if expires_at <= starts_at:
        raise DeviceSlotError("expires_at must be after starts_at.")
    if expires_at - starts_at > timedelta(days=MAX_DEVICE_SLOT_EXCEPTION_DAYS):
        raise DeviceSlotError(
            f"Device slot exceptions may not exceed {MAX_DEVICE_SLOT_EXCEPTION_DAYS} days -- temporary means temporary."
        )

    exception = DeviceSlotException(
        license_id=license_row.id, extra_slots=extra_slots, reason=reason,
        starts_at=starts_at, expires_at=expires_at, status="ACTIVE",
        created_by_staff_user_id=actor_staff_user_id,
    )
    db_session.add(exception)
    db_session.commit()
    audit_record(
        actor_staff_user_id=actor_staff_user_id,
        actor_role_snapshot=None,
        action_code="DEVICE_SLOT_EXCEPTION_CREATED",
        entity_type="device_slot_exception",
        entity_public_id=str(exception.id),
        after_state={"license_id": str(license_row.id), "extra_slots": extra_slots, "expires_at": expires_at.isoformat()},
        reason=reason,
    )
    return exception


def revoke_device_slot_exception(exception: DeviceSlotException, *, reason: str, actor_staff_user_id) -> None:
    if exception.status != "ACTIVE":
        raise DeviceSlotError(f"Cannot revoke a device slot exception in status {exception.status}.")
    if not reason or not reason.strip():
        raise DeviceSlotError("A reason is required to revoke a device slot exception.")
    exception.status = "REVOKED"
    exception.revoked_at = utcnow()
    exception.revoked_by_staff_user_id = actor_staff_user_id
    exception.revocation_reason = reason
    db_session.commit()
    audit_record(
        actor_staff_user_id=actor_staff_user_id,
        actor_role_snapshot=None,
        action_code="DEVICE_SLOT_EXCEPTION_REVOKED",
        entity_type="device_slot_exception",
        entity_public_id=str(exception.id),
        after_state={"status": "REVOKED"},
        reason=reason,
    )


def resolve_effective_device_limit(license_row: License, *, as_of: datetime | None = None) -> int:
    """`License.device_limit` stays the permanent contractual figure --
    never written to by this function. Sums every currently-ACTIVE,
    currently-in-window exception on top of it; multiple stacked exceptions
    are allowed (each independently reasoned, time-boxed, and revocable)."""
    as_of = as_of or utcnow()
    stmt = select(DeviceSlotException).where(
        DeviceSlotException.license_id == license_row.id,
        DeviceSlotException.status == "ACTIVE",
        DeviceSlotException.starts_at <= as_of,
        DeviceSlotException.expires_at > as_of,
    )
    extra = sum(e.extra_slots for e in db_session.execute(stmt).scalars().all())
    return license_row.device_limit + extra


@dataclass
class OverLimitFinding:
    license_id: str
    active_count: int
    effective_limit: int
    dedup_key: str


@dataclass
class DeviceLimitScanResult:
    as_of: date
    dry_run: bool
    scanned_count: int = 0
    notifications_created: int = 0
    notifications_deduped: int = 0
    findings: list[OverLimitFinding] = field(default_factory=list)


def scan_over_limit_licenses(*, as_of: date | None = None, dry_run: bool = True) -> DeviceLimitScanResult:
    """Report-only by default, same convention as `expiry_scan.py`. NEVER
    deactivates or replaces any installation itself -- Part P's explicit
    "over-limit remediation without silent deactivation": this job only
    ever surfaces an `InternalNotification` for a human to act on via
    `release_device_slot()`/`replace_device_slot()`/a plan-appropriate
    renewal, never picks which installation to remove."""
    as_of = as_of or utcnow().date()
    as_of_dt = datetime.combine(as_of, datetime.min.time())
    result = DeviceLimitScanResult(as_of=as_of, dry_run=dry_run)

    licenses = db_session.execute(
        select(License).where(License.status.in_(_LICENSE_STATUSES_ELIGIBLE_FOR_SCAN))
    ).scalars().all()

    for license_row in licenses:
        result.scanned_count += 1
        active_count = count_slot_consuming_installations(license_row.id)
        effective_limit = resolve_effective_device_limit(license_row, as_of=as_of_dt)
        if active_count <= effective_limit:
            continue

        dedup_key = f"DEVICE_LIMIT_EXCEEDED:{license_row.id}:{active_count}"
        finding = OverLimitFinding(str(license_row.id), active_count, effective_limit, dedup_key)
        result.findings.append(finding)
        if dry_run:
            continue

        _notification, created = create_notification(
            notification_type="DEVICE_LIMIT_EXCEEDED",
            severity="WARNING",
            title=f"License {license_row.id} is over its device limit",
            message=(
                f"License {license_row.id} has {active_count} active installation(s) against an effective "
                f"limit of {effective_limit}. Resolve via release_device_slot()/replace_device_slot() or a "
                f"device-allowance renewal -- never automatically."
            ),
            dedup_key=dedup_key,
            license_id=license_row.id,
            customer_id=license_row.customer_id,
            assigned_role_code="SUPPORT",
            source_policy_code=None,
        )
        if created:
            result.notifications_created += 1
        else:
            result.notifications_deduped += 1

    return result
