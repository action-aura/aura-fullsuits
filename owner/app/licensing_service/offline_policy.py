"""Offline-grace policy authority (Part P). Defines and returns policy only --
never enforces it inside any product, and its enum structurally cannot express
a destructive instruction (Principle 12)."""
from __future__ import annotations

from sqlalchemy import select

from app.extensions import db_session
from app.models.licensing_service import LicenseOfflinePolicyAssignment, OfflinePolicy

DEFAULT_POLICY_CODE = "standard-v1"

# hard_expiry_behavior enum -- deliberately contains no destructive option.
HARD_EXPIRY_BEHAVIORS = ("WARN_ONLY", "RESTRICT_COMMERCIAL_FEATURES_PRESERVE_DATA")


def seed_default_offline_policy() -> OfflinePolicy:
    existing = db_session.execute(select(OfflinePolicy).where(OfflinePolicy.policy_code == DEFAULT_POLICY_CODE)).scalars().first()
    if existing is not None:
        return existing
    policy = OfflinePolicy(
        policy_code=DEFAULT_POLICY_CODE,
        policy_version=1,
        check_in_interval_seconds=24 * 3600,
        retry_interval_seconds=3600,
        offline_grace_seconds=14 * 24 * 3600,
        warning_start_seconds=10 * 24 * 3600,
        hard_expiry_behavior="WARN_ONLY",
        clock_rollback_tolerance_seconds=300,
        assertion_refresh_threshold_seconds=24 * 3600,
        emergency_extension_allowed=False,
    )
    db_session.add(policy)
    db_session.commit()
    return policy


def get_policy_for_license(license_row) -> OfflinePolicy:
    """Falls back to the default policy if the license has no explicit
    assignment -- never returns 'no policy' (which would be indistinguishable
    from unlimited grace)."""
    assignment = db_session.execute(
        select(LicenseOfflinePolicyAssignment).where(LicenseOfflinePolicyAssignment.license_id == license_row.id)
    ).scalars().first()
    if assignment is not None:
        return assignment.offline_policy
    default = db_session.execute(select(OfflinePolicy).where(OfflinePolicy.policy_code == DEFAULT_POLICY_CODE)).scalars().first()
    if default is None:
        default = seed_default_offline_policy()
    return default


def assign_policy(license_row, policy_code: str, actor_staff_user_id) -> LicenseOfflinePolicyAssignment:
    from app.audit.services import record as audit_record

    policy = db_session.execute(select(OfflinePolicy).where(OfflinePolicy.policy_code == policy_code)).scalars().first()
    if policy is None:
        raise ValueError(f"Unknown offline policy code: {policy_code}")
    existing = db_session.execute(
        select(LicenseOfflinePolicyAssignment).where(LicenseOfflinePolicyAssignment.license_id == license_row.id)
    ).scalars().first()
    if existing is not None:
        before = existing.offline_policy.policy_code
        existing.offline_policy_id = policy.id
        existing.assigned_by_staff_user_id = actor_staff_user_id
        row = existing
    else:
        before = None
        row = LicenseOfflinePolicyAssignment(
            license_id=license_row.id, offline_policy_id=policy.id, assigned_by_staff_user_id=actor_staff_user_id
        )
        db_session.add(row)
    db_session.commit()
    audit_record(
        actor_staff_user_id=actor_staff_user_id, actor_role_snapshot=None, action_code="OFFLINE_POLICY_ASSIGNED",
        entity_type="license", entity_public_id=str(license_row.id),
        before_state={"policy_code": before}, after_state={"policy_code": policy_code},
    )
    return row


def serialize_policy(policy: OfflinePolicy) -> dict:
    return {
        "policy_id": policy.policy_code,
        "policy_version": policy.policy_version,
        "check_in_interval_seconds": policy.check_in_interval_seconds,
        "retry_interval_seconds": policy.retry_interval_seconds,
        "offline_grace_seconds": policy.offline_grace_seconds,
        "warning_start_seconds": policy.warning_start_seconds,
        "hard_expiry_behavior": policy.hard_expiry_behavior,
        "clock_rollback_tolerance_seconds": policy.clock_rollback_tolerance_seconds,
        "assertion_refresh_threshold_seconds": policy.assertion_refresh_threshold_seconds,
        "emergency_extension_allowed": policy.emergency_extension_allowed,
        "emergency_extension_until": policy.emergency_extension_until.isoformat() if policy.emergency_extension_until else None,
    }
