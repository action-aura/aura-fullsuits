"""Phase 9.5D Milestone 12 -- Commercial Refund domain service tests."""
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
        customer = create_customer({"legal_name": "Refund Test Customer Co"}, actor_staff_user_id=staff_id)
        return customer.id


def _seed_plan(app, plan_code, price=Decimal("200.00")):
    from app.catalog.services import add_plan_price, create_plan, seed_canonical_catalog
    from app.extensions import db_session
    from app.models.catalog import Product

    with app.app_context():
        seed_canonical_catalog()
        product = Product(product_code=f"PROD_{plan_code}", name="Refund Test Product", is_active=True, is_sellable=True)
        db_session.add(product)
        db_session.flush()
        plan = create_plan(
            {
                "plan_code": plan_code,
                "product_id": product.id,
                "name": "Refund Test Plan",
                "billing_model": "MONTHLY",
                "effective_date": date.today() - timedelta(days=1),
            },
            actor_staff_user_id=None,
        )
        add_plan_price(plan, price, "USD", date.today() - timedelta(days=1), actor_staff_user_id=None)
        return plan.id


def _make_paid_invoice(app, staff_id, profile_id, customer_id, plan_id, finance_staff_id):
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
    return invoice, payment


def test_full_refund_lifecycle(app, seeded):
    staff_a, profile_a = _seed_sales_employee(app, "refunda@example.com")
    staff_b, _ = _seed_sales_employee(app, "refundb@example.com", role_codes=["FINANCE"])
    customer_id = _seed_customer(app, staff_a)
    plan_id = _seed_plan(app, "RA_PLAN")
    with app.app_context():
        from app.commercial_sales.refunds import approve_refund, confirm_refund, create_refund

        invoice, payment = _make_paid_invoice(app, staff_a, profile_a, customer_id, plan_id, staff_b)
        refund = create_refund(
            invoice, amount=invoice.total, reason="Customer requested cancellation", payment_record_id=payment.id,
            actor_employee_profile_id=profile_a, actor_staff_user_id=staff_a,
        )
        assert refund.status == "DRAFT"

        approve_refund(refund, actor_staff_user_id=staff_b)
        assert refund.status == "APPROVED"

        confirm_refund(refund, actor_staff_user_id=staff_b)
        assert refund.status == "PAID"
        assert invoice.status == "REFUNDED"


def test_partial_refund_marks_invoice_partially_refunded(app, seeded):
    staff_a, profile_a = _seed_sales_employee(app, "refundc@example.com")
    staff_b, _ = _seed_sales_employee(app, "refundd@example.com", role_codes=["FINANCE"])
    customer_id = _seed_customer(app, staff_a)
    plan_id = _seed_plan(app, "RC_PLAN", price=Decimal("200.00"))
    with app.app_context():
        from app.commercial_sales.refunds import approve_refund, confirm_refund, create_refund

        invoice, payment = _make_paid_invoice(app, staff_a, profile_a, customer_id, plan_id, staff_b)
        refund = create_refund(
            invoice, amount=Decimal("50.00"), reason="Partial service credit", payment_record_id=payment.id,
            actor_employee_profile_id=profile_a, actor_staff_user_id=staff_a,
        )
        approve_refund(refund, actor_staff_user_id=staff_b)
        confirm_refund(refund, actor_staff_user_id=staff_b)

        assert invoice.status == "PARTIALLY_REFUNDED"


def test_refund_requires_reason(app, seeded):
    staff_a, profile_a = _seed_sales_employee(app, "refunde@example.com")
    staff_b, _ = _seed_sales_employee(app, "refundf@example.com", role_codes=["FINANCE"])
    customer_id = _seed_customer(app, staff_a)
    plan_id = _seed_plan(app, "RE_PLAN")
    with app.app_context():
        from app.commercial_sales.errors import CommercialSalesError
        from app.commercial_sales.refunds import create_refund

        invoice, payment = _make_paid_invoice(app, staff_a, profile_a, customer_id, plan_id, staff_b)
        with pytest.raises(CommercialSalesError) as exc:
            create_refund(
                invoice, amount=Decimal("10.00"), reason="", payment_record_id=payment.id,
                actor_employee_profile_id=profile_a, actor_staff_user_id=staff_a,
            )
        assert exc.value.code == "REASON_REQUIRED"


def test_refund_exceeding_refundable_balance_rejected(app, seeded):
    staff_a, profile_a = _seed_sales_employee(app, "refundg@example.com")
    staff_b, _ = _seed_sales_employee(app, "refundh@example.com", role_codes=["FINANCE"])
    customer_id = _seed_customer(app, staff_a)
    plan_id = _seed_plan(app, "RG_PLAN", price=Decimal("200.00"))
    with app.app_context():
        from app.commercial_sales.errors import CommercialSalesError
        from app.commercial_sales.refunds import create_refund

        invoice, payment = _make_paid_invoice(app, staff_a, profile_a, customer_id, plan_id, staff_b)
        with pytest.raises(CommercialSalesError) as exc:
            create_refund(
                invoice, amount=Decimal("201.00"), reason="too much", payment_record_id=payment.id,
                actor_employee_profile_id=profile_a, actor_staff_user_id=staff_a,
            )
        assert exc.value.code == "REFUND_EXCEEDS_REFUNDABLE"


def test_second_refund_respects_prior_refund(app, seeded):
    """Prior refunds included in the refundable-balance calculation."""
    staff_a, profile_a = _seed_sales_employee(app, "refundi@example.com")
    staff_b, _ = _seed_sales_employee(app, "refundj@example.com", role_codes=["FINANCE"])
    customer_id = _seed_customer(app, staff_a)
    plan_id = _seed_plan(app, "RI_PLAN", price=Decimal("200.00"))
    with app.app_context():
        from app.commercial_sales.errors import CommercialSalesError
        from app.commercial_sales.refunds import approve_refund, confirm_refund, create_refund

        invoice, payment = _make_paid_invoice(app, staff_a, profile_a, customer_id, plan_id, staff_b)
        first = create_refund(
            invoice, amount=Decimal("150.00"), reason="first refund", payment_record_id=payment.id,
            actor_employee_profile_id=profile_a, actor_staff_user_id=staff_a,
        )
        approve_refund(first, actor_staff_user_id=staff_b)
        confirm_refund(first, actor_staff_user_id=staff_b)

        with pytest.raises(CommercialSalesError) as exc:
            create_refund(
                invoice, amount=Decimal("51.00"), reason="second refund too much", payment_record_id=payment.id,
                actor_employee_profile_id=profile_a, actor_staff_user_id=staff_a,
            )
        assert exc.value.code == "REFUND_EXCEEDS_REFUNDABLE"


def test_self_approval_forbidden(app, seeded):
    staff_a, profile_a = _seed_sales_employee(app, "refundk@example.com", role_codes=["FINANCE"])
    staff_b, _ = _seed_sales_employee(app, "refundl@example.com", role_codes=["FINANCE"])
    customer_id = _seed_customer(app, staff_a)
    plan_id = _seed_plan(app, "RK_PLAN")
    with app.app_context():
        from app.commercial_sales.errors import CommercialSalesError
        from app.commercial_sales.refunds import approve_refund, create_refund

        invoice, payment = _make_paid_invoice(app, staff_a, profile_a, customer_id, plan_id, staff_b)
        refund = create_refund(
            invoice, amount=Decimal("10.00"), reason="test", payment_record_id=payment.id,
            actor_employee_profile_id=profile_a, actor_staff_user_id=staff_a,
        )
        with pytest.raises(CommercialSalesError) as exc:
            approve_refund(refund, actor_staff_user_id=staff_a)
        assert exc.value.code == "SELF_APPROVAL_FORBIDDEN"


def test_void_refund(app, seeded):
    staff_a, profile_a = _seed_sales_employee(app, "refundm@example.com")
    staff_b, _ = _seed_sales_employee(app, "refundn@example.com", role_codes=["FINANCE"])
    customer_id = _seed_customer(app, staff_a)
    plan_id = _seed_plan(app, "RM_PLAN")
    with app.app_context():
        from app.commercial_sales.refunds import create_refund, void_refund

        invoice, payment = _make_paid_invoice(app, staff_a, profile_a, customer_id, plan_id, staff_b)
        refund = create_refund(
            invoice, amount=Decimal("10.00"), reason="test", payment_record_id=payment.id,
            actor_employee_profile_id=profile_a, actor_staff_user_id=staff_a,
        )
        void_refund(refund, actor_staff_user_id=staff_a)
        assert refund.status == "VOID"


def test_no_hard_delete_original_payment(app, seeded):
    """A Refund is a real, separate record -- never an edit to the
    original Payment (Non-Negotiable Rule 16 / payment-and-fulfillment-
    contract.md's own explicit rule)."""
    staff_a, profile_a = _seed_sales_employee(app, "refundo@example.com")
    staff_b, _ = _seed_sales_employee(app, "refundp@example.com", role_codes=["FINANCE"])
    customer_id = _seed_customer(app, staff_a)
    plan_id = _seed_plan(app, "RO_PLAN")
    with app.app_context():
        from app.commercial_sales.refunds import approve_refund, confirm_refund, create_refund

        invoice, payment = _make_paid_invoice(app, staff_a, profile_a, customer_id, plan_id, staff_b)
        original_amount = payment.amount
        original_status = payment.status

        refund = create_refund(
            invoice, amount=invoice.total, reason="full refund", payment_record_id=payment.id,
            actor_employee_profile_id=profile_a, actor_staff_user_id=staff_a,
        )
        approve_refund(refund, actor_staff_user_id=staff_b)
        confirm_refund(refund, actor_staff_user_id=staff_b)

        assert payment.amount == original_amount
        assert payment.status == original_status  # never edited to reflect the refund
