"""Phase 9.5A Milestone 22 / Phase 9.5B -- EmployeeProfileService /
EmployeePresenceService.

Create/update/activate/suspend/reactivate/terminate/archive a profile
(lifecycle state machine below), touch/revoke a presence session. Follows the
exact pattern established by owner/app/customers/services.py: plain
functions, db_session.add()+commit(), then audit_record() immediately after.
"""
from __future__ import annotations

import uuid

from sqlalchemy import select

from app.audit.services import record as audit_record
from app.auth.session import revoke_all_sessions_for_staff
from app.extensions import db_session
from app.models.base import utcnow
from app.models.employees import EmployeeProfile, EmployeePresenceSession
from app.models.staff import StaffUser
from app.security.super_admin_guard import assert_can_remove_super_admin_status

# Phase 9.5B Milestone 2 -- canonical employment-status transitions.
# TERMINATED -> ACTIVE is deliberately absent (no rehire policy this phase --
# governing spec's own explicit instruction). ARCHIVED is terminal.
ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    "PENDING": {"ACTIVE", "SUSPENDED", "TERMINATED"},
    "ACTIVE": {"SUSPENDED", "TERMINATED"},
    "SUSPENDED": {"ACTIVE", "TERMINATED"},
    "TERMINATED": {"ARCHIVED"},
    "ARCHIVED": set(),
}


class InvalidEmploymentTransitionError(ValueError):
    """Raised when a requested employment_status transition is not in
    ALLOWED_TRANSITIONS -- e.g. TERMINATED -> ACTIVE, ARCHIVED -> anything,
    or a same-state no-op transition."""


def _assert_valid_transition(current: str, target: str) -> None:
    if target not in ALLOWED_TRANSITIONS.get(current, set()):
        raise InvalidEmploymentTransitionError(f"{current} -> {target} is not an allowed employment-status transition")


def create_employee_profile(fields: dict, actor_staff_user_id: uuid.UUID) -> EmployeeProfile:
    profile = EmployeeProfile(
        **fields,
        created_by_staff_user_id=actor_staff_user_id,
        updated_by_staff_user_id=actor_staff_user_id,
    )
    db_session.add(profile)
    db_session.commit()
    audit_record(
        actor_staff_user_id=actor_staff_user_id,
        actor_role_snapshot=None,
        action_code="EMPLOYEE_PROFILE_CREATED",
        entity_type="employee_profile",
        entity_public_id=str(profile.id),
        after_state={"employee_number": profile.employee_number, "employment_status": profile.employment_status},
    )
    return profile


def update_employee_profile(profile: EmployeeProfile, fields: dict, actor_staff_user_id: uuid.UUID) -> EmployeeProfile:
    before = {k: getattr(profile, k) for k in fields}
    for k, v in fields.items():
        setattr(profile, k, v)
    profile.updated_by_staff_user_id = actor_staff_user_id
    profile.version += 1
    db_session.commit()
    audit_record(
        actor_staff_user_id=actor_staff_user_id,
        actor_role_snapshot=None,
        action_code="EMPLOYEE_PROFILE_UPDATED",
        entity_type="employee_profile",
        entity_public_id=str(profile.id),
        before_state=before,
        after_state=fields,
    )
    return profile


def activate_employee(profile: EmployeeProfile, actor_staff_user_id: uuid.UUID) -> EmployeeProfile:
    """PENDING -> ACTIVE. Normally happens automatically when onboarding
    setup completes (see app.staff.services.create_staff_from_invitation);
    exposed as a direct management action for the case where a profile was
    created without the invitation flow (e.g. an account that already
    existed before Phase 9.5B)."""
    before_status = profile.employment_status
    _assert_valid_transition(before_status, "ACTIVE")
    profile.employment_status = "ACTIVE"
    profile.updated_by_staff_user_id = actor_staff_user_id
    profile.version += 1
    staff = db_session.get(StaffUser, profile.staff_user_id)
    if staff is not None and staff.disabled_at is None:
        staff.is_active = True
    db_session.commit()
    audit_record(
        actor_staff_user_id=actor_staff_user_id,
        actor_role_snapshot=None,
        action_code="EMPLOYEE_ACTIVATED",
        entity_type="employee_profile",
        entity_public_id=str(profile.id),
        before_state={"employment_status": before_status},
        after_state={"employment_status": "ACTIVE"},
    )
    return profile


def suspend_employee(profile: EmployeeProfile, reason: str, actor_staff_user_id: uuid.UUID) -> EmployeeProfile:
    """Suspends the profile, blocks new login (StaffUser.is_active=False --
    the exact gate app.auth.services.authenticate() already checks, not a
    new one), and revokes every active session for the underlying StaffUser
    -- reuses revoke_all_sessions_for_staff() (Phase 8V,
    owner/app/auth/session.py:128), no new revocation mechanism built."""
    before_status = profile.employment_status
    _assert_valid_transition(before_status, "SUSPENDED")
    assert_can_remove_super_admin_status(profile.staff_user_id)
    profile.employment_status = "SUSPENDED"
    profile.updated_by_staff_user_id = actor_staff_user_id
    profile.version += 1
    staff = db_session.get(StaffUser, profile.staff_user_id)
    if staff is not None:
        staff.is_active = False
    revoke_all_sessions_for_staff(profile.staff_user_id, reason="employee_suspended")
    db_session.commit()
    audit_record(
        actor_staff_user_id=actor_staff_user_id,
        actor_role_snapshot=None,
        action_code="EMPLOYEE_SUSPENDED",
        entity_type="employee_profile",
        entity_public_id=str(profile.id),
        reason=reason,
        before_state={"employment_status": before_status},
        after_state={"employment_status": "SUSPENDED"},
    )
    return profile


def reactivate_employee(profile: EmployeeProfile, actor_staff_user_id: uuid.UUID) -> EmployeeProfile:
    """SUSPENDED -> ACTIVE only (governing spec: no TERMINATED -> ACTIVE
    without an explicit rehire policy, not built this phase). Does not
    override a separate, independent app.staff.services.disable_staff()
    decision -- if the account was ALSO generically disabled
    (StaffUser.disabled_at is set), the profile still reactivates but the
    account stays login-blocked until that separate decision is reversed."""
    before_status = profile.employment_status
    _assert_valid_transition(before_status, "ACTIVE")
    profile.employment_status = "ACTIVE"
    profile.updated_by_staff_user_id = actor_staff_user_id
    profile.version += 1
    staff = db_session.get(StaffUser, profile.staff_user_id)
    account_reactivated = False
    if staff is not None and staff.disabled_at is None:
        staff.is_active = True
        account_reactivated = True
    db_session.commit()
    audit_record(
        actor_staff_user_id=actor_staff_user_id,
        actor_role_snapshot=None,
        action_code="EMPLOYEE_REACTIVATED",
        entity_type="employee_profile",
        entity_public_id=str(profile.id),
        before_state={"employment_status": before_status},
        after_state={"employment_status": "ACTIVE", "account_login_restored": account_reactivated},
    )
    return profile


def terminate_employee(profile: EmployeeProfile, reason: str, actor_staff_user_id: uuid.UUID) -> EmployeeProfile:
    before_status = profile.employment_status
    _assert_valid_transition(before_status, "TERMINATED")
    assert_can_remove_super_admin_status(profile.staff_user_id)
    profile.employment_status = "TERMINATED"
    profile.employment_end_date = utcnow().date()
    profile.updated_by_staff_user_id = actor_staff_user_id
    profile.version += 1
    staff = db_session.get(StaffUser, profile.staff_user_id)
    if staff is not None:
        staff.is_active = False
    revoke_all_sessions_for_staff(profile.staff_user_id, reason="employee_terminated")
    db_session.commit()
    audit_record(
        actor_staff_user_id=actor_staff_user_id,
        actor_role_snapshot=None,
        action_code="EMPLOYEE_TERMINATED",
        entity_type="employee_profile",
        entity_public_id=str(profile.id),
        reason=reason,
        before_state={"employment_status": before_status},
        after_state={"employment_status": "TERMINATED", "employment_end_date": str(profile.employment_end_date)},
    )
    return profile


def archive_employee(profile: EmployeeProfile, actor_staff_user_id: uuid.UUID) -> EmployeeProfile:
    """TERMINATED -> ARCHIVED. Hides from active lists, preserves full
    history (archived_at set, row never deleted -- Non-Negotiable Principle 9)."""
    before_status = profile.employment_status
    _assert_valid_transition(before_status, "ARCHIVED")
    profile.employment_status = "ARCHIVED"
    profile.archived_at = utcnow()
    profile.updated_by_staff_user_id = actor_staff_user_id
    profile.version += 1
    db_session.commit()
    audit_record(
        actor_staff_user_id=actor_staff_user_id,
        actor_role_snapshot=None,
        action_code="EMPLOYEE_ARCHIVED",
        entity_type="employee_profile",
        entity_public_id=str(profile.id),
        before_state={"employment_status": before_status},
        after_state={"employment_status": "ARCHIVED"},
    )
    return profile


def touch_presence(
    employee_profile_id: uuid.UUID,
    staff_session_id: uuid.UUID,
    app_instance_id: str,
    platform: str,
    app_version: str | None = None,
    device_label: str | None = None,
) -> EmployeePresenceSession:
    """Upserts a presence session by (employee_profile_id, app_instance_id).
    No location field, no continuous telemetry -- see
    docs/owner/phase9_5a/employee-presence-contract.md."""
    now = utcnow()
    stmt = select(EmployeePresenceSession).where(
        EmployeePresenceSession.employee_profile_id == employee_profile_id,
        EmployeePresenceSession.app_instance_id == app_instance_id,
        EmployeePresenceSession.revoked_at.is_(None),
    )
    session_row = db_session.execute(stmt).scalars().first()
    if session_row is None:
        session_row = EmployeePresenceSession(
            employee_profile_id=employee_profile_id,
            staff_session_id=staff_session_id,
            app_instance_id=app_instance_id,
            platform=platform,
            app_version=app_version,
            device_label=device_label,
            last_seen_at=now,
            last_activity_at=now,
        )
        db_session.add(session_row)
    else:
        session_row.staff_session_id = staff_session_id
        session_row.last_seen_at = now
        session_row.last_activity_at = now
        if app_version:
            session_row.app_version = app_version
    db_session.commit()
    return session_row


def revoke_presence(session_row: EmployeePresenceSession) -> None:
    session_row.revoked_at = utcnow()
    db_session.commit()
