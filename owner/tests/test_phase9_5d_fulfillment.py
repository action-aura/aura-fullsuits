"""Phase 9.5D Milestone 13 -- commercial fulfillment orchestration tests."""
from __future__ import annotations

import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest

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
        customer = create_customer({"legal_name": "Fulfillment Test Customer Co"}, actor_staff_user_id=staff_id)
        return customer.id


def _seed_plan(app, plan_code, price=Decimal("100.00")):
    from app.catalog.services import add_plan_price, create_plan, seed_canonical_catalog
    from app.extensions import db_session
    from app.models.catalog import Product, ProductPlatform, Platform

    with app.app_context():
        seed_canonical_catalog()
        product = Product(product_code=f"PROD_{plan_code}", name="Fulfillment Test Product", is_active=True, is_sellable=True)
        db_session.add(product)
        db_session.flush()
        platform = db_session.query(Platform).first()
        db_session.add(ProductPlatform(product_id=product.id, platform_id=platform.id, supported=True))
        plan = create_plan(
            {
                "plan_code": plan_code,
                "product_id": product.id,
                "name": "Fulfillment Test Plan",
                "billing_model": "MONTHLY",
                "effective_date": date.today() - timedelta(days=1),
                "included_device_count": 3,
            },
            actor_staff_user_id=None,
        )
        add_plan_price(plan, price, "USD", date.today() - timedelta(days=1), actor_staff_user_id=None)
        return plan.id


def _make_paid_order(app, staff_id, profile_id, customer_id, plan_id, finance_staff_id):
    from app.commercial_sales.allocation import allocate_payment
    from app.commercial_sales.invoices import create_invoice_from_order, issue_invoice
    from app.commercial_sales.payments import confirm_payment, submit_payment
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

    payment = submit_payment(
        customer_id=customer_id, amount=invoice.total, currency="USD", method="CASH",
        payment_date=date.today(), actor_staff_user_id=staff_id,
    )
    confirm_payment(payment, actor_staff_user_id=finance_staff_id)
    allocate_payment(payment=payment, invoice=invoice, amount=invoice.total, actor_staff_user_id=finance_staff_id)
    return order, invoice


def test_fulfill_order_creates_active_subscription_and_issued_license(app, seeded):
    staff_id, profile_id = _seed_sales_employee(app, "fulfilla@example.com")
    finance_id, _ = _seed_sales_employee(app, "fulfillb@example.com", role_codes=["FINANCE"])
    customer_id = _seed_customer(app, staff_id)
    plan_id = _seed_plan(app, "FA_PLAN")
    with app.app_context():
        from app.commercial_sales.fulfillment import fulfill_order
        from app.extensions import db_session
        from app.models.licensing import License
        from app.models.subscriptions import Subscription

        order, invoice = _make_paid_order(app, staff_id, profile_id, customer_id, plan_id, finance_id)
        result = fulfill_order(order, actor_staff_user_id=finance_id, license_pepper=app.config["LICENSE_PEPPER"], idempotency_key=str(uuid.uuid4()))

        subscription = db_session.get(Subscription, result["subscription_id"])
        assert subscription.status == "ACTIVE"
        assert subscription.sales_order_id == order.id

        license_row = db_session.get(License, result["license_id"])
        assert license_row.status == "ISSUED"
        assert license_row.allowed_platforms  # real platform codes, never "ALL"
        assert "ALL" not in license_row.allowed_platforms.split(",")

        assert order.status == "FULFILLED"
        assert order.fulfilled_at is not None


def test_fulfillment_requires_confirmed_order(app, seeded):
    staff_id, profile_id = _seed_sales_employee(app, "fulfillc@example.com")
    customer_id = _seed_customer(app, staff_id)
    plan_id = _seed_plan(app, "FC_PLAN")
    with app.app_context():
        from app.commercial_sales.errors import CommercialSalesError
        from app.commercial_sales.fulfillment import fulfill_order
        from app.commercial_sales.quotes import add_quote_line, create_quote, record_customer_decision, submit_quote
        from app.commercial_sales.sales_orders import create_order_from_quote

        quote = create_quote({"customer_id": customer_id, "currency": "USD"}, actor_employee_profile_id=profile_id, actor_staff_user_id=staff_id)
        add_quote_line(quote, plan_id=plan_id, addon_id=None, quantity=1, actor_staff_user_id=staff_id)
        submit_quote(quote, actor_staff_user_id=staff_id)
        record_customer_decision(quote, accepted=True, actor_staff_user_id=staff_id)
        order = create_order_from_quote(quote, actor_employee_profile_id=profile_id, actor_staff_user_id=staff_id, idempotency_key=str(uuid.uuid4()))
        # order still DRAFT

        with pytest.raises(CommercialSalesError) as exc:
            fulfill_order(order, actor_staff_user_id=staff_id, license_pepper=app.config["LICENSE_PEPPER"], idempotency_key=str(uuid.uuid4()))
        assert exc.value.code == "FULFILLMENT_NOT_ELIGIBLE"


def test_fulfillment_requires_fully_paid_invoice(app, seeded):
    staff_id, profile_id = _seed_sales_employee(app, "fulfilld@example.com")
    finance_id, _ = _seed_sales_employee(app, "fulfille@example.com", role_codes=["FINANCE"])
    customer_id = _seed_customer(app, staff_id)
    plan_id = _seed_plan(app, "FD_PLAN", price=Decimal("200.00"))
    with app.app_context():
        from app.commercial_sales.allocation import allocate_payment
        from app.commercial_sales.errors import CommercialSalesError
        from app.commercial_sales.fulfillment import fulfill_order
        from app.commercial_sales.invoices import create_invoice_from_order, issue_invoice
        from app.commercial_sales.payments import confirm_payment, submit_payment
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

        payment = submit_payment(
            customer_id=customer_id, amount=Decimal("50.00"), currency="USD", method="CASH",
            payment_date=date.today(), actor_staff_user_id=staff_id,
        )
        confirm_payment(payment, actor_staff_user_id=finance_id)
        allocate_payment(payment=payment, invoice=invoice, amount=Decimal("50.00"), actor_staff_user_id=finance_id)
        assert invoice.status == "PARTIALLY_PAID"

        with pytest.raises(CommercialSalesError) as exc:
            fulfill_order(order, actor_staff_user_id=finance_id, license_pepper=app.config["LICENSE_PEPPER"], idempotency_key=str(uuid.uuid4()))
        assert exc.value.code == "FULFILLMENT_NOT_ELIGIBLE"


def test_fulfillment_blocked_by_pending_refund(app, seeded):
    staff_id, profile_id = _seed_sales_employee(app, "fulfillf@example.com")
    finance_id, _ = _seed_sales_employee(app, "fulfillg@example.com", role_codes=["FINANCE"])
    customer_id = _seed_customer(app, staff_id)
    plan_id = _seed_plan(app, "FF_PLAN")
    with app.app_context():
        from app.commercial_sales.errors import CommercialSalesError
        from app.commercial_sales.fulfillment import fulfill_order
        from app.commercial_sales.refunds import create_refund

        order, invoice = _make_paid_order(app, staff_id, profile_id, customer_id, plan_id, finance_id)
        create_refund(
            invoice, amount=Decimal("10.00"), reason="partial issue", payment_record_id=None,
            actor_employee_profile_id=profile_id, actor_staff_user_id=staff_id,
        )

        with pytest.raises(CommercialSalesError) as exc:
            fulfill_order(order, actor_staff_user_id=finance_id, license_pepper=app.config["LICENSE_PEPPER"], idempotency_key=str(uuid.uuid4()))
        assert exc.value.code == "FULFILLMENT_NOT_ELIGIBLE"


def test_idempotent_replay_returns_same_subscription(app, seeded):
    staff_id, profile_id = _seed_sales_employee(app, "fulfillh@example.com")
    finance_id, _ = _seed_sales_employee(app, "fulfilli@example.com", role_codes=["FINANCE"])
    customer_id = _seed_customer(app, staff_id)
    plan_id = _seed_plan(app, "FH_PLAN")
    with app.app_context():
        from app.commercial_sales.fulfillment import fulfill_order

        order, invoice = _make_paid_order(app, staff_id, profile_id, customer_id, plan_id, finance_id)
        key = str(uuid.uuid4())
        first = fulfill_order(order, actor_staff_user_id=finance_id, license_pepper=app.config["LICENSE_PEPPER"], idempotency_key=key)
        second = fulfill_order(order, actor_staff_user_id=finance_id, license_pepper=app.config["LICENSE_PEPPER"], idempotency_key=key)
        assert first["subscription_id"] == second["subscription_id"]
        assert second["replayed"] is True


def test_recovery_guard_reuses_existing_subscription_after_simulated_partial_failure(app, seeded):
    """Real gap found and fixed while building this milestone: the
    idempotency-key record is written last in the sequence, so a crash
    between Subscription creation and that final commit would leave a
    real, committed Subscription with no matching replay record. This
    simulates exactly that crash point directly (a Subscription created
    and linked to the order, no idempotency-key row yet) and proves a
    fresh fulfill_order() call reuses it rather than creating a second
    one."""
    staff_id, profile_id = _seed_sales_employee(app, "fulfilln@example.com")
    finance_id, _ = _seed_sales_employee(app, "fulfillo@example.com", role_codes=["FINANCE"])
    customer_id = _seed_customer(app, staff_id)
    plan_id = _seed_plan(app, "FN_PLAN")
    with app.app_context():
        from app.commercial_sales.fulfillment import fulfill_order
        from app.extensions import db_session
        from app.models.catalog import Plan
        from app.models.subscriptions import Subscription
        from app.subscriptions.services import create_subscription

        order, invoice = _make_paid_order(app, staff_id, profile_id, customer_id, plan_id, finance_id)
        plan = db_session.get(Plan, plan_id)

        # Simulate the exact partial-failure state: a real Subscription
        # already exists for this order, but fulfill_order() has never
        # successfully completed (no idempotency-key record, order still
        # CONFIRMED not FULFILLED).
        pre_existing = create_subscription(
            {"customer_id": customer_id, "product_id": plan.product_id, "plan_id": plan_id, "sales_order_id": order.id},
            finance_id,
        )
        assert db_session.query(Subscription).filter_by(sales_order_id=order.id).count() == 1

        result = fulfill_order(order, actor_staff_user_id=finance_id, license_pepper=app.config["LICENSE_PEPPER"], idempotency_key=str(uuid.uuid4()))

        assert result["subscription_id"] == pre_existing.id
        assert db_session.query(Subscription).filter_by(sales_order_id=order.id).count() == 1
        assert pre_existing.status == "ACTIVE"


def test_already_fulfilled_order_rejected(app, seeded):
    staff_id, profile_id = _seed_sales_employee(app, "fulfillj@example.com")
    finance_id, _ = _seed_sales_employee(app, "fulfillk@example.com", role_codes=["FINANCE"])
    customer_id = _seed_customer(app, staff_id)
    plan_id = _seed_plan(app, "FJ_PLAN")
    with app.app_context():
        from app.commercial_sales.errors import CommercialSalesError
        from app.commercial_sales.fulfillment import fulfill_order

        order, invoice = _make_paid_order(app, staff_id, profile_id, customer_id, plan_id, finance_id)
        fulfill_order(order, actor_staff_user_id=finance_id, license_pepper=app.config["LICENSE_PEPPER"], idempotency_key=str(uuid.uuid4()))

        with pytest.raises(CommercialSalesError) as exc:
            fulfill_order(order, actor_staff_user_id=finance_id, license_pepper=app.config["LICENSE_PEPPER"], idempotency_key=str(uuid.uuid4()))
        assert exc.value.code == "FULFILLMENT_ALREADY_COMPLETE"


def test_no_installation_created_by_fulfillment(app, seeded):
    """Non-Negotiable: fulfillment never creates an Installation --
    device activation remains a separate, later event."""
    staff_id, profile_id = _seed_sales_employee(app, "fulfilll@example.com")
    finance_id, _ = _seed_sales_employee(app, "fulfillm@example.com", role_codes=["FINANCE"])
    customer_id = _seed_customer(app, staff_id)
    plan_id = _seed_plan(app, "FL_PLAN")
    with app.app_context():
        from app.commercial_sales.fulfillment import fulfill_order
        from app.extensions import db_session
        from app.models.installations import Installation

        order, invoice = _make_paid_order(app, staff_id, profile_id, customer_id, plan_id, finance_id)
        fulfill_order(order, actor_staff_user_id=finance_id, license_pepper=app.config["LICENSE_PEPPER"], idempotency_key=str(uuid.uuid4()))

        assert db_session.query(Installation).count() == 0


def test_fulfillment_module_never_constructs_subscription_or_license_directly():
    """Structural proof, matching Phase 9.5C's own verification method for
    leads/conversion.py: grep the actual source for a direct model
    constructor call."""
    import inspect

    from app.commercial_sales import fulfillment

    source = inspect.getsource(fulfillment)
    assert "Subscription(" not in source
    assert "License(" not in source
    assert "create_subscription(" in source
    assert "issue_license_key(" in source
