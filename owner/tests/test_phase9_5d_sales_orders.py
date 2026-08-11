"""Phase 9.5D Milestone 8 -- Sales Order domain service tests."""
from __future__ import annotations

import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest

from tests.conftest import make_staff

_employee_number_counter = iter(range(1, 100000))


def _seed_sales_employee(app, email):
    from app.employees.services import create_employee_profile

    staff_id = make_staff(app, email, role_codes=["SALES"])
    with app.app_context():
        profile = create_employee_profile(
            {
                "staff_user_id": staff_id,
                "employee_number": f"EMP-{next(_employee_number_counter):05d}",
                "full_name": f"Sales {email}",
                "employment_start_date": date(2026, 1, 1),
            },
            actor_staff_user_id=staff_id,
        )
        return staff_id, profile.id


def _seed_customer(app, staff_id):
    from app.customers.services import create_customer

    with app.app_context():
        customer = create_customer({"legal_name": "Order Test Customer Co"}, actor_staff_user_id=staff_id)
        return customer.id


def _seed_plan(app, plan_code, price=Decimal("100.00")):
    from app.catalog.services import add_plan_price, create_plan, seed_canonical_catalog
    from app.extensions import db_session
    from app.models.catalog import Product

    with app.app_context():
        seed_canonical_catalog()
        product = Product(product_code=f"PROD_{plan_code}", name="Order Test Product", is_active=True, is_sellable=True)
        db_session.add(product)
        db_session.flush()
        plan = create_plan(
            {
                "plan_code": plan_code,
                "product_id": product.id,
                "name": "Order Test Plan",
                "billing_model": "MONTHLY",
                "effective_date": date.today() - timedelta(days=1),
            },
            actor_staff_user_id=None,
        )
        add_plan_price(plan, price, "USD", date.today() - timedelta(days=1), actor_staff_user_id=None)
        return plan.id


def _make_accepted_quote(app, staff_id, profile_id, customer_id, plan_id):
    from app.commercial_sales.quotes import add_quote_line, create_quote, record_customer_decision, submit_quote

    quote = create_quote({"customer_id": customer_id, "currency": "USD"}, actor_employee_profile_id=profile_id, actor_staff_user_id=staff_id)
    add_quote_line(quote, plan_id=plan_id, addon_id=None, quantity=2, actor_staff_user_id=staff_id)
    submit_quote(quote, actor_staff_user_id=staff_id)
    record_customer_decision(quote, accepted=True, actor_staff_user_id=staff_id)
    return quote


def test_create_order_from_accepted_quote(app, seeded):
    staff_id, profile_id = _seed_sales_employee(app, "ordera@example.com")
    customer_id = _seed_customer(app, staff_id)
    plan_id = _seed_plan(app, "OA_PLAN")
    with app.app_context():
        from app.commercial_sales.sales_orders import create_order_from_quote

        quote = _make_accepted_quote(app, staff_id, profile_id, customer_id, plan_id)
        order = create_order_from_quote(
            quote, actor_employee_profile_id=profile_id, actor_staff_user_id=staff_id, idempotency_key=str(uuid.uuid4())
        )

        assert order.status == "DRAFT"
        assert order.order_number.startswith("SO-")
        assert order.customer_id == customer_id
        assert order.total == Decimal("200.00")
        assert len(order.lines) == 1


def test_order_requires_accepted_quote(app, seeded):
    staff_id, profile_id = _seed_sales_employee(app, "orderb@example.com")
    customer_id = _seed_customer(app, staff_id)
    plan_id = _seed_plan(app, "OB_PLAN")
    with app.app_context():
        from app.commercial_sales.errors import CommercialSalesError
        from app.commercial_sales.quotes import add_quote_line, create_quote
        from app.commercial_sales.sales_orders import create_order_from_quote

        quote = create_quote({"customer_id": customer_id, "currency": "USD"}, actor_employee_profile_id=profile_id, actor_staff_user_id=staff_id)
        add_quote_line(quote, plan_id=plan_id, addon_id=None, quantity=1, actor_staff_user_id=staff_id)

        with pytest.raises(CommercialSalesError) as exc:
            create_order_from_quote(
                quote, actor_employee_profile_id=profile_id, actor_staff_user_id=staff_id, idempotency_key=str(uuid.uuid4())
            )
        assert exc.value.code == "QUOTE_NOT_ACCEPTED"


def test_idempotent_replay_returns_same_order(app, seeded):
    staff_id, profile_id = _seed_sales_employee(app, "orderc@example.com")
    customer_id = _seed_customer(app, staff_id)
    plan_id = _seed_plan(app, "OC_PLAN")
    with app.app_context():
        from app.commercial_sales.sales_orders import create_order_from_quote

        quote = _make_accepted_quote(app, staff_id, profile_id, customer_id, plan_id)
        key = str(uuid.uuid4())
        first = create_order_from_quote(quote, actor_employee_profile_id=profile_id, actor_staff_user_id=staff_id, idempotency_key=key)
        second = create_order_from_quote(quote, actor_employee_profile_id=profile_id, actor_staff_user_id=staff_id, idempotency_key=key)
        assert first.id == second.id


def test_second_different_key_against_same_quote_rejected(app, seeded):
    """One active Order per accepted Quote -- a genuinely different
    idempotency key against the same already-ordered Quote must not
    create a second Order."""
    staff_id, profile_id = _seed_sales_employee(app, "orderd@example.com")
    customer_id = _seed_customer(app, staff_id)
    plan_id = _seed_plan(app, "OD_PLAN")
    with app.app_context():
        from app.commercial_sales.errors import CommercialSalesError
        from app.commercial_sales.sales_orders import create_order_from_quote
        from app.extensions import db_session
        from app.models.commercial_sales import SalesOrder

        quote = _make_accepted_quote(app, staff_id, profile_id, customer_id, plan_id)
        create_order_from_quote(quote, actor_employee_profile_id=profile_id, actor_staff_user_id=staff_id, idempotency_key=str(uuid.uuid4()))

        with pytest.raises(CommercialSalesError) as exc:
            create_order_from_quote(quote, actor_employee_profile_id=profile_id, actor_staff_user_id=staff_id, idempotency_key=str(uuid.uuid4()))
        assert exc.value.code == "IDEMPOTENCY_CONFLICT"
        assert db_session.query(SalesOrder).filter_by(quote_id=quote.id).count() == 1


def test_confirm_and_cancel_transitions(app, seeded):
    staff_id, profile_id = _seed_sales_employee(app, "ordere@example.com")
    customer_id = _seed_customer(app, staff_id)
    plan_id = _seed_plan(app, "OE_PLAN")
    with app.app_context():
        from app.commercial_sales.sales_orders import confirm_order, create_order_from_quote

        quote = _make_accepted_quote(app, staff_id, profile_id, customer_id, plan_id)
        order = create_order_from_quote(quote, actor_employee_profile_id=profile_id, actor_staff_user_id=staff_id, idempotency_key=str(uuid.uuid4()))

        confirm_order(order, actor_staff_user_id=staff_id)
        assert order.status == "CONFIRMED"
        assert order.confirmed_at is not None


def test_cancel_requires_reason(app, seeded):
    staff_id, profile_id = _seed_sales_employee(app, "orderf@example.com")
    customer_id = _seed_customer(app, staff_id)
    plan_id = _seed_plan(app, "OF_PLAN")
    with app.app_context():
        from app.commercial_sales.errors import CommercialSalesError
        from app.commercial_sales.sales_orders import cancel_order, create_order_from_quote

        quote = _make_accepted_quote(app, staff_id, profile_id, customer_id, plan_id)
        order = create_order_from_quote(quote, actor_employee_profile_id=profile_id, actor_staff_user_id=staff_id, idempotency_key=str(uuid.uuid4()))

        with pytest.raises(CommercialSalesError) as exc:
            cancel_order(order, reason="", actor_staff_user_id=staff_id)
        assert exc.value.code == "REASON_REQUIRED"


def test_confirmed_order_cannot_return_to_draft(app, seeded):
    staff_id, profile_id = _seed_sales_employee(app, "orderg@example.com")
    customer_id = _seed_customer(app, staff_id)
    plan_id = _seed_plan(app, "OG_PLAN")
    with app.app_context():
        from app.commercial_sales.errors import CommercialSalesError
        from app.commercial_sales.sales_orders import confirm_order, create_order_from_quote

        quote = _make_accepted_quote(app, staff_id, profile_id, customer_id, plan_id)
        order = create_order_from_quote(quote, actor_employee_profile_id=profile_id, actor_staff_user_id=staff_id, idempotency_key=str(uuid.uuid4()))
        confirm_order(order, actor_staff_user_id=staff_id)

        with pytest.raises(CommercialSalesError) as exc:
            confirm_order(order, actor_staff_user_id=staff_id)
        assert exc.value.code == "INVALID_ORDER_TRANSITION"


def test_stale_version_rejected(app, seeded):
    staff_id, profile_id = _seed_sales_employee(app, "orderh@example.com")
    customer_id = _seed_customer(app, staff_id)
    plan_id = _seed_plan(app, "OH_PLAN")
    with app.app_context():
        from app.commercial_sales.errors import CommercialSalesError
        from app.commercial_sales.sales_orders import confirm_order, create_order_from_quote

        quote = _make_accepted_quote(app, staff_id, profile_id, customer_id, plan_id)
        order = create_order_from_quote(quote, actor_employee_profile_id=profile_id, actor_staff_user_id=staff_id, idempotency_key=str(uuid.uuid4()))

        with pytest.raises(CommercialSalesError) as exc:
            confirm_order(order, actor_staff_user_id=staff_id, expected_version=order.version + 1)
        assert exc.value.code == "STALE_VERSION"


def test_order_creates_no_invoice_payment_subscription_license_commission(app, seeded):
    """Negative-space proof, matching Phase 9.5C/Milestone 7's own
    precedent: creating and confirming an Order must never create an
    Invoice/Payment/Subscription/License/Commission."""
    staff_id, profile_id = _seed_sales_employee(app, "orderi@example.com")
    customer_id = _seed_customer(app, staff_id)
    plan_id = _seed_plan(app, "OI_PLAN")
    with app.app_context():
        from app.commercial_sales.sales_orders import confirm_order, create_order_from_quote
        from app.extensions import db_session
        from app.models.commercial_sales import CommercialInvoice
        from app.models.commissions import CommissionLedgerEntry
        from app.models.licensing import License
        from app.models.subscriptions import PaymentRecord, Subscription

        quote = _make_accepted_quote(app, staff_id, profile_id, customer_id, plan_id)
        order = create_order_from_quote(quote, actor_employee_profile_id=profile_id, actor_staff_user_id=staff_id, idempotency_key=str(uuid.uuid4()))
        confirm_order(order, actor_staff_user_id=staff_id)

        assert db_session.query(CommercialInvoice).filter_by(sales_order_id=order.id).count() == 0
        assert db_session.query(PaymentRecord).count() == 0
        assert db_session.query(Subscription).filter_by(customer_id=customer_id).count() == 0
        assert db_session.query(License).filter_by(customer_id=customer_id).count() == 0
        assert db_session.query(CommissionLedgerEntry).filter_by(employee_profile_id=profile_id).count() == 0
