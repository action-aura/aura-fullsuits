"""Phase 9.5B -- shared last-usable-SUPER_ADMIN protection (Non-Negotiable
Principle 11).

Real gap found during this phase's Milestone 1 audit: `StaffUser.is_super_admin`
(a direct boolean, checked in `app.security.rbac.get_staff_permission_codes` --
it bypasses role-assignment lookup entirely) is the one real authority for
Super Admin status; role assignment to the "SUPER_ADMIN" Role row is
cosmetic/redundant for permissions. Nothing previously blocked disabling the
only usable Super Admin account (`owner/app/staff/services.py::disable_staff`
had no such check). This is the one shared guard every action that could
remove a StaffUser's usable-Super-Admin status must call.
"""
from __future__ import annotations

import uuid

from sqlalchemy import select

from app.extensions import db_session
from app.models.staff import StaffUser


class LastSuperAdminError(ValueError):
    """Raised when an action would leave zero usable Super Admin accounts."""


def usable_super_admin_count(*, exclude_staff_user_id: uuid.UUID | None = None) -> int:
    """'Usable' = is_super_admin AND is_active AND not disabled. Employee-
    profile employment_status is deliberately not consulted -- a Super Admin
    with no EmployeeProfile at all (e.g. the original bootstrap admin) must
    still count."""
    stmt = select(StaffUser).where(
        StaffUser.is_super_admin.is_(True),
        StaffUser.is_active.is_(True),
        StaffUser.disabled_at.is_(None),
    )
    rows = db_session.execute(stmt).scalars().all()
    if exclude_staff_user_id is not None:
        rows = [r for r in rows if r.id != exclude_staff_user_id]
    return len(rows)


def assert_can_remove_super_admin_status(staff_user_id: uuid.UUID) -> None:
    """Call before an action that would make staff_user_id stop being a
    usable Super Admin (disabling the account, or suspending/terminating its
    employee profile). No-op if the account isn't currently a usable Super
    Admin -- this action can't be the one that removes the last one."""
    staff = db_session.get(StaffUser, staff_user_id)
    if staff is None or not staff.is_super_admin or not staff.is_active or staff.disabled_at is not None:
        return
    if usable_super_admin_count(exclude_staff_user_id=staff_user_id) < 1:
        raise LastSuperAdminError(
            "This action would leave zero usable Super Admin accounts. At least one must remain."
        )
