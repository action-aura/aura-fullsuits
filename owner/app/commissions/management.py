"""Phase 9.5D Milestone 14 -- CommissionPlan/CommissionRuleVersion/
EmployeeCommissionPlanAssignment management.

Milestone 1's audit classification: MODEL PRESENT, SERVICE MISSING --
app/commissions/services.py already has the real, Decimal-exact
calculate_commission() formula (Phase 9.5A), but nothing anywhere
creates a CommissionPlan, adds a CommissionRuleVersion, or assigns an
employee to one. This module closes that gap, in the SAME existing
app/commissions/ package (not a new module) -- the established home for
commission logic since Phase 9.5A, not app/commercial_sales/ (that
module boundary is for the new Quote/Order/Invoice/Refund document
chain specifically, per Milestone 1's audit).

Non-Negotiable: "do not allow unrestricted employee-entered rates" --
enforced here with real bounds (0 < rate_percentage <= 100,
fixed_amount > 0 with a real currency), independent of whatever
permission gate a route layer adds on top.
See docs/owner/phase9_5d/commission-policy-contract.md,
commission-basis-and-rounding.md.
"""
from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import select

from app.audit.services import record as audit_record
from app.commercial_sales.calculator import validate_currency
from app.commissions.errors import CommissionError
from app.extensions import db_session
from app.models.commissions import COMMISSION_RULE_TYPES, CommissionPlan, CommissionRuleVersion, EmployeeCommissionPlanAssignment


def create_commission_plan(fields: dict, actor_staff_user_id: uuid.UUID) -> CommissionPlan:
    plan = CommissionPlan(
        plan_code=fields["plan_code"],
        name=fields["name"],
        description=fields.get("description"),
        is_active=fields.get("is_active", True),
    )
    db_session.add(plan)
    db_session.commit()

    audit_record(
        actor_staff_user_id=actor_staff_user_id,
        actor_role_snapshot=None,
        action_code="COMMISSION_PLAN_CREATED",
        entity_type="commission_plan",
        entity_public_id=str(plan.id),
        after_state={"plan_code": plan.plan_code, "name": plan.name},
    )
    return plan


def _validate_rule_fields(
    *, rule_type: str, rate_percentage: Decimal | None, fixed_amount: Decimal | None, currency: str | None
) -> None:
    if rule_type not in COMMISSION_RULE_TYPES:
        raise CommissionError("COMMISSION_RULE_TYPE_NOT_IMPLEMENTED", rule_type=rule_type)

    if rule_type in ("PERCENTAGE_OF_PAYMENT", "PERCENTAGE_FIRST_SALE", "PERCENTAGE_RENEWAL"):
        if rate_percentage is None or rate_percentage <= 0 or rate_percentage > 100:
            raise CommissionError("INVALID_COMMISSION_RATE")
    elif rule_type == "FIXED_AMOUNT":
        if fixed_amount is None or fixed_amount <= 0:
            raise CommissionError("INVALID_COMMISSION_FIXED_AMOUNT")
        if not currency:
            raise CommissionError("INVALID_COMMISSION_FIXED_AMOUNT")
        validate_currency(currency)


def create_commission_rule_version(
    plan: CommissionPlan,
    *,
    rule_type: str,
    rate_percentage: Decimal | None = None,
    fixed_amount: Decimal | None = None,
    currency: str | None = None,
    product_id: uuid.UUID | None = None,
    effective_from: date,
    actor_staff_user_id: uuid.UUID,
) -> CommissionRuleVersion:
    """Append-only -- matches PlanPrice's exact pattern (Milestone 1's
    audit precedent): closes the current open rule's effective_until,
    inserts a new row. Historical CommissionLedgerEntry rows pin the
    exact commission_rule_version_id that was actually applied
    (Milestone 15) -- a later rule change never retroactively alters an
    already-earned entry."""
    _validate_rule_fields(rule_type=rule_type, rate_percentage=rate_percentage, fixed_amount=fixed_amount, currency=currency)

    current = db_session.execute(
        select(CommissionRuleVersion).where(
            CommissionRuleVersion.commission_plan_id == plan.id,
            CommissionRuleVersion.product_id == product_id,
            CommissionRuleVersion.effective_until.is_(None),
        )
    ).scalars().first()
    if current is not None:
        current.effective_until = effective_from

    new_rule = CommissionRuleVersion(
        commission_plan_id=plan.id,
        rule_type=rule_type,
        rate_percentage=rate_percentage,
        fixed_amount=fixed_amount,
        currency=currency,
        product_id=product_id,
        effective_from=effective_from,
        created_by_staff_user_id=actor_staff_user_id,
    )
    db_session.add(new_rule)
    db_session.commit()

    audit_record(
        actor_staff_user_id=actor_staff_user_id,
        actor_role_snapshot=None,
        action_code="COMMISSION_RULE_VERSION_CREATED",
        entity_type="commission_rule_version",
        entity_public_id=str(new_rule.id),
        after_state={"commission_plan_id": str(plan.id), "rule_type": rule_type},
    )
    return new_rule


def assign_employee_commission_plan(
    employee_profile_id: uuid.UUID,
    plan: CommissionPlan,
    *,
    effective_from: date,
    actor_staff_user_id: uuid.UUID,
) -> EmployeeCommissionPlanAssignment:
    """Closes any prior open assignment for this employee before opening
    the new one -- an employee has at most one active commission plan
    assignment at any given date (mirrors CommissionRuleVersion's own
    append-only pattern)."""
    current = db_session.execute(
        select(EmployeeCommissionPlanAssignment).where(
            EmployeeCommissionPlanAssignment.employee_profile_id == employee_profile_id,
            EmployeeCommissionPlanAssignment.effective_until.is_(None),
        )
    ).scalars().first()
    if current is not None:
        current.effective_until = effective_from

    assignment = EmployeeCommissionPlanAssignment(
        employee_profile_id=employee_profile_id,
        commission_plan_id=plan.id,
        effective_from=effective_from,
        created_by_staff_user_id=actor_staff_user_id,
    )
    db_session.add(assignment)
    db_session.commit()

    audit_record(
        actor_staff_user_id=actor_staff_user_id,
        actor_role_snapshot=None,
        action_code="EMPLOYEE_COMMISSION_PLAN_ASSIGNED",
        entity_type="employee_commission_plan_assignment",
        entity_public_id=str(assignment.id),
        after_state={"employee_profile_id": str(employee_profile_id), "commission_plan_id": str(plan.id)},
    )
    return assignment
