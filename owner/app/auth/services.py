"""Login/authentication service logic (Part E), separate from route glue so the
future activation API or a CLI can reuse it without touching Flask routing."""
from __future__ import annotations

from sqlalchemy import select

from app.extensions import db_session
from app.models.base import utcnow
from app.models.staff import Role, StaffRoleAssignment, StaffUser
from app.security.passwords import verify_password
from app.security.ratelimit import is_locked_out, record_attempt

MAX_ATTEMPTS_DEFAULT = 5
LOCKOUT_SECONDS_DEFAULT = 900


# OWNER-SEC item 5 (review D SEC-08): mfa_required defaults to False in the
# schema, so every account not created through the invitation flow (or the
# bootstrap CLI) was password-only even when its role can issue licences,
# suspend shops or touch money. Rather than a data migration, login derives
# the requirement: an account is MFA-mandatory if its stored flag says so, OR
# it is a super admin, OR it holds one of these roles, OR any of its roles
# grants one of these capabilities. Read-only/support style roles stay
# optional. "Mandatory" only changes the next login: the existing
# enrol-then-verify flow runs, nobody is refused.
MFA_MANDATORY_ROLE_CODES = frozenset({"SUPER_ADMIN", "ADMIN", "FINANCE", "SALES"})
MFA_MANDATORY_PERMISSION_CODES = frozenset({
    "licenses.issue", "licenses.suspend", "licenses.revoke", "licenses.replace", "licenses.reactivate",
    "subscriptions.suspend", "subscriptions.cancel",
    "payments.create", "payments.correct", "payments.confirm", "refunds.approve",
    "staff.create", "staff.assign_roles", "staff.disable", "staff.reset_mfa", "employees.assign_role",
    "signing_keys.manage", "system.restore",
})


def mfa_is_mandatory(staff: StaffUser) -> bool:
    if staff.mfa_required or staff.is_super_admin:
        return True
    from app.security.rbac import get_staff_permission_codes

    role_codes = set(
        db_session.execute(
            select(Role.code)
            .join(StaffRoleAssignment, StaffRoleAssignment.role_id == Role.id)
            .where(StaffRoleAssignment.staff_user_id == staff.id)
        ).scalars().all()
    )
    if role_codes & MFA_MANDATORY_ROLE_CODES:
        return True
    return bool(get_staff_permission_codes(staff) & MFA_MANDATORY_PERMISSION_CODES)


def find_staff_by_email(email: str) -> StaffUser | None:
    stmt = select(StaffUser).where(StaffUser.email == email.strip().lower())
    return db_session.execute(stmt).scalars().first()


def authenticate(
    email: str, password: str, ip_address: str | None, max_attempts: int, lockout_seconds: int
) -> tuple[StaffUser | None, str]:
    """Returns (staff_user_or_None, reason). reason is one of:
    ok / locked_out / invalid_credentials / account_disabled.
    Deliberately does not distinguish "no such email" from "wrong password" in
    the reason string surfaced to the caller for display (Part E)."""
    email_norm = (email or "").strip().lower()
    if is_locked_out(email_norm, ip_address, max_attempts, lockout_seconds):
        record_attempt(email_norm, ip_address, success=False, reason="locked_out")
        return None, "locked_out"

    staff = find_staff_by_email(email_norm)
    if staff is None or not verify_password(password, staff.password_hash):
        record_attempt(email_norm, ip_address, success=False, reason="invalid_credentials")
        return None, "invalid_credentials"

    if not staff.is_active or staff.disabled_at is not None:
        record_attempt(email_norm, ip_address, success=False, reason="account_disabled")
        return None, "account_disabled"

    staff.last_login_at = utcnow()
    db_session.commit()
    record_attempt(email_norm, ip_address, success=True)
    return staff, "ok"
