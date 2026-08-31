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
from app.commercial_ops.errors import StableCodeError
from app.extensions import db_session
from app.installations.services import count_slot_consuming_installations, transition_installation
from app.models.activation_governance import DeviceSlotException
from app.models.base import utcnow
from app.models.commercial_sales import CommercialOperationsIdempotencyKey
from app.models.installations import Installation
from app.models.licensing import License
from app.models.subscriptions import Subscription

MAX_DEVICE_SLOT_EXCEPTION_DAYS = 90

_LICENSE_STATUSES_ELIGIBLE_FOR_SCAN = ("ISSUED", "ACTIVE")

# docs/owner/packages-and-issuance-design.md §C.2 -- shared idempotency
# ledger convention (CommercialOperationsIdempotencyKey), same operation-code
# keying as LEAD_CREATION_OPERATION_CODE etc. in leads/services.py.
ADD_DEVICES_OPERATION_CODE = "LICENSE_ADD_DEVICES"


class DeviceSlotError(StableCodeError):
    _MESSAGES = {
        "REASON_REQUIRED_TO_RELEASE": "A reason is required to release a device slot.",
        "REASON_REQUIRED_TO_REPLACE": "A reason is required to replace a device slot.",
        "EXTRA_SLOTS_MUST_BE_POSITIVE": "extra_slots must be positive.",
        "REASON_REQUIRED_TO_CREATE_EXCEPTION": "A reason is required to create a device slot exception.",
        "EXPIRES_AT_BEFORE_STARTS_AT": "expires_at must be after starts_at.",
        "EXCEPTION_EXCEEDS_MAX_DAYS": "Device slot exceptions may not exceed {max_days} days -- temporary means temporary.",
        "INVALID_REVOKE_STATUS": "Cannot revoke a device slot exception in status {status}.",
        "REASON_REQUIRED_TO_REVOKE": "A reason is required to revoke a device slot exception.",
        "ADDITIONAL_DEVICES_MUST_BE_POSITIVE": "additional_devices must be positive.",
        "REASON_REQUIRED_TO_ADD_DEVICES": "A reason is required to add devices to a license.",
        "DEVICE_LIMIT_BELOW_ACTIVE_COUNT": (
            "Cannot set this license's device limit to {new_limit} -- {active_count} device(s) are "
            "already active on it. Add enough devices to cover current usage."
        ),
        "LICENSE_NOT_FOUND": "License not found.",
        "SUBSCRIPTION_NOT_FOUND": "Subscription not found for this license.",
    }


def release_device_slot(installation: Installation, *, reason: str, actor_staff_user_id) -> None:
    if not reason or not reason.strip():
        raise DeviceSlotError("REASON_REQUIRED_TO_RELEASE")
    transition_installation(installation, "DEACTIVATED", actor_staff_user_id, reason=reason)


def replace_device_slot(old_installation: Installation, *, reason: str, actor_staff_user_id) -> None:
    if not reason or not reason.strip():
        raise DeviceSlotError("REASON_REQUIRED_TO_REPLACE")
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
        raise DeviceSlotError("EXTRA_SLOTS_MUST_BE_POSITIVE")
    if not reason or not reason.strip():
        raise DeviceSlotError("REASON_REQUIRED_TO_CREATE_EXCEPTION")
    starts_at = starts_at or utcnow()
    if expires_at <= starts_at:
        raise DeviceSlotError("EXPIRES_AT_BEFORE_STARTS_AT")
    if expires_at - starts_at > timedelta(days=MAX_DEVICE_SLOT_EXCEPTION_DAYS):
        raise DeviceSlotError("EXCEPTION_EXCEEDS_MAX_DAYS", max_days=MAX_DEVICE_SLOT_EXCEPTION_DAYS)

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
        raise DeviceSlotError("INVALID_REVOKE_STATUS", status=exception.status)
    if not reason or not reason.strip():
        raise DeviceSlotError("REASON_REQUIRED_TO_REVOKE")
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


def add_devices(
    license_row: License,
    *,
    additional_devices: int,
    reason: str,
    actor_staff_user_id,
    idempotency_key: str | None = None,
) -> License:
    """Permanently raises a license's device allowance in one act (Phase 8V,
    docs/owner/packages-and-issuance-design.md §C.2 -- "the single most
    common paid action... has no first-class path"; §0 finding 6 verified
    the only prior ways to change `device_limit` were the five-state
    renewal-request pipeline or a temporary, <=90-day
    DeviceSlotException). Distinct from `create_device_slot_exception()`
    above: that grants a TEMPORARY allowance on top of `device_limit`
    (never writes it); this writes `license_row.device_limit` itself,
    exactly like `apply_renewal_request()`'s device-allowance mirroring
    (`commercial_ops/renewal_requests.py`) -- which is this function's
    deliberate template, including its FOR UPDATE locking. Unlike the
    renewal pipeline, this is additive-only and has no DRAFT/APPROVED/
    APPLIED workflow, so a device sale is one staff action, not five.

    Idempotency follows the CommercialOperationsIdempotencyKey convention
    shared by Quote/Order/Invoice/Payment/Refund/Commission/Lead creation
    (see `leads/services.py::create_lead()`), not `RenewalRequest`'s own
    `idempotency_key` column -- this is a single mutation against an
    existing row, not a persisted multi-state request record, so there is
    no separate entity to key against.

    Deliberately does NOT inherit `apply_renewal_request()`'s "silently
    permit a lower device_allowance than currently-active installations"
    behavior (that function relies on `scan_over_limit_licenses()` to flag
    the resulting over-limit state later, per its own Phase 8V-P2 comment).
    This action refuses outright instead: "Add devices" must never leave a
    license persisted below what is already deployed on it.
    """
    if idempotency_key is not None:
        existing_key = db_session.execute(
            select(CommercialOperationsIdempotencyKey).where(
                CommercialOperationsIdempotencyKey.idempotency_key == idempotency_key,
                CommercialOperationsIdempotencyKey.operation_code == ADD_DEVICES_OPERATION_CODE,
            )
        ).scalars().first()
        if existing_key is not None:
            return db_session.get(License, existing_key.result_reference_id)

    if additional_devices <= 0:
        raise DeviceSlotError("ADDITIONAL_DEVICES_MUST_BE_POSITIVE")
    if not reason or not reason.strip():
        raise DeviceSlotError("REASON_REQUIRED_TO_ADD_DEVICES")

    # Re-select FOR UPDATE, exactly like apply_renewal_request()'s License
    # lock -- a concurrent activation attempt against this exact license
    # (activation.py's own SELECT ... FOR UPDATE) serializes against this
    # write rather than racing it.
    locked_license = db_session.execute(
        select(License).where(License.id == license_row.id).with_for_update()
    ).scalars().first()
    if locked_license is None:
        raise DeviceSlotError("LICENSE_NOT_FOUND")

    subscription = db_session.execute(
        select(Subscription).where(Subscription.id == locked_license.subscription_id).with_for_update()
    ).scalars().first()
    if subscription is None:
        raise DeviceSlotError("SUBSCRIPTION_NOT_FOUND")

    active_count = count_slot_consuming_installations(locked_license.id)
    previous_device_limit = locked_license.device_limit
    new_device_limit = previous_device_limit + additional_devices

    if new_device_limit < active_count:
        raise DeviceSlotError("DEVICE_LIMIT_BELOW_ACTIVE_COUNT", new_limit=new_device_limit, active_count=active_count)

    locked_license.device_limit = new_device_limit
    subscription.device_allowance = new_device_limit

    if idempotency_key is not None:
        db_session.add(
            CommercialOperationsIdempotencyKey(
                idempotency_key=idempotency_key,
                operation_code=ADD_DEVICES_OPERATION_CODE,
                result_reference_id=locked_license.id,
            )
        )
    db_session.commit()
    audit_record(
        actor_staff_user_id=actor_staff_user_id,
        actor_role_snapshot=None,
        action_code="LICENSE_DEVICES_ADDED",
        entity_type="license",
        entity_public_id=str(locked_license.id),
        before_state={"device_limit": previous_device_limit},
        after_state={"device_limit": new_device_limit, "additional_devices": additional_devices},
        reason=reason,
    )
    return locked_license


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


def scan_over_limit_licenses(
    *, as_of: date | None = None, now: datetime | None = None, dry_run: bool = True
) -> DeviceLimitScanResult:
    """Report-only by default, same convention as `expiry_scan.py`. NEVER
    deactivates or replaces any installation itself -- Part P's explicit
    "over-limit remediation without silent deactivation": this job only
    ever surfaces an `InternalNotification` for a human to act on via
    `release_device_slot()`/`replace_device_slot()`/a plan-appropriate
    renewal, never picks which installation to remove.

    Phase 8V-P9: `as_of` (date-precision) is used only for the scan
    result's own `as_of`/dedup bookkeeping -- it must NEVER be used to
    evaluate DeviceSlotException windows. Those are stored with
    timezone-aware DateTime precision (see DeviceSlotException's own
    columns and create_device_slot_exception()'s `datetime` parameters),
    and the real enforcement path (activation.py's own device-limit check,
    via resolve_effective_device_limit()) already evaluates them against
    real wall-clock time. A prior version of this scan truncated to
    midnight-of-`as_of` before calling resolve_effective_device_limit(),
    which meant a real, active, same-day short-duration exception was
    correctly honored by activation.py but invisible to this scan's own
    over-limit finding -- a detection/notification precision gap (P2:
    enforcement itself was never affected, only this scan's own report),
    not a security bypass. Fixed by evaluating the effective limit at
    `now` (real current time) here, independent of `as_of`."""
    as_of = as_of or utcnow().date()
    now = now or utcnow()
    result = DeviceLimitScanResult(as_of=as_of, dry_run=dry_run)

    licenses = db_session.execute(
        select(License).where(License.status.in_(_LICENSE_STATUSES_ELIGIBLE_FOR_SCAN))
    ).scalars().all()

    for license_row in licenses:
        result.scanned_count += 1
        active_count = count_slot_consuming_installations(license_row.id)
        effective_limit = resolve_effective_device_limit(license_row, as_of=now)
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
