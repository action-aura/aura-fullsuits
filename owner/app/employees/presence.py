"""Phase 9.5B Milestone 8 -- presence state derivation.

Presence STATE (ONLINE/RECENTLY_ACTIVE/OFFLINE) is derived at query time from
EmployeePresenceSession.last_seen_at, never stored -- matches Phase 9.5A's
own employee-presence-contract.md exactly. Thresholds are the spec's own:
< 2 min ONLINE, 2-15 min RECENTLY_ACTIVE, > 15 min (or revoked, or no
session at all) OFFLINE.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import select

from app.extensions import db_session
from app.models.base import utcnow
from app.models.employees import EmployeePresenceSession

ONLINE_THRESHOLD_SECONDS = 120
RECENTLY_ACTIVE_THRESHOLD_SECONDS = 900

ONLINE = "ONLINE"
RECENTLY_ACTIVE = "RECENTLY_ACTIVE"
OFFLINE = "OFFLINE"


def presence_state(session_row: EmployeePresenceSession | None, *, as_of: datetime | None = None) -> str:
    if session_row is None or session_row.revoked_at is not None:
        return OFFLINE
    as_of = as_of or utcnow()
    elapsed_seconds = (as_of - session_row.last_seen_at).total_seconds()
    if elapsed_seconds < ONLINE_THRESHOLD_SECONDS:
        return ONLINE
    if elapsed_seconds < RECENTLY_ACTIVE_THRESHOLD_SECONDS:
        return RECENTLY_ACTIVE
    return OFFLINE


def current_presence_session(employee_profile_id: uuid.UUID) -> EmployeePresenceSession | None:
    """Most recently active, non-revoked presence session across every
    device/app-instance -- an employee's overall presence is the most
    recent of however many concurrent sessions they have."""
    stmt = (
        select(EmployeePresenceSession)
        .where(
            EmployeePresenceSession.employee_profile_id == employee_profile_id,
            EmployeePresenceSession.revoked_at.is_(None),
        )
        .order_by(EmployeePresenceSession.last_seen_at.desc())
    )
    return db_session.execute(stmt).scalars().first()


def employee_presence_state(employee_profile_id: uuid.UUID, *, as_of: datetime | None = None) -> str:
    return presence_state(current_presence_session(employee_profile_id), as_of=as_of)


def bulk_presence_states(employee_profile_ids: list[uuid.UUID], *, as_of: datetime | None = None) -> dict:
    """One query for a whole list screen -- avoids N+1 (Milestone 5/19's own
    performance requirement). Returns {employee_profile_id: state}."""
    if not employee_profile_ids:
        return {}
    stmt = select(EmployeePresenceSession).where(
        EmployeePresenceSession.employee_profile_id.in_(employee_profile_ids),
        EmployeePresenceSession.revoked_at.is_(None),
    )
    rows = db_session.execute(stmt).scalars().all()
    latest_by_employee: dict[uuid.UUID, EmployeePresenceSession] = {}
    for row in rows:
        current = latest_by_employee.get(row.employee_profile_id)
        if current is None or row.last_seen_at > current.last_seen_at:
            latest_by_employee[row.employee_profile_id] = row
    return {
        emp_id: presence_state(latest_by_employee.get(emp_id), as_of=as_of)
        for emp_id in employee_profile_ids
    }
