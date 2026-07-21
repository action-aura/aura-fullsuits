"""Staff management services (Part E/G/H)."""
from __future__ import annotations

import uuid
from datetime import timedelta

from sqlalchemy import select

from app.audit.services import record as audit_record
from app.auth.session import revoke_all_sessions_for_staff
from app.extensions import db_session
from app.models.base import utcnow
from app.models.staff import Role, StaffInvitation, StaffRoleAssignment, StaffUser
from app.security.passwords import hash_password
from app.security.tokens import generate_token, hash_token

INVITATION_TTL_SECONDS = 60 * 60 * 24 * 3  # 3 days


class SelfEscalationError(ValueError):
    """Raised when a non-Super-Admin actor attempts to grant the SUPER_ADMIN
    role, whether via direct role assignment or an invitation. Enforced here,
    in the service layer, as a second, structural check independent of
    whatever permission a role happens to be granted at any given time --
    holding staff.assign_roles/staff.create alone must never be sufficient to
    mint a new Super Admin."""


def _assert_not_granting_super_admin_unless_actor_is_one(role_codes, actor_staff_user_id: uuid.UUID) -> None:
    if "SUPER_ADMIN" not in role_codes:
        return
    actor = db_session.get(StaffUser, actor_staff_user_id) if actor_staff_user_id else None
    if actor is None or not actor.is_super_admin:
        raise SelfEscalationError("Only an existing Super Admin may grant the SUPER_ADMIN role.")


def create_staff_from_invitation(invitation: StaffInvitation, display_name: str, password_hash: str) -> StaffUser:
    staff = StaffUser(email=invitation.email.strip().lower(), display_name=display_name, password_hash=password_hash)
    db_session.add(staff)
    db_session.flush()
    for role_code in [c.strip() for c in invitation.role_codes.split(",") if c.strip()]:
        role = db_session.execute(select(Role).where(Role.code == role_code)).scalars().first()
        if role is not None:
            db_session.add(StaffRoleAssignment(staff_user_id=staff.id, role_id=role.id))
    db_session.commit()
    return staff


def create_invitation(email: str, role_codes: list[str], invited_by_staff_user_id: uuid.UUID) -> tuple[StaffInvitation, str]:
    _assert_not_granting_super_admin_unless_actor_is_one(role_codes, invited_by_staff_user_id)
    raw_token = generate_token()
    invitation = StaffInvitation(
        email=email.strip().lower(),
        token_hash=hash_token(raw_token),
        role_codes=",".join(role_codes),
        invited_by_staff_user_id=invited_by_staff_user_id,
        expires_at=utcnow() + timedelta(seconds=INVITATION_TTL_SECONDS),
    )
    db_session.add(invitation)
    db_session.commit()
    audit_record(
        actor_staff_user_id=invited_by_staff_user_id,
        actor_role_snapshot=None,
        action_code="STAFF_INVITATION_CREATED",
        entity_type="staff_invitation",
        entity_public_id=str(invitation.id),
        after_state={"email": invitation.email, "role_codes": invitation.role_codes},
    )
    return invitation, raw_token


def revoke_invitation(invitation: StaffInvitation, actor_staff_user_id: uuid.UUID) -> None:
    invitation.revoked_at = utcnow()
    db_session.commit()
    audit_record(
        actor_staff_user_id=actor_staff_user_id,
        actor_role_snapshot=None,
        action_code="STAFF_INVITATION_REVOKED",
        entity_type="staff_invitation",
        entity_public_id=str(invitation.id),
    )


def assign_roles(staff: StaffUser, role_codes: list[str], actor_staff_user_id: uuid.UUID) -> None:
    _assert_not_granting_super_admin_unless_actor_is_one(role_codes, actor_staff_user_id)
    before = sorted(a.role.code for a in staff.role_assignments)
    for assignment in list(staff.role_assignments):
        db_session.delete(assignment)
    db_session.flush()
    for role_code in role_codes:
        role = db_session.execute(select(Role).where(Role.code == role_code)).scalars().first()
        if role is not None:
            db_session.add(
                StaffRoleAssignment(staff_user_id=staff.id, role_id=role.id, assigned_by_staff_user_id=actor_staff_user_id)
            )
    staff.session_version += 1  # force re-check of every existing session against the new role set
    db_session.commit()
    revoke_all_sessions_for_staff(staff.id, reason="roles_changed")
    audit_record(
        actor_staff_user_id=actor_staff_user_id,
        actor_role_snapshot=None,
        action_code="STAFF_ROLES_CHANGED",
        entity_type="staff_user",
        entity_public_id=str(staff.id),
        before_state={"role_codes": before},
        after_state={"role_codes": sorted(role_codes)},
    )


def disable_staff(staff: StaffUser, reason: str, actor_staff_user_id: uuid.UUID) -> None:
    staff.is_active = False
    staff.disabled_at = utcnow()
    staff.disabled_reason = reason
    staff.session_version += 1
    db_session.commit()
    revoke_all_sessions_for_staff(staff.id, reason="account_disabled")
    audit_record(
        actor_staff_user_id=actor_staff_user_id,
        actor_role_snapshot=None,
        action_code="STAFF_DISABLED",
        entity_type="staff_user",
        entity_public_id=str(staff.id),
        reason=reason,
    )


def reset_mfa(staff: StaffUser, actor_staff_user_id: uuid.UUID) -> None:
    if staff.mfa_credential is not None:
        db_session.delete(staff.mfa_credential)
    staff.session_version += 1
    db_session.commit()
    revoke_all_sessions_for_staff(staff.id, reason="mfa_reset")
    audit_record(
        actor_staff_user_id=actor_staff_user_id,
        actor_role_snapshot=None,
        action_code="STAFF_MFA_RESET",
        entity_type="staff_user",
        entity_public_id=str(staff.id),
    )
