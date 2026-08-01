"""Phase 9.5A Milestone 22 -- EmployeeProfileService / EmployeePresenceService.

Foundation operations only: create/update a profile, suspend/terminate safely
(reusing the real Phase-8V session-revocation mechanism), touch/revoke a
presence session. Follows the exact pattern established by
owner/app/customers/services.py: plain functions, db_session.add()+commit(),
then audit_record() immediately after.
"""
from __future__ import annotations

import uuid

from sqlalchemy import select

from app.audit.services import record as audit_record
from app.auth.session import revoke_all_sessions_for_staff
from app.extensions import db_session
from app.models.base import utcnow
from app.models.employees import EmployeeProfile, EmployeePresenceSession


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


def suspend_employee(profile: EmployeeProfile, reason: str, actor_staff_user_id: uuid.UUID) -> EmployeeProfile:
    """Suspends the profile and revokes every active session for the
    underlying StaffUser -- reuses revoke_all_sessions_for_staff() (Phase 8V,
    owner/app/auth/session.py:128), no new revocation mechanism built."""
    before_status = profile.employment_status
    profile.employment_status = "SUSPENDED"
    profile.updated_by_staff_user_id = actor_staff_user_id
    profile.version += 1
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


def terminate_employee(profile: EmployeeProfile, reason: str, actor_staff_user_id: uuid.UUID) -> EmployeeProfile:
    before_status = profile.employment_status
    profile.employment_status = "TERMINATED"
    profile.employment_end_date = utcnow().date()
    profile.updated_by_staff_user_id = actor_staff_user_id
    profile.version += 1
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
