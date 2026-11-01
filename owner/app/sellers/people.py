"""Who is a seller, and who holds a permission -- the two questions every rule here asks."""
from __future__ import annotations

from sqlalchemy import select

from app.contracts.service import staff_is_bound_by_seller_agreement
from app.extensions import db_session
from app.models.staff import StaffUser
from app.security.rbac import get_staff_permission_codes


def is_seller(staff: StaffUser | None) -> bool:
    """The people the seller agreement binds: the SALES role, never a Super Admin
    (contracts/service.py documents why a manager on their own is not a seller)."""
    return staff_is_bound_by_seller_agreement(staff)


def active_staff_holding(permission_code: str) -> list[StaffUser]:
    """Every working staff account that holds `permission_code` (Super Admins hold all of them)."""
    rows = db_session.execute(
        select(StaffUser).where(StaffUser.is_active.is_(True), StaffUser.disabled_at.is_(None)).order_by(StaffUser.email)
    ).scalars().all()
    return [staff for staff in rows if permission_code in get_staff_permission_codes(staff)]
