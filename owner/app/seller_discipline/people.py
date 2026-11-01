"""Who counts as a seller here: the same people the seller agreement binds (the SALES role,
contracts.service.staff_is_bound_by_seller_agreement) who also have an employee profile."""
from __future__ import annotations

import uuid

from sqlalchemy import select

from app.contracts import service as contract_service
from app.extensions import db_session
from app.models.employees import EmployeeProfile
from app.models.staff import StaffUser
from app.seller_discipline.errors import DisciplineError

LIVE_STATUSES = ("ACTIVE", "SUSPENDED")


def profile_of(staff_user_id) -> EmployeeProfile | None:
    return db_session.execute(
        select(EmployeeProfile).where(EmployeeProfile.staff_user_id == staff_user_id)
    ).scalars().first()


def load_seller(staff_user_id, *, statuses: tuple[str, ...] = LIVE_STATUSES) -> tuple[StaffUser, EmployeeProfile]:
    """The seller and their profile, or a refusal. Everything is read before anything is written."""
    try:
        staff = db_session.get(StaffUser, uuid.UUID(str(staff_user_id)))
    except (ValueError, TypeError):
        staff = None
    if staff is None or not contract_service.staff_is_bound_by_seller_agreement(staff):
        raise DisciplineError("NOT_A_SELLER")
    profile = profile_of(staff.id)
    if profile is None or profile.employment_status not in statuses:
        raise DisciplineError("SELLER_NOT_CURRENT")
    return staff, profile


def sellers_for_picker() -> list[tuple[StaffUser, EmployeeProfile]]:
    """Current sellers (active or suspended), by name -- for the manager's pickers."""
    rows = db_session.execute(
        select(StaffUser, EmployeeProfile).join(EmployeeProfile, EmployeeProfile.staff_user_id == StaffUser.id)
        .where(EmployeeProfile.employment_status.in_(LIVE_STATUSES), EmployeeProfile.archived_at.is_(None))
        .order_by(EmployeeProfile.full_name)
    ).all()
    return [(staff, profile) for staff, profile in rows if contract_service.staff_is_bound_by_seller_agreement(staff)]


def seller_page_context(staff) -> dict:
    """What a seller's own pages (profile, dashboard) say about clauses 16-17: whether they may end
    their agreement, a running notice, and notices waiting for their reply. A non-seller gets
    {"bound": False} and sees nothing."""
    if staff is None or not contract_service.staff_is_bound_by_seller_agreement(staff):
        return {"bound": False, "ending": None, "awaiting": 0, "may_end": False}
    from app.seller_discipline import sanctions, terminations

    profile = profile_of(staff.id)
    ending = terminations.open_notice_for(staff.id)
    return {
        "bound": True, "ending": ending, "awaiting": sanctions.awaiting_reply_count(staff.id),
        "may_end": ending is None and profile is not None and profile.employment_status == "ACTIVE",
    }
