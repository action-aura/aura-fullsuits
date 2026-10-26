"""Step 0 (2026-10-24) -- putting sellers on the owner's monthly ladder.

The ladder is an ordinary commission plan whose rule is MONTHLY_LADDER
(app/commissions/ladder.py); a seller moves to it by an ordinary plan
assignment (management.assign_employee_commission_plan), which closes their
previous plan the day the new one starts. So a percentage seller and a
ladder seller coexist plainly, and the rule in force on the day a payment is
confirmed decides which one pays -- never both (ladder.py).

Used by `flask commissions seed-ladder-plan` / `assign-ladder`. There is no
screen for commission plans yet (management.py has none either).
"""
from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import select

from app.audit.services import record as audit_record
from app.commissions.ladder import LADDER_CURRENCY, LADDER_RULE_TYPE
from app.commissions.management import (
    assign_employee_commission_plan,
    create_commission_plan,
    create_commission_rule_version,
)
from app.extensions import db_session
from app.models.commissions import CommissionPlan, CommissionRuleVersion, EmployeeCommissionPlanAssignment
from app.models.employees import EmployeeProfile

LADDER_PLAN_CODE = "SALES_LADDER"


def seed_ladder_plan(*, actor_staff_user_id: uuid.UUID, effective_from: date | None = None) -> tuple[CommissionPlan, bool]:
    """(plan, created). Creates SALES_LADDER with one MONTHLY_LADDER rule in
    JOD if missing; an existing plan is returned untouched."""
    plan = db_session.execute(select(CommissionPlan).where(CommissionPlan.plan_code == LADDER_PLAN_CODE)).scalars().first()
    if plan is not None:
        return plan, False
    plan = create_commission_plan(
        {"plan_code": LADDER_PLAN_CODE, "name": "Sales ladder (monthly)",
         "description": "The owner's monthly ladder: 20/50/100/200/350 JOD totals, +30 after the 5th sale; "
                        "10 JOD per renewal; 30-day hold."},
        actor_staff_user_id,
    )
    create_commission_rule_version(plan, rule_type=LADDER_RULE_TYPE, currency=LADDER_CURRENCY,
                                   effective_from=effective_from or date(2026, 1, 1),
                                   actor_staff_user_id=actor_staff_user_id)
    return plan, True


def assign_seller_to_ladder(staff_user_id: uuid.UUID, *, effective_from: date, actor_staff_user_id: uuid.UUID
                            ) -> EmployeeCommissionPlanAssignment:
    plan = db_session.execute(select(CommissionPlan).where(CommissionPlan.plan_code == LADDER_PLAN_CODE)).scalars().first()
    if plan is None:
        raise ValueError("Run `flask commissions seed-ladder-plan` first: the SALES_LADDER plan does not exist.")
    rule = db_session.execute(
        select(CommissionRuleVersion).where(CommissionRuleVersion.commission_plan_id == plan.id,
                                            CommissionRuleVersion.rule_type == LADDER_RULE_TYPE)
    ).scalars().first()
    if rule is None or rule.effective_from > effective_from:
        raise ValueError("The SALES_LADDER plan has no ladder rule in force on that date.")
    profile = db_session.execute(
        select(EmployeeProfile).where(EmployeeProfile.staff_user_id == staff_user_id)
    ).scalars().first()
    if profile is None:
        raise ValueError("That staff user has no employee profile; sellers are employees.")
    return assign_employee_commission_plan(profile.id, plan, effective_from=effective_from,
                                           actor_staff_user_id=actor_staff_user_id)


def current_plan_assignment(employee_profile_id: uuid.UUID, on: date):
    """(assignment, plan) in force for the employee on `on`, or (None, None)."""
    row = db_session.execute(
        select(EmployeeCommissionPlanAssignment, CommissionPlan)
        .join(CommissionPlan, CommissionPlan.id == EmployeeCommissionPlanAssignment.commission_plan_id)
        .where(
            EmployeeCommissionPlanAssignment.employee_profile_id == employee_profile_id,
            EmployeeCommissionPlanAssignment.effective_from <= on,
            (EmployeeCommissionPlanAssignment.effective_until.is_(None))
            | (EmployeeCommissionPlanAssignment.effective_until > on),
        )
        .order_by(EmployeeCommissionPlanAssignment.effective_from.desc())
    ).first()
    return (row[0], row[1]) if row else (None, None)


def assign_profile_to_ladder(profile: EmployeeProfile, *, effective_from: date, actor_staff_user_id: uuid.UUID
                             ) -> tuple[EmployeeCommissionPlanAssignment, bool]:
    """(assignment, created). Idempotent for the screen: a second press while
    the employee is already on the ladder returns the existing assignment
    instead of closing it and opening an identical one (which the CLI would
    do, and which would reset the start date of a seller already earning)."""
    assignment, plan = current_plan_assignment(profile.id, effective_from)
    if plan is not None and plan.plan_code == LADDER_PLAN_CODE:
        return assignment, False
    created = assign_seller_to_ladder(profile.staff_user_id, effective_from=effective_from,
                                      actor_staff_user_id=actor_staff_user_id)
    # Also on the employee's own audit timeline (the assignment row's audit
    # entry, written by management.py, is filed under the assignment).
    audit_record(
        actor_staff_user_id=actor_staff_user_id, actor_role_snapshot=None,
        action_code="EMPLOYEE_COMMISSION_PLAN_ASSIGNED", entity_type="employee_profile",
        entity_public_id=str(profile.id), after_state={"commission_plan_code": LADDER_PLAN_CODE,
                                                       "effective_from": effective_from.isoformat()},
    )
    return created, True
