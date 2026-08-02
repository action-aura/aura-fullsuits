"""Phase 9.5D Milestone 6 -- CommercialApproval service tests, including
integration with the Quote domain's automatic exception-detection."""
from __future__ import annotations

import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest

from tests.conftest import make_staff


def _seed_sales_employee(app, email):
    from app.employees.services import create_employee_profile

    staff_id = make_staff(app, email, role_codes=["SALES"])
    with app.app_context():
        profile = create_employee_profile(
            {
                "staff_user_id": staff_id,
                "employee_number": f"EMP-{email[:6].upper()}",
                "full_name": f"Sales {email}",
                "employment_start_date": date(2026, 1, 1),
            },
            actor_staff_user_id=staff_id,
        )
        return staff_id, profile.id


def _seed_customer(app, staff_id):
    from app.customers.services import create_customer

    with app.app_context():
        customer = create_customer({"legal_name": "Approval Test Customer Co"}, actor_staff_user_id=staff_id)
        return customer.id


def _seed_plan(app, plan_code, price=Decimal("100.00")):
    from app.catalog.services import add_plan_price, create_plan, seed_canonical_catalog
    from app.extensions import db_session
    from app.models.catalog import Product

    with app.app_context():
        seed_canonical_catalog()
        product = Product(product_code=f"PROD_{plan_code}", name="Approval Test Product", is_active=True, is_sellable=True)
        db_session.add(product)
        db_session.flush()
        plan = create_plan(
            {
                "plan_code": plan_code,
                "product_id": product.id,
                "name": "Approval Test Plan",
                "billing_model": "MONTHLY",
                "effective_date": date.today() - timedelta(days=1),
            },
            actor_staff_user_id=None,
        )
        add_plan_price(plan, price, "USD", date.today() - timedelta(days=1), actor_staff_user_id=None)
        return plan.id


def test_normal_line_creates_no_approval(app, seeded):
    staff_id, profile_id = _seed_sales_employee(app, "appa@example.com")
    customer_id = _seed_customer(app, staff_id)
    plan_id = _seed_plan(app, "APA_PLAN")
    with app.app_context():
        from app.commercial_sales.approvals import unresolved_approvals_for_targets
        from app.commercial_sales.quotes import add_quote_line, create_quote

        quote = create_quote({"customer_id": customer_id, "currency": "USD"}, actor_employee_profile_id=profile_id, actor_staff_user_id=staff_id)
        line = add_quote_line(quote, plan_id=plan_id, addon_id=None, quantity=1, actor_staff_user_id=staff_id)

        assert unresolved_approvals_for_targets("QUOTE_LINE", [line.id]) == []


def test_price_override_line_creates_pending_approval(app, seeded):
    staff_id, profile_id = _seed_sales_employee(app, "appb@example.com")
    customer_id = _seed_customer(app, staff_id)
    plan_id = _seed_plan(app, "APB_PLAN")
    with app.app_context():
        from app.commercial_sales.approvals import unresolved_approvals_for_targets
        from app.commercial_sales.quotes import add_quote_line, create_quote

        quote = create_quote({"customer_id": customer_id, "currency": "USD"}, actor_employee_profile_id=profile_id, actor_staff_user_id=staff_id)
        line = add_quote_line(
            quote, plan_id=plan_id, addon_id=None, quantity=1, override_unit_price=Decimal("50.00"),
            override_reason="loyal customer", actor_staff_user_id=staff_id,
        )

        pending = unresolved_approvals_for_targets("QUOTE_LINE", [line.id])
        assert len(pending) == 1
        assert pending[0].reason_code == "PRICE_OVERRIDE"
        assert pending[0].status == "PENDING"


def test_accept_blocked_while_approval_pending(app, seeded):
    staff_id, profile_id = _seed_sales_employee(app, "appc@example.com")
    customer_id = _seed_customer(app, staff_id)
    plan_id = _seed_plan(app, "APC_PLAN")
    with app.app_context():
        from app.commercial_sales.errors import CommercialSalesError
        from app.commercial_sales.quotes import add_quote_line, create_quote, record_customer_decision, submit_quote

        quote = create_quote({"customer_id": customer_id, "currency": "USD"}, actor_employee_profile_id=profile_id, actor_staff_user_id=staff_id)
        add_quote_line(
            quote, plan_id=plan_id, addon_id=None, quantity=1, override_unit_price=Decimal("50.00"),
            override_reason="discount", actor_staff_user_id=staff_id,
        )
        submit_quote(quote, actor_staff_user_id=staff_id)

        with pytest.raises(CommercialSalesError) as exc:
            record_customer_decision(quote, accepted=True, actor_staff_user_id=staff_id)
        assert exc.value.code == "APPROVAL_REQUIRED"


def test_accept_succeeds_after_approval_granted(app, seeded):
    staff_a, profile_a = _seed_sales_employee(app, "appd_a@example.com")
    staff_b, _ = _seed_sales_employee(app, "appd_b@example.com")
    customer_id = _seed_customer(app, staff_a)
    plan_id = _seed_plan(app, "APD_PLAN")
    with app.app_context():
        from app.commercial_sales.approvals import decide_approval, unresolved_approvals_for_targets
        from app.commercial_sales.quotes import add_quote_line, create_quote, record_customer_decision, submit_quote

        quote = create_quote({"customer_id": customer_id, "currency": "USD"}, actor_employee_profile_id=profile_a, actor_staff_user_id=staff_a)
        line = add_quote_line(
            quote, plan_id=plan_id, addon_id=None, quantity=1, override_unit_price=Decimal("50.00"),
            override_reason="discount", actor_staff_user_id=staff_a,
        )
        submit_quote(quote, actor_staff_user_id=staff_a)

        approval = unresolved_approvals_for_targets("QUOTE_LINE", [line.id])[0]
        decide_approval(
            approval, approved=True, decision_reason=None, decided_by_staff_user_id=staff_b, current_target_version=line.version
        )

        record_customer_decision(quote, accepted=True, actor_staff_user_id=staff_a)
        assert quote.status == "ACCEPTED"


def test_self_approval_forbidden(app, seeded):
    staff_id, profile_id = _seed_sales_employee(app, "appe@example.com")
    customer_id = _seed_customer(app, staff_id)
    plan_id = _seed_plan(app, "APE_PLAN")
    with app.app_context():
        from app.commercial_sales.approvals import decide_approval, unresolved_approvals_for_targets
        from app.commercial_sales.errors import CommercialSalesError
        from app.commercial_sales.quotes import add_quote_line, create_quote

        quote = create_quote({"customer_id": customer_id, "currency": "USD"}, actor_employee_profile_id=profile_id, actor_staff_user_id=staff_id)
        line = add_quote_line(
            quote, plan_id=plan_id, addon_id=None, quantity=1, override_unit_price=Decimal("0.00"),
            override_reason="free trial", actor_staff_user_id=staff_id,
        )
        approval = unresolved_approvals_for_targets("QUOTE_LINE", [line.id])[0]
        assert approval.reason_code == "ZERO_PRICE_LINE"

        with pytest.raises(CommercialSalesError) as exc:
            decide_approval(
                approval, approved=True, decision_reason=None, decided_by_staff_user_id=staff_id, current_target_version=line.version
            )
        assert exc.value.code == "SELF_APPROVAL_FORBIDDEN"


def test_reject_requires_reason(app, seeded):
    staff_a, profile_a = _seed_sales_employee(app, "appf_a@example.com")
    staff_b, _ = _seed_sales_employee(app, "appf_b@example.com")
    customer_id = _seed_customer(app, staff_a)
    plan_id = _seed_plan(app, "APF_PLAN")
    with app.app_context():
        from app.commercial_sales.approvals import decide_approval, unresolved_approvals_for_targets
        from app.commercial_sales.errors import CommercialSalesError
        from app.commercial_sales.quotes import add_quote_line, create_quote

        quote = create_quote({"customer_id": customer_id, "currency": "USD"}, actor_employee_profile_id=profile_a, actor_staff_user_id=staff_a)
        line = add_quote_line(
            quote, plan_id=plan_id, addon_id=None, quantity=1, override_unit_price=Decimal("50.00"),
            override_reason="discount", actor_staff_user_id=staff_a,
        )
        approval = unresolved_approvals_for_targets("QUOTE_LINE", [line.id])[0]

        with pytest.raises(CommercialSalesError) as exc:
            decide_approval(
                approval, approved=False, decision_reason=None, decided_by_staff_user_id=staff_b, current_target_version=line.version
            )
        assert exc.value.code == "REASON_REQUIRED"


def test_stale_approval_rejected_after_quote_changed(app, seeded):
    staff_a, profile_a = _seed_sales_employee(app, "appg_a@example.com")
    staff_b, _ = _seed_sales_employee(app, "appg_b@example.com")
    customer_id = _seed_customer(app, staff_a)
    plan_id = _seed_plan(app, "APG_PLAN")
    with app.app_context():
        from app.commercial_sales.approvals import decide_approval, unresolved_approvals_for_targets
        from app.commercial_sales.errors import CommercialSalesError
        from app.commercial_sales.quotes import add_quote_line, create_quote

        quote = create_quote({"customer_id": customer_id, "currency": "USD"}, actor_employee_profile_id=profile_a, actor_staff_user_id=staff_a)
        line = add_quote_line(
            quote, plan_id=plan_id, addon_id=None, quantity=1, override_unit_price=Decimal("50.00"),
            override_reason="discount", actor_staff_user_id=staff_a,
        )
        approval = unresolved_approvals_for_targets("QUOTE_LINE", [line.id])[0]
        stale_version = approval.target_version_at_request

        # No update_quote_line() exists yet in this milestone's scope, so
        # this simulates a future line edit directly -- proving the STALE
        # check itself works correctly once something real does bump
        # QuoteLine.version, even though no current code path does.
        line.version += 1

        with pytest.raises(CommercialSalesError) as exc:
            decide_approval(
                approval, approved=True, decision_reason=None, decided_by_staff_user_id=staff_b, current_target_version=line.version
            )
        assert exc.value.code == "APPROVAL_STALE"
        assert line.version != stale_version


def test_double_decision_rejected(app, seeded):
    staff_a, profile_a = _seed_sales_employee(app, "apph_a@example.com")
    staff_b, _ = _seed_sales_employee(app, "apph_b@example.com")
    customer_id = _seed_customer(app, staff_a)
    plan_id = _seed_plan(app, "APH_PLAN")
    with app.app_context():
        from app.commercial_sales.approvals import decide_approval, unresolved_approvals_for_targets
        from app.commercial_sales.errors import CommercialSalesError
        from app.commercial_sales.quotes import add_quote_line, create_quote

        quote = create_quote({"customer_id": customer_id, "currency": "USD"}, actor_employee_profile_id=profile_a, actor_staff_user_id=staff_a)
        line = add_quote_line(
            quote, plan_id=plan_id, addon_id=None, quantity=1, override_unit_price=Decimal("50.00"),
            override_reason="discount", actor_staff_user_id=staff_a,
        )
        approval = unresolved_approvals_for_targets("QUOTE_LINE", [line.id])[0]
        decide_approval(
            approval, approved=True, decision_reason=None, decided_by_staff_user_id=staff_b, current_target_version=line.version
        )

        with pytest.raises(CommercialSalesError) as exc:
            decide_approval(
                approval, approved=True, decision_reason=None, decided_by_staff_user_id=staff_b, current_target_version=line.version
            )
        assert exc.value.code == "INVALID_APPROVAL_TRANSITION"
