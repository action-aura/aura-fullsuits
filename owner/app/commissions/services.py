"""Phase 9.5A Milestone 22/12 -- CommissionPolicyService.

calculate_commission() is the real, Decimal-exact formula from
docs/owner/phase9_5a/commission-calculation-contract.md, reused unchanged
by both the preview path here (Milestone 22 -- no ledger row written) and
the real posting path a later milestone/phase adds.
"""
from __future__ import annotations

import uuid
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import select

from app.extensions import db_session
from app.models.commissions import CommissionRuleVersion, EmployeeCommissionPlanAssignment


def calculate_commission(rule: CommissionRuleVersion, base_amount: Decimal) -> Decimal:
    if rule.rule_type == "PERCENTAGE_OF_PAYMENT":
        return (base_amount * rule.rate_percentage / Decimal("100")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    if rule.rule_type == "FIXED_AMOUNT":
        return rule.fixed_amount
    raise NotImplementedError(f"rule_type {rule.rule_type} not implemented this phase")


def resolve_active_rule(employee_profile_id: uuid.UUID, as_of: date | None = None) -> CommissionRuleVersion | None:
    as_of = as_of or date.today()
    assignment = db_session.execute(
        select(EmployeeCommissionPlanAssignment)
        .where(
            EmployeeCommissionPlanAssignment.employee_profile_id == employee_profile_id,
            EmployeeCommissionPlanAssignment.effective_from <= as_of,
        )
        .where(
            (EmployeeCommissionPlanAssignment.effective_until.is_(None))
            | (EmployeeCommissionPlanAssignment.effective_until > as_of)
        )
        .order_by(EmployeeCommissionPlanAssignment.effective_from.desc())
    ).scalars().first()
    if assignment is None:
        return None
    return db_session.execute(
        select(CommissionRuleVersion)
        .where(
            CommissionRuleVersion.commission_plan_id == assignment.commission_plan_id,
            CommissionRuleVersion.effective_from <= as_of,
        )
        .where(
            (CommissionRuleVersion.effective_until.is_(None)) | (CommissionRuleVersion.effective_until > as_of)
        )
        .order_by(CommissionRuleVersion.effective_from.desc())
    ).scalars().first()


def preview_commission(employee_profile_id: uuid.UUID, base_amount: Decimal, as_of: date | None = None) -> Decimal | None:
    """Computes the same real formula against a hypothetical amount without
    writing a ledger row -- 'if this deal closes, you'd earn ~X', clearly
    distinct from a posted CommissionLedgerEntry (commission-calculation-
    contract.md). Returns None when the employee has no active commission
    plan assignment or rule (nothing to preview, not an error)."""
    rule = resolve_active_rule(employee_profile_id, as_of)
    if rule is None:
        return None
    return calculate_commission(rule, base_amount)
