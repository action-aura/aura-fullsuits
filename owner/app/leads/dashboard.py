"""Phase 9.5C Milestone 14 -- CRM dashboards.

Every metric here is a direct, ownership-scoped COUNT query -- no
unauthorized count leakage: an employee-scoped call never queries beyond
that employee's own created/assigned Leads (the same apply_ownership_
filter() used everywhere else in this phase), and revenue/invoice/
payment/commission metrics are deliberately absent (out of phase scope).
"""
from __future__ import annotations

import uuid
from datetime import timedelta

from sqlalchemy import func, select

from app.extensions import db_session
from app.leads.ownership import apply_ownership_filter
from app.models.base import utcnow
from app.models.customers import Customer
from app.models.employees import EmployeeProfile
from app.models.leads import Lead, LeadFollowup, LeadInteraction


def employee_crm_dashboard(actor_employee_profile_id: uuid.UUID) -> dict:
    now = utcnow()
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    day_end = day_start + timedelta(days=1)

    def own_lead_count(*extra_where):
        stmt = apply_ownership_filter(select(func.count(Lead.id)), Lead, actor_employee_profile_id, all_permission_held=False)
        for clause in extra_where:
            stmt = stmt.where(clause)
        return db_session.execute(stmt).scalar_one()

    return {
        "own_active_leads": own_lead_count(Lead.status.notin_(("CONFIRMED", "LOST", "ARCHIVED"))),
        "new_leads": own_lead_count(Lead.status == "NEW"),
        "potential_leads": own_lead_count(Lead.status == "POTENTIAL"),
        "qualified_leads": own_lead_count(Lead.status == "QUALIFIED"),
        "converted_leads": own_lead_count(Lead.status == "CONFIRMED"),
        "lost_leads": own_lead_count(Lead.status == "LOST"),
        "followups_due_today": db_session.execute(
            select(func.count(LeadFollowup.id)).where(
                LeadFollowup.employee_profile_id == actor_employee_profile_id,
                LeadFollowup.completed_at.is_(None), LeadFollowup.cancelled_at.is_(None),
                LeadFollowup.due_at >= day_start, LeadFollowup.due_at < day_end,
            )
        ).scalar_one(),
        "followups_overdue": db_session.execute(
            select(func.count(LeadFollowup.id)).where(
                LeadFollowup.employee_profile_id == actor_employee_profile_id,
                LeadFollowup.completed_at.is_(None), LeadFollowup.cancelled_at.is_(None),
                LeadFollowup.due_at < now,
            )
        ).scalar_one(),
        "recent_interactions": db_session.execute(
            select(func.count(LeadInteraction.id)).where(
                LeadInteraction.employee_profile_id == actor_employee_profile_id,
                LeadInteraction.occurred_at >= now - timedelta(days=7),
            )
        ).scalar_one(),
    }


def management_crm_dashboard() -> dict:
    now = utcnow()
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    day_end = day_start + timedelta(days=1)

    status_counts = dict(db_session.execute(select(Lead.status, func.count(Lead.id)).group_by(Lead.status)).all())
    by_employee = dict(db_session.execute(
        select(Lead.assigned_employee_profile_id, func.count(Lead.id))
        .where(Lead.assigned_employee_profile_id.isnot(None))
        .group_by(Lead.assigned_employee_profile_id)
    ).all())
    conversions_by_employee = dict(db_session.execute(
        select(Lead.assigned_employee_profile_id, func.count(Lead.id))
        .where(Lead.status == "CONFIRMED", Lead.assigned_employee_profile_id.isnot(None))
        .group_by(Lead.assigned_employee_profile_id)
    ).all())
    unassigned = db_session.execute(
        select(func.count(Lead.id)).where(Lead.assigned_employee_profile_id.is_(None), Lead.status.notin_(("LOST", "ARCHIVED", "CONFIRMED")))
    ).scalar_one()

    # Reassignment-required: active Lead/Customer whose current assignee is
    # no longer ACTIVE -- a query, never an automatic mutation (Milestone 3).
    reassignment_required_leads = db_session.execute(
        select(func.count(Lead.id)).select_from(Lead).join(EmployeeProfile, EmployeeProfile.id == Lead.assigned_employee_profile_id)
        .where(EmployeeProfile.employment_status != "ACTIVE", Lead.status.notin_(("LOST", "ARCHIVED", "CONFIRMED")))
    ).scalar_one()

    followups_due_today = db_session.execute(
        select(func.count(LeadFollowup.id)).where(
            LeadFollowup.completed_at.is_(None), LeadFollowup.cancelled_at.is_(None),
            LeadFollowup.due_at >= day_start, LeadFollowup.due_at < day_end,
        )
    ).scalar_one()
    followups_overdue = db_session.execute(
        select(func.count(LeadFollowup.id)).where(
            LeadFollowup.completed_at.is_(None), LeadFollowup.cancelled_at.is_(None), LeadFollowup.due_at < now,
        )
    ).scalar_one()

    return {
        "total_active_leads": sum(v for k, v in status_counts.items() if k not in ("LOST", "ARCHIVED", "CONFIRMED")),
        "leads_by_status": status_counts,
        "leads_by_employee": {str(k): v for k, v in by_employee.items()},
        "conversions_by_employee": {str(k): v for k, v in conversions_by_employee.items()},
        "unassigned_leads": unassigned,
        "reassignment_required_leads": reassignment_required_leads,
        "followups_due_today": followups_due_today,
        "followups_overdue": followups_overdue,
    }
