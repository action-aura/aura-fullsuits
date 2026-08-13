"""Phase 9.5D Milestone 11 -- payment allocation tests."""
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
        customer = create_customer({"legal_name": "Allocation Test Customer Co"}, actor_staff_user_id=staff_id)
        return customer.id


def _seed_plan(app, plan_code, price=Decimal("200.00")):
    from app.catalog.services import add_plan_price, create_plan, seed_canonical_catalog
    from app.extensions import db_session
    from app.models.catalog import Product

    with app.app_context():
        seed_canonical_catalog()
        product = Product(product_code=f"PROD_{plan_code}", name="Allocation Test Product", is_active=True, is_sellable=True)
        db_session.add(product)
        db_session.flush()
        plan = create_plan(
            {
                "plan_code": plan_code,
                "product_id": product.id,
                "name": "Allocation Test Plan",
                "billing_model": "MONTHLY",
                "effective_date": date.today() - timedelta(days=1),
            },
            actor_staff_user_id=None,
        )
        add_plan_price(plan, price, "USD", date.today() - timedelta(days=1), actor_staff_user_id=None)
        return plan.id


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


def test_full_allocation_marks_invoice_paid(app, seeded):
    staff_a, profile_a = _seed_sales_employee(app, "alloca@example.com")
    staff_b, _ = _seed_sales_employee(app, "allocb@example.com", role_codes=["FINANCE"])
    customer_id = _seed_customer(app, staff_a)
    plan_id = _seed_plan(app, "ALA_PLAN")
    with app.app_context():
        from app.commercial_sales.allocation import allocate_payment

        invoice = _make_issued_invoice(app, staff_a, profile_a, customer_id, plan_id)
        payment = _make_confirmed_payment(app, customer_id, Decimal("200.00"), staff_a, staff_b)

        allocation = allocate_payment(payment=payment, invoice=invoice, amount=Decimal("200.00"), actor_staff_user_id=staff_b)

        assert allocation.allocated_amount == Decimal("200.00")
        assert invoice.status == "PAID"


def test_partial_allocation_marks_invoice_partially_paid(app, seeded):
    staff_a, profile_a = _seed_sales_employee(app, "allocc@example.com")
    staff_b, _ = _seed_sales_employee(app, "allocd@example.com", role_codes=["FINANCE"])
    customer_id = _seed_customer(app, staff_a)
    plan_id = _seed_plan(app, "ALC_PLAN")
    with app.app_context():
        from app.commercial_sales.allocation import allocate_payment

        invoice = _make_issued_invoice(app, staff_a, profile_a, customer_id, plan_id)
        payment = _make_confirmed_payment(app, customer_id, Decimal("200.00"), staff_a, staff_b)

        allocate_payment(payment=payment, invoice=invoice, amount=Decimal("50.00"), actor_staff_user_id=staff_b)
        assert invoice.status == "PARTIALLY_PAID"


def test_unconfirmed_payment_cannot_be_allocated(app, seeded):
    staff_a, profile_a = _seed_sales_employee(app, "alloce@example.com")
    customer_id = _seed_customer(app, staff_a)
    plan_id = _seed_plan(app, "ALE_PLAN")
    with app.app_context():
        from app.commercial_sales.allocation import allocate_payment
        from app.commercial_sales.errors import CommercialSalesError
        from app.commercial_sales.payments import submit_payment

        invoice = _make_issued_invoice(app, staff_a, profile_a, customer_id, plan_id)
        payment = submit_payment(
            customer_id=customer_id, amount=Decimal("200.00"), currency="USD", method="CASH",
            payment_date=date.today(), actor_staff_user_id=staff_a,
        )

        with pytest.raises(CommercialSalesError) as exc:
            allocate_payment(payment=payment, invoice=invoice, amount=Decimal("200.00"), actor_staff_user_id=staff_a)
        assert exc.value.code == "PAYMENT_NOT_CONFIRMED"


def test_allocation_exceeding_payment_balance_rejected(app, seeded):
    staff_a, profile_a = _seed_sales_employee(app, "allocf@example.com")
    staff_b, _ = _seed_sales_employee(app, "allocg@example.com", role_codes=["FINANCE"])
    customer_id = _seed_customer(app, staff_a)
    plan_id = _seed_plan(app, "ALF_PLAN", price=Decimal("500.00"))
    with app.app_context():
        from app.commercial_sales.allocation import allocate_payment
        from app.commercial_sales.errors import CommercialSalesError

        invoice = _make_issued_invoice(app, staff_a, profile_a, customer_id, plan_id)
        payment = _make_confirmed_payment(app, customer_id, Decimal("100.00"), staff_a, staff_b)

        with pytest.raises(CommercialSalesError) as exc:
            allocate_payment(payment=payment, invoice=invoice, amount=Decimal("101.00"), actor_staff_user_id=staff_b)
        assert exc.value.code == "ALLOCATION_EXCEEDS_PAYMENT"


def test_allocation_exceeding_invoice_outstanding_rejected(app, seeded):
    staff_a, profile_a = _seed_sales_employee(app, "alloch@example.com")
    staff_b, _ = _seed_sales_employee(app, "alloci@example.com", role_codes=["FINANCE"])
    customer_id = _seed_customer(app, staff_a)
    plan_id = _seed_plan(app, "ALH_PLAN", price=Decimal("100.00"))
    with app.app_context():
        from app.commercial_sales.allocation import allocate_payment
        from app.commercial_sales.errors import CommercialSalesError

        invoice = _make_issued_invoice(app, staff_a, profile_a, customer_id, plan_id)
        payment = _make_confirmed_payment(app, customer_id, Decimal("500.00"), staff_a, staff_b)

        with pytest.raises(CommercialSalesError) as exc:
            allocate_payment(payment=payment, invoice=invoice, amount=Decimal("101.00"), actor_staff_user_id=staff_b)
        assert exc.value.code == "ALLOCATION_EXCEEDS_OUTSTANDING"


def test_cross_currency_allocation_rejected(app, seeded):
    staff_a, profile_a = _seed_sales_employee(app, "allocj@example.com")
    staff_b, _ = _seed_sales_employee(app, "allock@example.com", role_codes=["FINANCE"])
    customer_id = _seed_customer(app, staff_a)
    plan_id = _seed_plan(app, "ALJ_PLAN")
    with app.app_context():
        from app.commercial_sales.allocation import allocate_payment
        from app.commercial_sales.errors import CommercialSalesError
        from app.commercial_sales.payments import confirm_payment, submit_payment

        invoice = _make_issued_invoice(app, staff_a, profile_a, customer_id, plan_id)
        payment = submit_payment(
            customer_id=customer_id, amount=Decimal("200.00"), currency="EUR", method="CASH",
            payment_date=date.today(), actor_staff_user_id=staff_a,
        )
        confirm_payment(payment, actor_staff_user_id=staff_b)

        with pytest.raises(CommercialSalesError) as exc:
            allocate_payment(payment=payment, invoice=invoice, amount=Decimal("200.00"), actor_staff_user_id=staff_b)
        assert exc.value.code == "CURRENCY_MISMATCH"


def test_multiple_partial_payments_sum_to_paid(app, seeded):
    """One Invoice may receive multiple confirmed Payments."""
    staff_a, profile_a = _seed_sales_employee(app, "allocl@example.com")
    staff_b, _ = _seed_sales_employee(app, "allocm@example.com", role_codes=["FINANCE"])
    customer_id = _seed_customer(app, staff_a)
    plan_id = _seed_plan(app, "ALL_PLAN", price=Decimal("300.00"))
    with app.app_context():
        from app.commercial_sales.allocation import allocate_payment

        invoice = _make_issued_invoice(app, staff_a, profile_a, customer_id, plan_id)
        payment1 = _make_confirmed_payment(app, customer_id, Decimal("100.00"), staff_a, staff_b)
        payment2 = _make_confirmed_payment(app, customer_id, Decimal("200.00"), staff_a, staff_b)

        allocate_payment(payment=payment1, invoice=invoice, amount=Decimal("100.00"), actor_staff_user_id=staff_b)
        assert invoice.status == "PARTIALLY_PAID"
        allocate_payment(payment=payment2, invoice=invoice, amount=Decimal("200.00"), actor_staff_user_id=staff_b)
        assert invoice.status == "PAID"


def test_reversal_requires_reason(app, seeded):
    staff_a, profile_a = _seed_sales_employee(app, "allocn@example.com")
    staff_b, _ = _seed_sales_employee(app, "alloco@example.com", role_codes=["FINANCE"])
    customer_id = _seed_customer(app, staff_a)
    plan_id = _seed_plan(app, "ALN_PLAN")
    with app.app_context():
        from app.commercial_sales.allocation import allocate_payment, reverse_allocation
        from app.commercial_sales.errors import CommercialSalesError

        invoice = _make_issued_invoice(app, staff_a, profile_a, customer_id, plan_id)
        payment = _make_confirmed_payment(app, customer_id, Decimal("200.00"), staff_a, staff_b)
        allocation = allocate_payment(payment=payment, invoice=invoice, amount=Decimal("200.00"), actor_staff_user_id=staff_b)

        with pytest.raises(CommercialSalesError) as exc:
            reverse_allocation(allocation, reason="", actor_staff_user_id=staff_b)
        assert exc.value.code == "REASON_REQUIRED"


def test_reversal_reduces_invoice_status_back_down(app, seeded):
    staff_a, profile_a = _seed_sales_employee(app, "allocp@example.com")
    staff_b, _ = _seed_sales_employee(app, "allocq@example.com", role_codes=["FINANCE"])
    customer_id = _seed_customer(app, staff_a)
    plan_id = _seed_plan(app, "ALP_PLAN")
    with app.app_context():
        from app.commercial_sales.allocation import allocate_payment, reverse_allocation

        invoice = _make_issued_invoice(app, staff_a, profile_a, customer_id, plan_id)
        payment = _make_confirmed_payment(app, customer_id, Decimal("200.00"), staff_a, staff_b)
        allocation = allocate_payment(payment=payment, invoice=invoice, amount=Decimal("200.00"), actor_staff_user_id=staff_b)
        assert invoice.status == "PAID"

        reverse_allocation(allocation, reason="wrong invoice", actor_staff_user_id=staff_b)
        assert invoice.status == "ISSUED"


def test_reversed_allocation_frees_payment_balance_for_reallocation(app, seeded):
    staff_a, profile_a = _seed_sales_employee(app, "allocr@example.com")
    staff_b, _ = _seed_sales_employee(app, "allocs@example.com", role_codes=["FINANCE"])
    customer_id = _seed_customer(app, staff_a)
    plan_id = _seed_plan(app, "ALR_PLAN")
    with app.app_context():
        from app.commercial_sales.allocation import allocate_payment, reverse_allocation, unallocated_payment_balance

        invoice_a = _make_issued_invoice(app, staff_a, profile_a, customer_id, plan_id)
        invoice_b = _make_issued_invoice(app, staff_a, profile_a, customer_id, plan_id)
        payment = _make_confirmed_payment(app, customer_id, Decimal("200.00"), staff_a, staff_b)

        allocation = allocate_payment(payment=payment, invoice=invoice_a, amount=Decimal("200.00"), actor_staff_user_id=staff_b)
        assert unallocated_payment_balance(payment) == Decimal("0.00")

        reverse_allocation(allocation, reason="applied to wrong invoice", actor_staff_user_id=staff_b)
        assert unallocated_payment_balance(payment) == Decimal("200.00")

        reallocated = allocate_payment(payment=payment, invoice=invoice_b, amount=Decimal("200.00"), actor_staff_user_id=staff_b)
        assert reallocated.allocated_amount == Decimal("200.00")
        assert invoice_b.status == "PAID"
