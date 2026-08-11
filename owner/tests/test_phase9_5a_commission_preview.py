from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from tests.conftest import make_staff


def _make_profile(app, staff_id, employee_number):
    from app.employees.services import create_employee_profile

    return create_employee_profile(
        {
            "staff_user_id": staff_id,
            "employee_number": employee_number,
            "full_name": f"Employee {employee_number}",
            "employment_start_date": date(2026, 1, 1),
        },
        actor_staff_user_id=staff_id,
    )


def test_preview_commission_returns_none_without_plan_assignment(app, seeded):
    staff_id = make_staff(app, "comm1@example.com")
    with app.app_context():
        from app.commissions.services import preview_commission

        profile = _make_profile(app, staff_id, "EMP-CM1")
        assert preview_commission(profile.id, Decimal("1000.00")) is None


def test_preview_commission_percentage_of_payment(app, seeded):
    staff_id = make_staff(app, "comm2@example.com")
    with app.app_context():
        from app.commissions.services import preview_commission
        from app.extensions import db_session
        from app.models.commissions import CommissionPlan, CommissionRuleVersion, EmployeeCommissionPlanAssignment

        profile = _make_profile(app, staff_id, "EMP-CM2")
        plan = CommissionPlan(plan_code="STANDARD-10PCT", name="Standard 10%")
        db_session.add(plan)
        db_session.flush()
        rule = CommissionRuleVersion(
            commission_plan_id=plan.id, rule_type="PERCENTAGE_OF_PAYMENT", rate_percentage=Decimal("10.000"),
            effective_from=date(2026, 1, 1),
        )
        db_session.add(rule)
        db_session.add(
            EmployeeCommissionPlanAssignment(
                employee_profile_id=profile.id, commission_plan_id=plan.id, effective_from=date(2026, 1, 1),
            )
        )
        db_session.commit()

        result = preview_commission(profile.id, Decimal("1234.56"), as_of=date(2026, 6, 1))
        assert result == Decimal("123.46")  # 10% of 1234.56, ROUND_HALF_UP


def test_preview_commission_fixed_amount(app, seeded):
    staff_id = make_staff(app, "comm3@example.com")
    with app.app_context():
        from app.commissions.services import preview_commission
        from app.extensions import db_session
        from app.models.commissions import CommissionPlan, CommissionRuleVersion, EmployeeCommissionPlanAssignment

        profile = _make_profile(app, staff_id, "EMP-CM3")
        plan = CommissionPlan(plan_code="FLAT-50", name="Flat 50")
        db_session.add(plan)
        db_session.flush()
        rule = CommissionRuleVersion(
            commission_plan_id=plan.id, rule_type="FIXED_AMOUNT", fixed_amount=Decimal("50.00"),
            effective_from=date(2026, 1, 1),
        )
        db_session.add(rule)
        db_session.add(
            EmployeeCommissionPlanAssignment(
                employee_profile_id=profile.id, commission_plan_id=plan.id, effective_from=date(2026, 1, 1),
            )
        )
        db_session.commit()

        assert preview_commission(profile.id, Decimal("9999.00"), as_of=date(2026, 6, 1)) == Decimal("50.00")


def test_calculate_commission_raises_for_unimplemented_rule_type(app, seeded):
    with app.app_context():
        from app.commissions.services import calculate_commission
        from app.models.commissions import CommissionRuleVersion

        rule = CommissionRuleVersion(rule_type="PERCENTAGE_FIRST_SALE", effective_from=date(2026, 1, 1))
        with pytest.raises(NotImplementedError):
            calculate_commission(rule, Decimal("100.00"))
