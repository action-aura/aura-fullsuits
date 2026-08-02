"""Phase 9.5D Milestone 14 -- commission plan/rule/assignment management
tests."""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest

from tests.conftest import make_staff

_employee_number_counter = iter(range(1, 100000))


def _seed_employee(app, email):
    from app.employees.services import create_employee_profile

    staff_id = make_staff(app, email, role_codes=["SALES"])
    with app.app_context():
        profile = create_employee_profile(
            {
                "staff_user_id": staff_id,
                "employee_number": f"EMP-{next(_employee_number_counter):05d}",
                "full_name": f"Staff {email}",
                "employment_start_date": date(2026, 1, 1),
            },
            actor_staff_user_id=staff_id,
        )
        return staff_id, profile.id


def test_create_commission_plan(app, seeded):
    staff_id, _ = _seed_employee(app, "cma@example.com")
    with app.app_context():
        from app.commissions.management import create_commission_plan

        plan = create_commission_plan({"plan_code": "STANDARD_SALES", "name": "Standard Sales Plan"}, staff_id)
        assert plan.plan_code == "STANDARD_SALES"
        assert plan.is_active is True


def test_create_percentage_rule(app, seeded):
    staff_id, _ = _seed_employee(app, "cmb@example.com")
    with app.app_context():
        from app.commissions.management import create_commission_plan, create_commission_rule_version

        plan = create_commission_plan({"plan_code": "PCT_PLAN", "name": "Percentage Plan"}, staff_id)
        rule = create_commission_rule_version(
            plan, rule_type="PERCENTAGE_OF_PAYMENT", rate_percentage=Decimal("5.0"),
            effective_from=date.today(), actor_staff_user_id=staff_id,
        )
        assert rule.rate_percentage == Decimal("5.0")
        assert rule.effective_until is None


def test_invalid_percentage_rate_rejected(app, seeded):
    staff_id, _ = _seed_employee(app, "cmc@example.com")
    with app.app_context():
        from app.commissions.errors import CommissionError
        from app.commissions.management import create_commission_plan, create_commission_rule_version

        plan = create_commission_plan({"plan_code": "BAD_PCT_PLAN", "name": "Bad Percentage Plan"}, staff_id)
        with pytest.raises(CommissionError) as exc:
            create_commission_rule_version(
                plan, rule_type="PERCENTAGE_OF_PAYMENT", rate_percentage=Decimal("150.0"),
                effective_from=date.today(), actor_staff_user_id=staff_id,
            )
        assert exc.value.code == "INVALID_COMMISSION_RATE"


def test_zero_rate_rejected(app, seeded):
    staff_id, _ = _seed_employee(app, "cmd@example.com")
    with app.app_context():
        from app.commissions.errors import CommissionError
        from app.commissions.management import create_commission_plan, create_commission_rule_version

        plan = create_commission_plan({"plan_code": "ZERO_PCT_PLAN", "name": "Zero Percentage Plan"}, staff_id)
        with pytest.raises(CommissionError) as exc:
            create_commission_rule_version(
                plan, rule_type="PERCENTAGE_OF_PAYMENT", rate_percentage=Decimal("0"),
                effective_from=date.today(), actor_staff_user_id=staff_id,
            )
        assert exc.value.code == "INVALID_COMMISSION_RATE"


def test_fixed_amount_rule_requires_currency(app, seeded):
    staff_id, _ = _seed_employee(app, "cme@example.com")
    with app.app_context():
        from app.commissions.errors import CommissionError
        from app.commissions.management import create_commission_plan, create_commission_rule_version

        plan = create_commission_plan({"plan_code": "FIXED_PLAN", "name": "Fixed Plan"}, staff_id)
        with pytest.raises(CommissionError) as exc:
            create_commission_rule_version(
                plan, rule_type="FIXED_AMOUNT", fixed_amount=Decimal("50.00"), currency=None,
                effective_from=date.today(), actor_staff_user_id=staff_id,
            )
        assert exc.value.code == "INVALID_COMMISSION_FIXED_AMOUNT"


def test_unimplemented_rule_type_rejected_at_creation(app, seeded):
    """PERCENTAGE_FIRST_SALE/PERCENTAGE_RENEWAL creation is intentionally
    still allowed as reserved configuration (the schema supports it) --
    only an actually-unknown rule_type string is rejected here."""
    staff_id, _ = _seed_employee(app, "cmf@example.com")
    with app.app_context():
        from app.commissions.errors import CommissionError
        from app.commissions.management import create_commission_plan, create_commission_rule_version

        plan = create_commission_plan({"plan_code": "UNKNOWN_TYPE_PLAN", "name": "Unknown Type Plan"}, staff_id)
        with pytest.raises(CommissionError) as exc:
            create_commission_rule_version(
                plan, rule_type="NOT_A_REAL_RULE_TYPE", effective_from=date.today(), actor_staff_user_id=staff_id,
            )
        assert exc.value.code == "COMMISSION_RULE_TYPE_NOT_IMPLEMENTED"


def test_rule_version_is_append_only(app, seeded):
    staff_id, _ = _seed_employee(app, "cmg@example.com")
    with app.app_context():
        from app.commissions.management import create_commission_plan, create_commission_rule_version

        plan = create_commission_plan({"plan_code": "APPEND_PLAN", "name": "Append Plan"}, staff_id)
        first = create_commission_rule_version(
            plan, rule_type="PERCENTAGE_OF_PAYMENT", rate_percentage=Decimal("5.0"),
            effective_from=date.today() - timedelta(days=30), actor_staff_user_id=staff_id,
        )
        second = create_commission_rule_version(
            plan, rule_type="PERCENTAGE_OF_PAYMENT", rate_percentage=Decimal("7.5"),
            effective_from=date.today(), actor_staff_user_id=staff_id,
        )

        assert first.effective_until == date.today()
        assert second.effective_until is None
        # First row's own rate is untouched -- a rate change never
        # retroactively alters a prior version.
        assert first.rate_percentage == Decimal("5.0")


def test_assign_employee_commission_plan_and_resolve(app, seeded):
    staff_id, profile_id = _seed_employee(app, "cmh@example.com")
    with app.app_context():
        from app.commissions.management import assign_employee_commission_plan, create_commission_plan, create_commission_rule_version
        from app.commissions.services import calculate_commission, resolve_active_rule

        plan = create_commission_plan({"plan_code": "ASSIGN_PLAN", "name": "Assign Plan"}, staff_id)
        create_commission_rule_version(
            plan, rule_type="PERCENTAGE_OF_PAYMENT", rate_percentage=Decimal("10.0"),
            effective_from=date.today() - timedelta(days=1), actor_staff_user_id=staff_id,
        )
        assign_employee_commission_plan(profile_id, plan, effective_from=date.today() - timedelta(days=1), actor_staff_user_id=staff_id)

        rule = resolve_active_rule(profile_id)
        assert rule is not None
        assert calculate_commission(rule, Decimal("1000.00")) == Decimal("100.00")


def test_reassigning_plan_closes_prior_assignment(app, seeded):
    staff_id, profile_id = _seed_employee(app, "cmi@example.com")
    with app.app_context():
        from app.commissions.management import assign_employee_commission_plan, create_commission_plan
        from app.extensions import db_session
        from app.models.commissions import EmployeeCommissionPlanAssignment

        plan_a = create_commission_plan({"plan_code": "REASSIGN_A", "name": "Plan A"}, staff_id)
        plan_b = create_commission_plan({"plan_code": "REASSIGN_B", "name": "Plan B"}, staff_id)

        first = assign_employee_commission_plan(profile_id, plan_a, effective_from=date.today() - timedelta(days=10), actor_staff_user_id=staff_id)
        second = assign_employee_commission_plan(profile_id, plan_b, effective_from=date.today(), actor_staff_user_id=staff_id)

        db_session.refresh(first)
        assert first.effective_until == date.today()
        assert second.effective_until is None

        all_assignments = db_session.query(EmployeeCommissionPlanAssignment).filter_by(employee_profile_id=profile_id).count()
        assert all_assignments == 2  # both retained, never deleted
