"""Who is told that a sale is waiting -- decided when the message is sent.

The owner's rule (2026-10-25): every ACTIVE staff member who could confirm the
sale, except its seller and whoever recorded it; somebody who leaves stops
receiving at once.

"Could confirm" is CONFIRM_SALE_PERMISSIONS (commercial_sales/direct_sale.py)
-- the confirm route's own permission set, not a parallel list (SALES_MANAGER,
FINANCE and SUPER_ADMIN hold it as seeded). "Not the seller, not the recorder"
is the same two-person rule _guard_confirmer enforces at confirm time.

It is asked twice: when the worker turns a sale's outbox row into one
delivery per device (recipients_for_sale), and again for every delivery just
before it is sent (staff_may_confirm_sales): a role removed, an account
disabled or an employee suspended in between means the message is cancelled,
not sent. Deactivation also deletes the person's devices outright
(app/push/subscriptions.py::remove_all_for_staff).
"""
from __future__ import annotations

from sqlalchemy import select

from app.commercial_sales.direct_sale import CONFIRM_SALE_PERMISSIONS
from app.extensions import db_session
from app.models.employees import EmployeeProfile
from app.models.push import PushSubscription
from app.models.staff import StaffUser
from app.security.rbac import get_staff_permission_codes


def staff_may_confirm_sales(staff: StaffUser | None) -> bool:
    if staff is None or not staff.is_active or staff.disabled_at is not None:
        return False
    return CONFIRM_SALE_PERMISSIONS <= get_staff_permission_codes(staff)


def excluded_for_sale(order, payment) -> set:
    """The seller and the recorder: they can never confirm this sale."""
    excluded = {payment.recorded_by_staff_user_id}
    seller = db_session.get(EmployeeProfile, order.created_by_employee_profile_id)
    if seller is not None:
        excluded.add(seller.staff_user_id)
    excluded.discard(None)
    return excluded


def recipients_for_sale(order, payment) -> list[StaffUser]:
    """Everyone who may confirm this sale AND has at least one device that
    turned notifications on. Staff without a device are not rows to make --
    they see the badge instead."""
    excluded = excluded_for_sale(order, payment)
    candidates = db_session.execute(
        select(StaffUser)
        .where(
            StaffUser.is_active.is_(True),
            StaffUser.disabled_at.is_(None),
            StaffUser.id.in_(select(PushSubscription.staff_user_id)),
        )
        .order_by(StaffUser.id)
    ).scalars().all()
    return [staff for staff in candidates if staff.id not in excluded and staff_may_confirm_sales(staff)]
