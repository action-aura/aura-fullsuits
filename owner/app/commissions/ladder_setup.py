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
from app.commissions.ladder import LADDER_CURRENCY, LADDER_RULE_TYPE, MONTHLY_PACKAGES_RULE_TYPE
from app.commissions.management import (
    assign_employee_commission_plan,
    create_commission_plan,
    create_commission_rule_version,
)
from app.extensions import db_session
from app.models.commissions import CommissionPlan, CommissionRuleVersion, EmployeeCommissionPlanAssignment
from app.models.employees import EmployeeProfile

LADDER_PLAN_CODE = "SALES_LADDER"
MONTHLY_PKGS_PLAN_CODE = "SALES_LADDER_MONTHLY_PKGS"
# Owner, 2026-10-26: a seller holds BOTH ladders (Package 1; Packages 2+3).
LADDER_PLAN_CODES = (LADDER_PLAN_CODE, MONTHLY_PKGS_PLAN_CODE)
_PLAN_RULE_TYPES = {LADDER_PLAN_CODE: LADDER_RULE_TYPE, MONTHLY_PKGS_PLAN_CODE: MONTHLY_PACKAGES_RULE_TYPE}
_PLAN_TEXT = {
    LADDER_PLAN_CODE: ("Sales ladder (Package 1)",
                       "Package 1 ladder: 20/50/100/200/350 JOD totals, +30 after the 5th sale; "
                       "10 JOD per renewal; 30-day hold."),
    MONTHLY_PKGS_PLAN_CODE: ("Sales ladder (monthly packages)",
                             "Packages 2 and 3 together: 5/10/20/30/55/65/90/100/115/130 JOD totals, +10 after "
                             "the 10th sale; counted at the first confirmed payment; 30-day hold."),
}


def _seed_one(plan_code: str, *, actor_staff_user_id: uuid.UUID, effective_from: date | None):
    plan = db_session.execute(select(CommissionPlan).where(CommissionPlan.plan_code == plan_code)).scalars().first()
    if plan is not None:
        return plan, False
    name, description = _PLAN_TEXT[plan_code]
    plan = create_commission_plan({"plan_code": plan_code, "name": name, "description": description},
                                  actor_staff_user_id)
    create_commission_rule_version(plan, rule_type=_PLAN_RULE_TYPES[plan_code], currency=LADDER_CURRENCY,
                                   effective_from=effective_from or date(2026, 1, 1),
                                   actor_staff_user_id=actor_staff_user_id)
    return plan, True


def seed_ladder_plan(*, actor_staff_user_id: uuid.UUID, effective_from: date | None = None) -> tuple[CommissionPlan, bool]:
    """(plan, created) for the Package 1 ladder; also makes sure the
    monthly-packages ladder plan exists (seed_ladder_plans reports both).
    An existing plan is returned untouched."""
    return seed_ladder_plans(actor_staff_user_id=actor_staff_user_id, effective_from=effective_from)[0]


def seed_ladder_plans(*, actor_staff_user_id: uuid.UUID, effective_from: date | None = None
                      ) -> list[tuple[CommissionPlan, bool]]:
    """[(plan, created)] for both ladder plans, Package 1 first."""
    out = []
    for code in LADDER_PLAN_CODES:
        plan, created = _seed_one(code, actor_staff_user_id=actor_staff_user_id, effective_from=effective_from)
        out.append((plan, created))
    return out


def _plan_with_rule(plan_code: str, effective_from: date) -> CommissionPlan:
    plan = db_session.execute(select(CommissionPlan).where(CommissionPlan.plan_code == plan_code)).scalars().first()
    if plan is None:
        raise ValueError(f"Run `flask commissions seed-ladder-plan` first: the {plan_code} plan does not exist.")
    # The SAME predicate services.resolve_active_rules uses to find the rule a seller's
    # sales will really be paid under: started on or before the date, not yet closed
    # on it, newest first. The old query had no ORDER BY and only compared
    # effective_from, so it could accept a date no version covered (a plan whose
    # rule was closed, or a gap between versions) -- the seller would then earn 0
    # JOD, exactly what this guard exists to stop -- or reject a date an older
    # version did cover (verifier F6).
    rule = db_session.execute(
        select(CommissionRuleVersion)
        .where(CommissionRuleVersion.commission_plan_id == plan.id,
               CommissionRuleVersion.rule_type == _PLAN_RULE_TYPES[plan_code],
               CommissionRuleVersion.effective_from <= effective_from)
        .where((CommissionRuleVersion.effective_until.is_(None)) | (CommissionRuleVersion.effective_until > effective_from))
        .order_by(CommissionRuleVersion.effective_from.desc())
    ).scalars().first()
    if rule is None:
        raise ValueError(f"The {plan_code} plan has no ladder rule in force on that date.")
    return plan


def assign_seller_to_ladder(staff_user_id: uuid.UUID, *, effective_from: date, actor_staff_user_id: uuid.UUID
                            ) -> EmployeeCommissionPlanAssignment:
    """Puts the seller on BOTH ladders (one action). Whatever non-ladder plan
    they hold is closed once; a ladder they already hold is left alone, and
    the second ladder never closes the first. Returns the Package 1 ladder
    assignment (existing or new)."""
    plans = [_plan_with_rule(code, effective_from) for code in LADDER_PLAN_CODES]
    profile = db_session.execute(
        select(EmployeeProfile).where(EmployeeProfile.staff_user_id == staff_user_id)
    ).scalars().first()
    if profile is None:
        raise ValueError("That staff user has no employee profile; sellers are employees.")
    held = current_plan_codes(profile.id, effective_from)
    first_assignment_of_the_run = True
    result = None
    for plan in plans:
        if plan.plan_code in held:
            if plan.plan_code == LADDER_PLAN_CODE:
                result = current_plan_assignment_for(profile.id, plan.id, effective_from)
            continue
        if first_assignment_of_the_run:
            _close_non_ladder_assignments(profile.id, effective_from)
            first_assignment_of_the_run = False
        assignment = assign_employee_commission_plan(profile.id, plan, effective_from=effective_from,
                                                     actor_staff_user_id=actor_staff_user_id, close_prior=False)
        if plan.plan_code == LADDER_PLAN_CODE:
            result = assignment
    return result


def _close_non_ladder_assignments(profile_id: uuid.UUID, effective_from: date) -> None:
    rows = db_session.execute(
        select(EmployeeCommissionPlanAssignment, CommissionPlan)
        .join(CommissionPlan, CommissionPlan.id == EmployeeCommissionPlanAssignment.commission_plan_id)
        .where(EmployeeCommissionPlanAssignment.employee_profile_id == profile_id,
               EmployeeCommissionPlanAssignment.effective_until.is_(None))
    ).all()
    for assignment, plan in rows:
        if plan.plan_code not in LADDER_PLAN_CODES:
            assignment.effective_until = effective_from
    db_session.flush()


def current_plan_codes(employee_profile_id: uuid.UUID, on: date) -> list[str]:
    """Plan codes of every assignment in force for the employee on `on`."""
    return list(db_session.execute(
        select(CommissionPlan.plan_code)
        .join(EmployeeCommissionPlanAssignment, CommissionPlan.id == EmployeeCommissionPlanAssignment.commission_plan_id)
        .where(
            EmployeeCommissionPlanAssignment.employee_profile_id == employee_profile_id,
            EmployeeCommissionPlanAssignment.effective_from <= on,
            (EmployeeCommissionPlanAssignment.effective_until.is_(None))
            | (EmployeeCommissionPlanAssignment.effective_until > on),
        )
        .order_by(EmployeeCommissionPlanAssignment.effective_from.desc(), CommissionPlan.plan_code)
    ).scalars().all())


def current_plan_assignment_for(employee_profile_id: uuid.UUID, plan_id: uuid.UUID, on: date):
    return db_session.execute(
        select(EmployeeCommissionPlanAssignment).where(
            EmployeeCommissionPlanAssignment.employee_profile_id == employee_profile_id,
            EmployeeCommissionPlanAssignment.commission_plan_id == plan_id,
            EmployeeCommissionPlanAssignment.effective_from <= on,
            (EmployeeCommissionPlanAssignment.effective_until.is_(None))
            | (EmployeeCommissionPlanAssignment.effective_until > on),
        )
    ).scalars().first()


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
    the employee already holds BOTH ladders returns the existing assignment
    instead of closing and re-opening them (which would reset the start date
    of a seller already earning). One press assigns both."""
    held = current_plan_codes(profile.id, effective_from)
    if all(code in held for code in LADDER_PLAN_CODES):
        plan = db_session.execute(
            select(CommissionPlan).where(CommissionPlan.plan_code == LADDER_PLAN_CODE)).scalars().one()
        return current_plan_assignment_for(profile.id, plan.id, effective_from), False
    created = assign_seller_to_ladder(profile.staff_user_id, effective_from=effective_from,
                                      actor_staff_user_id=actor_staff_user_id)
    # Also on the employee's own audit timeline (the assignment row's audit
    # entry, written by management.py, is filed under the assignment).
    audit_record(
        actor_staff_user_id=actor_staff_user_id, actor_role_snapshot=None,
        action_code="EMPLOYEE_COMMISSION_PLAN_ASSIGNED", entity_type="employee_profile",
        entity_public_id=str(profile.id), after_state={"commission_plan_codes": list(LADDER_PLAN_CODES),
                                                       "effective_from": effective_from.isoformat()},
    )
    return created, True
