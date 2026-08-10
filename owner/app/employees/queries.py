"""Phase 9.5B Milestone 5 -- employee list/detail read queries.

Presence filtering is expressed as a SQL EXISTS clause (mirrors
app.employees.presence.presence_state()'s own threshold logic exactly) so
filtering by presence still happens at the database level, before
pagination -- never a Python-side filter over an unbounded fetch (would
break both the anti-enumeration pagination discipline and Milestone 19's
own query-performance requirement at real scale).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta

from sqlalchemy import Select, exists, or_, select

from app.employees.presence import ONLINE_THRESHOLD_SECONDS, RECENTLY_ACTIVE_THRESHOLD_SECONDS
from app.extensions import db_session
from app.models.base import utcnow
from app.models.employees import EmployeeProfile, EmployeePresenceSession
from app.models.staff import Role, StaffRoleAssignment, StaffUser
from app.services.pagination import DEFAULT_PAGE_SIZE, paginate


def find_own_profile(staff_user_id: uuid.UUID) -> EmployeeProfile | None:
    stmt = select(EmployeeProfile).where(EmployeeProfile.staff_user_id == staff_user_id)
    return db_session.execute(stmt).scalars().first()


def _presence_filter_clause(state: str, as_of: datetime):
    online_cutoff = as_of - timedelta(seconds=ONLINE_THRESHOLD_SECONDS)
    recently_cutoff = as_of - timedelta(seconds=RECENTLY_ACTIVE_THRESHOLD_SECONDS)

    online = exists().where(
        EmployeePresenceSession.employee_profile_id == EmployeeProfile.id,
        EmployeePresenceSession.revoked_at.is_(None),
        EmployeePresenceSession.last_seen_at >= online_cutoff,
    )
    recently_active_only = exists().where(
        EmployeePresenceSession.employee_profile_id == EmployeeProfile.id,
        EmployeePresenceSession.revoked_at.is_(None),
        EmployeePresenceSession.last_seen_at >= recently_cutoff,
        EmployeePresenceSession.last_seen_at < online_cutoff,
    )
    any_recent = exists().where(
        EmployeePresenceSession.employee_profile_id == EmployeeProfile.id,
        EmployeePresenceSession.revoked_at.is_(None),
        EmployeePresenceSession.last_seen_at >= recently_cutoff,
    )
    if state == "ONLINE":
        return online
    if state == "RECENTLY_ACTIVE":
        return recently_active_only
    if state == "OFFLINE":
        return ~any_recent
    raise ValueError(f"unknown presence state filter: {state!r}")


def list_employees(
    *,
    page: int = 1,
    page_size: int = DEFAULT_PAGE_SIZE,
    search: str | None = None,
    department: str | None = None,
    employment_status: str | None = None,
    role_code: str | None = None,
    manager_employee_profile_id: uuid.UUID | None = None,
    presence: str | None = None,
    include_archived: bool = False,
    sort: str = "full_name",
) -> dict:
    stmt: Select = select(EmployeeProfile)
    if not include_archived:
        stmt = stmt.where(EmployeeProfile.employment_status != "ARCHIVED")
    if search:
        like = f"%{search.strip()}%"
        stmt = stmt.where(or_(EmployeeProfile.full_name.ilike(like), EmployeeProfile.employee_number.ilike(like)))
    if department:
        stmt = stmt.where(EmployeeProfile.department == department)
    if employment_status:
        stmt = stmt.where(EmployeeProfile.employment_status == employment_status)
    if manager_employee_profile_id:
        stmt = stmt.where(EmployeeProfile.manager_employee_profile_id == manager_employee_profile_id)
    if role_code:
        role_staff_ids = select(StaffRoleAssignment.staff_user_id).join(
            Role, Role.id == StaffRoleAssignment.role_id
        ).where(Role.code == role_code)
        stmt = stmt.where(EmployeeProfile.staff_user_id.in_(role_staff_ids))
    if presence:
        stmt = stmt.where(_presence_filter_clause(presence, utcnow()))

    sort_columns = {
        "full_name": EmployeeProfile.full_name,
        "employee_number": EmployeeProfile.employee_number,
        "employment_start_date": EmployeeProfile.employment_start_date,
        "employment_status": EmployeeProfile.employment_status,
    }
    stmt = stmt.order_by(sort_columns.get(sort, EmployeeProfile.full_name), EmployeeProfile.id)

    return paginate(stmt, page, page_size)
