"""Phase 9.5D Milestone 9 -- Commercial Invoice domain service tests."""
from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from freezegun import freeze_time

from tests.conftest import make_staff

_employee_number_counter = iter(range(1, 100000))

# A fixed absolute instant used only to pin issue_invoice()'s notion of "today".
#
# The bug this closes: issue_invoice() computes due_date from utcnow().date()
# (a UTC calendar date, app/models/base.py::utcnow) while this test asserted
# against date.today() (a LOCAL calendar date). On any machine east of UTC the
# two disagree for the first N hours of every local day -- at UTC+3 (Jordan,
# this project's real locale) that is a deterministic 3-hour window per day in
# which due_date is off by exactly one day and the test fails. Not a race; a
# real multi-hour window.
#
# Under freeze_time, freezegun's tz_offset defaults to 0, so BOTH
# datetime.now(timezone.utc) and date.today() return this exact UTC date on
# every machine -- the runner's local timezone stops being an input at all.
# That is what makes this safe across the entire IANA offset range (UTC on
# GitHub Actions, UTC+3 locally, UTC-11 or UTC+14 for anyone else) rather than
# just the two we happen to use. Midday rather than midnight is belt-and-braces
# in case a tz_offset is ever added: no offset in [-12, +12] can push 12:00 UTC
# into a neighbouring calendar day.
DUE_DATE_FREEZE_INSTANT = datetime(2026, 6, 15, 12, 0, 0, tzinfo=timezone.utc)


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
        customer = create_customer({"legal_name": "Invoice Test Customer Co"}, actor_staff_user_id=staff_id)
        return customer.id


def _seed_plan(app, plan_code, price=Decimal("150.00")):
    from app.catalog.services import add_plan_price, create_plan, seed_canonical_catalog
    from app.extensions import db_session
    from app.models.catalog import Product

    with app.app_context():
        seed_canonical_catalog()
        product = Product(product_code=f"PROD_{plan_code}", name="Invoice Test Product", is_active=True, is_sellable=True)
        db_session.add(product)
        db_session.flush()
        plan = create_plan(
            {
                "plan_code": plan_code,
                "product_id": product.id,
                "name": "Invoice Test Plan",
                "billing_model": "MONTHLY",
                "effective_date": date.today() - timedelta(days=1),
            },
            actor_staff_user_id=None,
        )
        add_plan_price(plan, price, "USD", date.today() - timedelta(days=1), actor_staff_user_id=None)
        return plan.id


def _make_confirmed_order(app, staff_id, profile_id, customer_id, plan_id):
    from app.commercial_sales.quotes import add_quote_line, create_quote, record_customer_decision, submit_quote
    from app.commercial_sales.sales_orders import confirm_order, create_order_from_quote

    quote = create_quote({"customer_id": customer_id, "currency": "USD"}, actor_employee_profile_id=profile_id, actor_staff_user_id=staff_id)
    add_quote_line(quote, plan_id=plan_id, addon_id=None, quantity=1, actor_staff_user_id=staff_id)
    submit_quote(quote, actor_staff_user_id=staff_id)
    record_customer_decision(quote, accepted=True, actor_staff_user_id=staff_id)
    order = create_order_from_quote(quote, actor_employee_profile_id=profile_id, actor_staff_user_id=staff_id, idempotency_key=str(uuid.uuid4()))
    confirm_order(order, actor_staff_user_id=staff_id)
    return order


def test_create_invoice_from_confirmed_order(app, seeded):
    staff_id, profile_id = _seed_sales_employee(app, "invoicea@example.com")
    customer_id = _seed_customer(app, staff_id)
    plan_id = _seed_plan(app, "IA_PLAN")
    with app.app_context():
        from app.commercial_sales.invoices import create_invoice_from_order

        order = _make_confirmed_order(app, staff_id, profile_id, customer_id, plan_id)
        invoice = create_invoice_from_order(order, actor_employee_profile_id=profile_id, actor_staff_user_id=staff_id, idempotency_key=str(uuid.uuid4()))

        assert invoice.status == "DRAFT"
        assert invoice.invoice_number.startswith("INV-")
        assert invoice.total == Decimal("150.00")
        assert len(invoice.lines) == 1


def test_invoice_requires_confirmed_order(app, seeded):
    staff_id, profile_id = _seed_sales_employee(app, "invoiceb@example.com")
    customer_id = _seed_customer(app, staff_id)
    plan_id = _seed_plan(app, "IB_PLAN")
    with app.app_context():
        from app.commercial_sales.errors import CommercialSalesError
        from app.commercial_sales.invoices import create_invoice_from_order
        from app.commercial_sales.quotes import add_quote_line, create_quote, record_customer_decision, submit_quote
        from app.commercial_sales.sales_orders import create_order_from_quote

        quote = create_quote({"customer_id": customer_id, "currency": "USD"}, actor_employee_profile_id=profile_id, actor_staff_user_id=staff_id)
        add_quote_line(quote, plan_id=plan_id, addon_id=None, quantity=1, actor_staff_user_id=staff_id)
        submit_quote(quote, actor_staff_user_id=staff_id)
        record_customer_decision(quote, accepted=True, actor_staff_user_id=staff_id)
        order = create_order_from_quote(quote, actor_employee_profile_id=profile_id, actor_staff_user_id=staff_id, idempotency_key=str(uuid.uuid4()))
        # order still DRAFT, not confirmed

        with pytest.raises(CommercialSalesError) as exc:
            create_invoice_from_order(order, actor_employee_profile_id=profile_id, actor_staff_user_id=staff_id, idempotency_key=str(uuid.uuid4()))
        assert exc.value.code == "INVALID_ORDER_TRANSITION"


def test_issue_invoice_sets_default_due_date(app, seeded):
    staff_id, profile_id = _seed_sales_employee(app, "invoicec@example.com")
    customer_id = _seed_customer(app, staff_id)
    plan_id = _seed_plan(app, "IC_PLAN")
    with app.app_context():
        from app.commercial_sales.invoices import create_invoice_from_order, issue_invoice

        order = _make_confirmed_order(app, staff_id, profile_id, customer_id, plan_id)
        invoice = create_invoice_from_order(order, actor_employee_profile_id=profile_id, actor_staff_user_id=staff_id, idempotency_key=str(uuid.uuid4()))

        # Freeze ONLY the issue_invoice() call and its assertions. Deliberately
        # NOT the seeding above: _seed_plan() registers a PlanPrice effective
        # from the REAL date.today() - 1, and catalog price resolution requires
        # effective_date <= as_of (app/catalog/services.py:250-257). Freezing
        # the seeding to a fixed past date would make that price not-yet-
        # effective and break add_quote_line() inside _make_confirmed_order().
        with freeze_time(DUE_DATE_FREEZE_INSTANT):
            issue_invoice(invoice, actor_staff_user_id=staff_id)

            assert invoice.status == "ISSUED"
            assert invoice.issued_at is not None
            assert invoice.due_date == DUE_DATE_FREEZE_INSTANT.date() + timedelta(days=30)


def test_void_invoice_with_no_payments(app, seeded):
    staff_id, profile_id = _seed_sales_employee(app, "invoiced@example.com")
    customer_id = _seed_customer(app, staff_id)
    plan_id = _seed_plan(app, "ID_PLAN")
    with app.app_context():
        from app.commercial_sales.invoices import create_invoice_from_order, issue_invoice, void_invoice

        order = _make_confirmed_order(app, staff_id, profile_id, customer_id, plan_id)
        invoice = create_invoice_from_order(order, actor_employee_profile_id=profile_id, actor_staff_user_id=staff_id, idempotency_key=str(uuid.uuid4()))
        issue_invoice(invoice, actor_staff_user_id=staff_id)

        void_invoice(invoice, reason="customer cancelled", actor_staff_user_id=staff_id)
        assert invoice.status == "VOID"


def test_void_requires_reason(app, seeded):
    staff_id, profile_id = _seed_sales_employee(app, "invoicee@example.com")
    customer_id = _seed_customer(app, staff_id)
    plan_id = _seed_plan(app, "IE_PLAN")
    with app.app_context():
        from app.commercial_sales.errors import CommercialSalesError
        from app.commercial_sales.invoices import create_invoice_from_order, void_invoice

        order = _make_confirmed_order(app, staff_id, profile_id, customer_id, plan_id)
        invoice = create_invoice_from_order(order, actor_employee_profile_id=profile_id, actor_staff_user_id=staff_id, idempotency_key=str(uuid.uuid4()))

        with pytest.raises(CommercialSalesError) as exc:
            void_invoice(invoice, reason="", actor_staff_user_id=staff_id)
        assert exc.value.code == "REASON_REQUIRED"


def test_paid_invoice_cannot_be_voided(app, seeded):
    """A CommercialRefund-worthy scenario, not a bare void -- simulates a
    confirmed allocation existing against the invoice (Milestone 11's real
    table, already migrated) directly, since Milestone 10/11's services
    don't exist yet in this milestone's scope."""
    staff_id, profile_id = _seed_sales_employee(app, "invoicef@example.com")
    customer_id = _seed_customer(app, staff_id)
    plan_id = _seed_plan(app, "IF_PLAN")
    with app.app_context():
        from app.commercial_sales.errors import CommercialSalesError
        from app.commercial_sales.invoices import create_invoice_from_order, issue_invoice, void_invoice
        from app.extensions import db_session
        from app.models.base import utcnow
        from app.models.commercial_sales import PaymentAllocation
        from app.models.subscriptions import PaymentRecord

        order = _make_confirmed_order(app, staff_id, profile_id, customer_id, plan_id)
        invoice = create_invoice_from_order(order, actor_employee_profile_id=profile_id, actor_staff_user_id=staff_id, idempotency_key=str(uuid.uuid4()))
        issue_invoice(invoice, actor_staff_user_id=staff_id)

        payment = PaymentRecord(
            customer_id=customer_id, currency="USD", amount=Decimal("150.00"), method="CASH", status="CONFIRMED",
            commercial_invoice_id=invoice.id, payment_date=date.today(),
        )
        db_session.add(payment)
        db_session.flush()
        db_session.add(
            PaymentAllocation(
                payment_record_id=payment.id, commercial_invoice_id=invoice.id, allocated_amount=Decimal("150.00"),
                currency="USD", allocated_by_staff_user_id=staff_id, allocated_at=utcnow(), version=1,
            )
        )
        db_session.commit()

        with pytest.raises(CommercialSalesError) as exc:
            void_invoice(invoice, reason="mistake", actor_staff_user_id=staff_id)
        assert exc.value.code == "INVALID_INVOICE_TRANSITION"


def test_idempotent_replay_returns_same_invoice(app, seeded):
    staff_id, profile_id = _seed_sales_employee(app, "invoiceg@example.com")
    customer_id = _seed_customer(app, staff_id)
    plan_id = _seed_plan(app, "IG_PLAN")
    with app.app_context():
        from app.commercial_sales.invoices import create_invoice_from_order

        order = _make_confirmed_order(app, staff_id, profile_id, customer_id, plan_id)
        key = str(uuid.uuid4())
        first = create_invoice_from_order(order, actor_employee_profile_id=profile_id, actor_staff_user_id=staff_id, idempotency_key=key)
        second = create_invoice_from_order(order, actor_employee_profile_id=profile_id, actor_staff_user_id=staff_id, idempotency_key=key)
        assert first.id == second.id


def test_no_statutory_claim_fields_present(app, seeded):
    """Structural proof of Non-Negotiable Rule 15: no tax-registration
    number, fiscal QR/UUID, or e-invoicing submission-status field exists
    anywhere on the model -- confirmed by column introspection, not just
    documentation."""
    from app.models.commercial_sales import CommercialInvoice

    columns = {c.name for c in CommercialInvoice.__table__.columns}
    forbidden_terms = ["fiscal", "e_invoice", "tax_registration", "government", "qr_code"]
    for term in forbidden_terms:
        assert not any(term in col for col in columns), f"unexpected statutory-shaped column matching {term!r}: {columns}"
