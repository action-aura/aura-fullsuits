"""Staff management services (Part E/G/H)."""
from __future__ import annotations

import json
import uuid
from datetime import date, timedelta

from sqlalchemy import select

from app.audit.services import record as audit_record
from app.auth.session import revoke_all_sessions_for_staff
from app.extensions import db_session
from app.models.base import utcnow
from app.models.employees import EmployeeProfile
from app.models.staff import Role, StaffInvitation, StaffRoleAssignment, StaffUser
from app.security.passwords import hash_password
from app.security.super_admin_guard import assert_can_remove_super_admin_status
from app.security.tokens import generate_token, hash_token

INVITATION_TTL_SECONDS = 60 * 60 * 24 * 3  # 3 days

# Phase 9.5B Milestone 3 -- employee-profile draft fields carried on a
# StaffInvitation, materialized into a real EmployeeProfile transactionally
# on acceptance. See docs/owner/phase9_5b/employee-onboarding-security-contract.md.
_EMPLOYEE_DRAFT_DATE_FIELDS = ("employment_start_date",)
_EMPLOYEE_DRAFT_UUID_FIELDS = ("manager_employee_profile_id", "commission_plan_id")


class EmployeeDraftValidationError(ValueError):
    """Raised when the employee-profile fields supplied to
    create_employee_invitation() fail validation before the invitation is
    even created (fail fast) -- the real, authoritative guard is still the
    DB-level UNIQUE constraint on employee_number, checked again at
    acceptance time inside the one atomic creation transaction."""


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


def _validate_employee_draft(employee_fields: dict) -> None:
    if not employee_fields.get("employee_number", "").strip():
        raise EmployeeDraftValidationError("employee_number is required")
    if not employee_fields.get("full_name", "").strip():
        raise EmployeeDraftValidationError("full_name is required")
    if not employee_fields.get("employment_start_date"):
        raise EmployeeDraftValidationError("employment_start_date is required")
    employee_number = employee_fields["employee_number"].strip()
    existing = db_session.execute(
        select(EmployeeProfile).where(EmployeeProfile.employee_number == employee_number)
    ).scalars().first()
    if existing is not None:
        raise EmployeeDraftValidationError(f"employee_number {employee_number!r} is already in use")
    # A materialized EmployeeProfile only exists after acceptance -- also
    # check every still-open invitation's own draft, so two concurrent
    # invitations can never race to the same employee_number before either
    # is accepted.
    open_invitations = db_session.execute(
        select(StaffInvitation).where(
            StaffInvitation.accepted_at.is_(None),
            StaffInvitation.revoked_at.is_(None),
            StaffInvitation.expires_at > utcnow(),
            StaffInvitation.employee_profile_draft.is_not(None),
        )
    ).scalars().all()
    for inv in open_invitations:
        try:
            if json.loads(inv.employee_profile_draft).get("employee_number") == employee_number:
                raise EmployeeDraftValidationError(f"employee_number {employee_number!r} is already reserved by a pending invitation")
        except json.JSONDecodeError:
            continue
    manager_id = employee_fields.get("manager_employee_profile_id")
    if manager_id and db_session.get(EmployeeProfile, manager_id) is None:
        raise EmployeeDraftValidationError("manager_employee_profile_id does not reference a real employee profile")
    commission_plan_id = employee_fields.get("commission_plan_id")
    if commission_plan_id:
        from app.models.commissions import CommissionPlan

        if db_session.get(CommissionPlan, commission_plan_id) is None:
            raise EmployeeDraftValidationError("commission_plan_id does not reference a real commission plan")


def create_employee_invitation(
    email: str,
    role_codes: list[str],
    employee_fields: dict,
    invited_by_staff_user_id: uuid.UUID,
    *,
    mfa_required: bool = True,
) -> tuple[StaffInvitation, str]:
    """Extends create_invitation() with an employee-profile draft, carried on
    the SAME real StaffInvitation row (no second setup-token mechanism --
    see docs/owner/phase9_5b/phase9-5a-evidence-reuse-decision.md) and
    materialized into a real EmployeeProfile transactionally on acceptance
    (create_staff_from_invitation). Defaults mfa_required=True per the
    governing spec's own recommended policy for newly created internal staff."""
    _validate_employee_draft(employee_fields)
    invitation, raw_token = create_invitation(email, role_codes, invited_by_staff_user_id)
    draft = dict(employee_fields)
    draft["mfa_required"] = mfa_required
    invitation.employee_profile_draft = json.dumps(draft, default=str)
    db_session.commit()
    return invitation, raw_token


def create_staff_from_invitation(
    invitation: StaffInvitation, display_name: str, password_hash: str
) -> tuple[StaffUser, EmployeeProfile | None]:
    """One atomic transaction: StaffUser + role assignments + (when a draft
    is present) EmployeeProfile, all committed together or none at all
    (Non-Negotiable Principle 3) -- a single db_session.commit() at the end,
    nothing committed before it, so any exception mid-function (e.g. a
    duplicate employee_number that slipped past the fail-fast check in
    create_employee_invitation()) rolls back the whole thing."""
    staff = StaffUser(email=invitation.email.strip().lower(), display_name=display_name, password_hash=password_hash)
    db_session.add(staff)
    db_session.flush()
    for role_code in [c.strip() for c in invitation.role_codes.split(",") if c.strip()]:
        role = db_session.execute(select(Role).where(Role.code == role_code)).scalars().first()
        if role is not None:
            db_session.add(StaffRoleAssignment(staff_user_id=staff.id, role_id=role.id))

    profile = None
    if invitation.employee_profile_draft:
        draft = json.loads(invitation.employee_profile_draft)
        staff.mfa_required = bool(draft.pop("mfa_required", True))
        for field in _EMPLOYEE_DRAFT_DATE_FIELDS:
            if draft.get(field):
                draft[field] = date.fromisoformat(draft[field])
        for field in _EMPLOYEE_DRAFT_UUID_FIELDS:
            if draft.get(field):
                draft[field] = uuid.UUID(draft[field])
        profile = EmployeeProfile(
            staff_user_id=staff.id,
            **draft,
            created_by_staff_user_id=invitation.invited_by_staff_user_id,
            updated_by_staff_user_id=invitation.invited_by_staff_user_id,
        )
        db_session.add(profile)

    db_session.commit()
    return staff, profile


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


def reissue_employee_invitation(invitation: StaffInvitation, actor_staff_user_id: uuid.UUID) -> tuple[StaffInvitation, str]:
    """Real fix for the governing spec's 'reissue setup credential' action:
    a PENDING EmployeeProfile already has a working password (the profile is
    only materialized transactionally on invitation *acceptance* -- by
    definition it already has one). The real 'my setup link is lost/expired'
    case has NO EmployeeProfile yet at all, because nothing was ever
    accepted -- so reissue operates on the open StaffInvitation itself
    (revoke the stale one, create a fresh one with the same email/roles/
    employee-profile draft), never on an employee_id."""
    if invitation.accepted_at is not None:
        raise ValueError("This invitation was already accepted -- nothing to reissue.")
    revoke_invitation(invitation, actor_staff_user_id)
    role_codes = [c.strip() for c in invitation.role_codes.split(",") if c.strip()]
    if invitation.employee_profile_draft:
        draft = json.loads(invitation.employee_profile_draft)
        mfa_required = draft.pop("mfa_required", True)
        return create_employee_invitation(invitation.email, role_codes, draft, actor_staff_user_id, mfa_required=mfa_required)
    return create_invitation(invitation.email, role_codes, actor_staff_user_id)


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
    assert_can_remove_super_admin_status(staff.id)
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
