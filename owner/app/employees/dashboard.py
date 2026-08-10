"""Phase 9.5B Milestone 9 -- employee-domain dashboard metrics.

Employee-domain metrics only -- not the future full business dashboard. Every
metric definition below is exact and precise (per the governing spec's own
explicit instruction not to conflate active/online/logged-in-today/employed).
"""
from __future__ import annotations

from sqlalchemy import func, select

from app.employees.presence import ONLINE_THRESHOLD_SECONDS, RECENTLY_ACTIVE_THRESHOLD_SECONDS
from app.extensions import db_session
from app.models.base import utcnow
from app.models.employees import EmployeeProfile, EmployeePresenceSession
from app.models.staff import MfaCredential, StaffUser


def _count(stmt) -> int:
    return db_session.execute(select(func.count()).select_from(stmt.subquery())).scalar_one()


def get_employee_dashboard_metrics() -> dict:
    now = utcnow()
    from datetime import timedelta

    online_cutoff = now - timedelta(seconds=ONLINE_THRESHOLD_SECONDS)
    recently_cutoff = now - timedelta(seconds=RECENTLY_ACTIVE_THRESHOLD_SECONDS)

    total_employees = _count(select(EmployeeProfile))
    by_status = {}
    for status in ("PENDING", "ACTIVE", "SUSPENDED", "TERMINATED", "ARCHIVED"):
        by_status[status] = _count(select(EmployeeProfile).where(EmployeeProfile.employment_status == status))

    online_now = _count(
        select(EmployeePresenceSession.employee_profile_id)
        .where(EmployeePresenceSession.revoked_at.is_(None), EmployeePresenceSession.last_seen_at >= online_cutoff)
        .distinct()
    )
    recently_active = _count(
        select(EmployeePresenceSession.employee_profile_id)
        .where(
            EmployeePresenceSession.revoked_at.is_(None),
            EmployeePresenceSession.last_seen_at >= recently_cutoff,
            EmployeePresenceSession.last_seen_at < online_cutoff,
        )
        .distinct()
    )

    # "Employees without completed MFA": ACTIVE employees whose StaffUser
    # requires MFA but has no confirmed MfaCredential yet.
    mfa_incomplete = _count(
        select(EmployeeProfile)
        .join(StaffUser, StaffUser.id == EmployeeProfile.staff_user_id)
        .outerjoin(MfaCredential, MfaCredential.staff_user_id == StaffUser.id)
        .where(
            EmployeeProfile.employment_status == "ACTIVE",
            StaffUser.mfa_required.is_(True),
            (MfaCredential.id.is_(None)) | (MfaCredential.confirmed.is_(False)),
        )
    )
    setup_pending = by_status["PENDING"]
    locked_accounts = _count(select(StaffUser).where(StaffUser.is_active.is_(False)))

    from app.models.staff import StaffSession

    active_sessions = _count(
        select(StaffSession).where(StaffSession.revoked_at.is_(None), StaffSession.expires_at > now)
    )

    return {
        "total_employees": total_employees,
        "active_employees": by_status["ACTIVE"],
        "pending_employees": by_status["PENDING"],
        "suspended_employees": by_status["SUSPENDED"],
        "terminated_employees": by_status["TERMINATED"],
        "archived_employees": by_status["ARCHIVED"],
        "online_now": online_now,
        "recently_active": recently_active,
        "offline": total_employees - online_now - recently_active,
        "mfa_incomplete": mfa_incomplete,
        "setup_pending": setup_pending,
        "locked_accounts": locked_accounts,
        "active_sessions": active_sessions,
        "generated_at": now,
    }
