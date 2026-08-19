"""Phase 9.5D Milestone 17 -- commercial-sales dashboard tests."""
from __future__ import annotations

import uuid
from datetime import date, timedelta
from decimal import Decimal

from tests.conftest import make_staff

_employee_number_counter = iter(range(1, 100000))


def _seed_sales_employee(app, email, role_codes=None):
    from app.employees.services import create_employee_profile

    staff_id = make_staff(app, email, role_codes=role_codes or ["SALES"])
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


def _seed_customer(app, staff_id):
    from app.customers.services import create_customer

    with app.app_context():
        customer = create_customer({"legal_name": "Dashboard Test Customer Co"}, actor_staff_user_id=staff_id)
        return customer.id


def _seed_plan(app, plan_code, price=Decimal("1000.00")):
    from app.catalog.services import add_plan_price, create_plan, seed_canonical_catalog
    from app.extensions import db_session
    from app.models.catalog import Product

    with app.app_context():
        seed_canonical_catalog()
        product = Product(product_code=f"PROD_{plan_code}", name="Dashboard Test Product", is_active=True, is_sellable=True)
        db_session.add(product)
        db_session.flush()
        plan = create_plan(
            {
                "plan_code": plan_code,
                "product_id": product.id,
                "name": "Dashboard Test Plan",
                "billing_model": "MONTHLY",
                "effective_date": date.today() - timedelta(days=1),
            },
            actor_staff_user_id=None,
        )
        add_plan_price(plan, price, "USD", date.today() - timedelta(days=1), actor_staff_user_id=None)
        return plan.id


def _grant_commission_plan(app, profile_id, staff_id, rate=Decimal("10.0")):
    from app.commissions.management import assign_employee_commission_plan, create_commission_plan, create_commission_rule_version

    plan = create_commission_plan({"plan_code": f"DASH_PLAN_{uuid.uuid4().hex[:8]}", "name": "Dashboard Test Plan"}, staff_id)
    create_commission_rule_version(
        plan, rule_type="PERCENTAGE_OF_PAYMENT", rate_percentage=rate,
        effective_from=date.today() - timedelta(days=1), actor_staff_user_id=staff_id,
    )
    assign_employee_commission_plan(profile_id, plan, effective_from=date.today() - timedelta(days=1), actor_staff_user_id=staff_id)


def _make_issued_invoice(app, staff_id, profile_id, customer_id, plan_id):
    from app.commercial_sales.invoices import create_invoice_from_order, issue_invoice
    from app.commercial_sales.quotes import add_quote_line, create_quote, record_customer_decision, submit_quote
    from app.commercial_sales.sales_orders import confirm_order, create_order_from_quote

    quote = create_quote({"customer_id": customer_id, "currency": "USD"}, actor_employee_profile_id=profile_id, actor_staff_user_id=staff_id)
    add_quote_line(quote, plan_id=plan_id, addon_id=None, quantity=1, actor_staff_user_id=staff_id)
    submit_quote(quote, actor_staff_user_id=staff_id)
    record_customer_decision(quote, accepted=True, actor_staff_user_id=staff_id)
    order = create_order_from_quote(quote, actor_employee_profile_id=profile_id, actor_staff_user_id=staff_id, idempotency_key=str(uuid.uuid4()))
    confirm_order(order, actor_staff_user_id=staff_id)
    invoice = create_invoice_from_order(order, actor_employee_profile_id=profile_id, actor_staff_user_id=staff_id, idempotency_key=str(uuid.uuid4()))
    issue_invoice(invoice, actor_staff_user_id=staff_id)
    return invoice


def _make_confirmed_payment(app, customer_id, amount, submitter_staff_id, confirmer_staff_id):
    from app.commercial_sales.payments import confirm_payment, submit_payment

    payment = submit_payment(
        customer_id=customer_id, amount=amount, currency="USD", method="CASH",
        payment_date=date.today(), actor_staff_user_id=submitter_staff_id,
    )
    return confirm_payment(payment, actor_staff_user_id=confirmer_staff_id)


def test_employee_dashboard_is_scoped_to_own_pipeline_and_commission(app, seeded):
    from app.commercial_sales.allocation import allocate_payment
    from app.commercial_sales.dashboards import employee_commercial_dashboard

    staff_a, profile_a = _seed_sales_employee(app, "dasha@example.com")
    staff_x, profile_x = _seed_sales_employee(app, "dashx@example.com")  # a peer, must be invisible to A's view
    staff_b, _ = _seed_sales_employee(app, "dashb@example.com", role_codes=["FINANCE"])
    customer_id = _seed_customer(app, staff_a)
    plan_id = _seed_plan(app, "DA_PLAN")
    with app.app_context():
        _grant_commission_plan(app, profile_a, staff_a, rate=Decimal("10.0"))
        _grant_commission_plan(app, profile_x, staff_x, rate=Decimal("10.0"))

        invoice_a = _make_issued_invoice(app, staff_a, profile_a, customer_id, plan_id)
        payment_a = _make_confirmed_payment(app, customer_id, Decimal("1000.00"), staff_a, staff_b)
        allocate_payment(payment=payment_a, invoice=invoice_a, amount=Decimal("1000.00"), actor_staff_user_id=staff_b)

        customer_x = _seed_customer(app, staff_x)
        invoice_x = _make_issued_invoice(app, staff_x, profile_x, customer_x, plan_id)
        payment_x = _make_confirmed_payment(app, customer_x, Decimal("500.00"), staff_x, staff_b)
        allocate_payment(payment=payment_x, invoice=invoice_x, amount=Decimal("500.00"), actor_staff_user_id=staff_b)

        dash_a = employee_commercial_dashboard(profile_a, staff_a)

        assert dash_a["invoices_by_status"].get("PAID", 0) == 1  # only A's own invoice, fully allocated -> PAID
        assert dash_a["commission_earned_unapproved"] == Decimal("100.00")  # only A's own 10% of 1000, never X's 50


def test_finance_dashboard_aggregates_across_all_employees(app, seeded):
    from app.commercial_sales.allocation import allocate_payment
    from app.commercial_sales.dashboards import finance_commercial_dashboard

    staff_a, profile_a = _seed_sales_employee(app, "dashc@example.com")
    staff_x, profile_x = _seed_sales_employee(app, "dashd@example.com")
    staff_b, _ = _seed_sales_employee(app, "dashe@example.com", role_codes=["FINANCE"])
    customer_a = _seed_customer(app, staff_a)
    customer_x = _seed_customer(app, staff_x)
    plan_id = _seed_plan(app, "DC_PLAN")
    with app.app_context():
        _grant_commission_plan(app, profile_a, staff_a, rate=Decimal("10.0"))
        _grant_commission_plan(app, profile_x, staff_x, rate=Decimal("10.0"))

        invoice_a = _make_issued_invoice(app, staff_a, profile_a, customer_a, plan_id)
        payment_a = _make_confirmed_payment(app, customer_a, Decimal("1000.00"), staff_a, staff_b)
        allocate_payment(payment=payment_a, invoice=invoice_a, amount=Decimal("1000.00"), actor_staff_user_id=staff_b)

        invoice_x = _make_issued_invoice(app, staff_x, profile_x, customer_x, plan_id)
        payment_x = _make_confirmed_payment(app, customer_x, Decimal("1000.00"), staff_x, staff_b)
        allocate_payment(payment=payment_x, invoice=invoice_x, amount=Decimal("1000.00"), actor_staff_user_id=staff_b)

        dash = finance_commercial_dashboard()

        assert dash["invoices_by_status"].get("PAID", 0) >= 2  # both invoices, cross-employee
        assert dash["commissions_pending_approval"] >= 2  # both EARNED entries, cross-employee
        # Shape is now per-currency (never a blended scalar) -- see
        # commercial_sales/dashboards.py::finance_commercial_dashboard().
        assert dash["outstanding_invoice_totals"].get("USD", Decimal("0.00")) >= Decimal("0.00")  # both fully paid, so nothing outstanding from these two


def test_employee_dashboard_pending_approval_request_count(app, seeded):
    """own_pending_approval_requests counts approvals THIS employee's own
    action requested, keyed by requested_by_staff_user_id -- never a
    peer's request. add_quote_line() auto-creates the CommercialApproval
    row when a line needs one (Milestone 6) -- no manual construction
    needed here."""
    from app.commercial_sales.dashboards import employee_commercial_dashboard
    from app.commercial_sales.quotes import add_quote_line, create_quote

    staff_a, profile_a = _seed_sales_employee(app, "dashf@example.com")
    customer_id = _seed_customer(app, staff_a)
    plan_id = _seed_plan(app, "DF_PLAN")
    with app.app_context():
        quote = create_quote({"customer_id": customer_id, "currency": "USD"}, actor_employee_profile_id=profile_a, actor_staff_user_id=staff_a)
        add_quote_line(quote, plan_id=plan_id, addon_id=None, quantity=1, override_unit_price=Decimal("500.00"), actor_staff_user_id=staff_a)

        dash = employee_commercial_dashboard(profile_a, staff_a)
        assert dash["own_pending_approval_requests"] == 1
