"""Regression tests for three confirmed cross-screen metric-consistency
bugs: (1) Attention Center's 'Pending Commission Approvals' filtered on a
CommissionLedgerEntry.status value ("PENDING") no real code path ever
assigns, so the section silently never rendered; (2)
finance_commercial_dashboard()'s outstanding_invoice_total summed gross
invoice totals, never netting confirmed payment allocations, and blended
every currency into one figure; (3) the Attention Center and the
Management operational dashboard used two different definitions of
"overdue invoice" (one didn't require a positive remaining balance, the
other's status filter silently excluded PARTIALLY_REFUNDED).

Each test is written to fail against the pre-fix code and pass against
the fix -- see the AUDIT comments in app/attention/service.py,
app/commercial_sales/dashboards.py, and
app/operational_reports/dashboards.py for the corresponding fixes.
"""
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
        customer = create_customer({"legal_name": "Cross-Screen Test Customer Co"}, actor_staff_user_id=staff_id)
        return customer.id


def _seed_plan(app, plan_code, currency="USD", price=Decimal("1000.00")):
    from app.catalog.services import add_plan_price, create_plan, seed_canonical_catalog
    from app.extensions import db_session
    from app.models.catalog import Product

    with app.app_context():
        seed_canonical_catalog()
        product = Product(product_code=f"PROD_{plan_code}", name="Cross-Screen Test Product", is_active=True, is_sellable=True)
        db_session.add(product)
        db_session.flush()
        plan = create_plan(
            {
                "plan_code": plan_code,
                "product_id": product.id,
                "name": "Cross-Screen Test Plan",
                "billing_model": "MONTHLY",
                "effective_date": date.today() - timedelta(days=1),
            },
            actor_staff_user_id=None,
        )
        add_plan_price(plan, price, currency, date.today() - timedelta(days=1), actor_staff_user_id=None)
        return plan.id


def _grant_commission_plan(app, profile_id, staff_id, rate=Decimal("10.0")):
    from app.commissions.management import assign_employee_commission_plan, create_commission_plan, create_commission_rule_version

    plan = create_commission_plan({"plan_code": f"XS_PLAN_{uuid.uuid4().hex[:8]}", "name": "Cross-Screen Commission Plan"}, staff_id)
    create_commission_rule_version(
        plan, rule_type="PERCENTAGE_OF_PAYMENT", rate_percentage=rate,
        effective_from=date.today() - timedelta(days=1), actor_staff_user_id=staff_id,
    )
    assign_employee_commission_plan(profile_id, plan, effective_from=date.today() - timedelta(days=1), actor_staff_user_id=staff_id)


def _make_issued_invoice(app, staff_id, profile_id, customer_id, plan_id, currency="USD", due_date=None):
    from app.commercial_sales.invoices import create_invoice_from_order, issue_invoice
    from app.commercial_sales.quotes import add_quote_line, create_quote, record_customer_decision, submit_quote
    from app.commercial_sales.sales_orders import confirm_order, create_order_from_quote

    quote = create_quote({"customer_id": customer_id, "currency": currency}, actor_employee_profile_id=profile_id, actor_staff_user_id=staff_id)
    add_quote_line(quote, plan_id=plan_id, addon_id=None, quantity=1, actor_staff_user_id=staff_id)
    submit_quote(quote, actor_staff_user_id=staff_id)
    record_customer_decision(quote, accepted=True, actor_staff_user_id=staff_id)
    order = create_order_from_quote(quote, actor_employee_profile_id=profile_id, actor_staff_user_id=staff_id, idempotency_key=str(uuid.uuid4()))
    confirm_order(order, actor_staff_user_id=staff_id)
    invoice = create_invoice_from_order(
        order, actor_employee_profile_id=profile_id, actor_staff_user_id=staff_id, idempotency_key=str(uuid.uuid4()), due_date=due_date
    )
    issue_invoice(invoice, actor_staff_user_id=staff_id)
    return invoice


def _make_confirmed_payment(app, customer_id, amount, currency, submitter_staff_id, confirmer_staff_id):
    from app.commercial_sales.payments import confirm_payment, submit_payment

    payment = submit_payment(
        customer_id=customer_id, amount=amount, currency=currency, method="CASH",
        payment_date=date.today(), actor_staff_user_id=submitter_staff_id,
    )
    return confirm_payment(payment, actor_staff_user_id=confirmer_staff_id)


# ------------------------------------------------------- Bug 1: commissions --

def test_earned_commission_appears_as_pending_commission_approval_attention_item(app, seeded):
    """AUDIT-owner-cross-screen bug 1: the real earning trigger
    (commissions/ledger.py::post_earning_for_allocation) always inserts
    status="EARNED" -- never "PENDING", which no code path assigns. Before
    the fix, attention/service.py's query for
    CommissionLedgerEntry.status == "PENDING" could never match a real
    row, so an EARNED entry never showed up under "Pending Commission
    Approvals" and never counted toward the topbar badge."""
    from app.attention.service import count_attention_items, get_attention_items
    from app.commercial_sales.allocation import allocate_payment
    from app.extensions import db_session
    from app.models.staff import StaffUser

    staff_a, profile_a = _seed_sales_employee(app, "xs-comm-a@example.com")
    staff_fin, _profile_fin = _seed_sales_employee(app, "xs-comm-fin@example.com", role_codes=["FINANCE"])
    customer_id = _seed_customer(app, staff_a)
    plan_id = _seed_plan(app, "XS_COMM_PLAN")
    with app.app_context():
        _grant_commission_plan(app, profile_a, staff_a)
        invoice = _make_issued_invoice(app, staff_a, profile_a, customer_id, plan_id)
        payment = _make_confirmed_payment(app, customer_id, Decimal("1000.00"), "USD", staff_a, staff_fin)
        allocate_payment(payment=payment, invoice=invoice, amount=Decimal("1000.00"), actor_staff_user_id=staff_fin)

    with app.test_request_context():
        actor_fin = db_session.get(StaffUser, staff_fin)
        items = get_attention_items(actor_fin)
        commission_items = [i for i in items if i.category == "pending_commissions"]
        assert len(commission_items) == 1, "the EARNED commission entry never appeared under 'Pending Commission Approvals'"
        assert count_attention_items(actor_fin) >= 1


# ------------------------------------------------ Bug 2: outstanding total --

def test_outstanding_invoice_total_nets_confirmed_payment_not_gross(app, seeded):
    """AUDIT-owner-cross-screen bug 2: the figure must be the invoice's
    remaining balance (total minus confirmed payment allocations already
    received), never the invoice's gross total."""
    from app.commercial_sales.allocation import allocate_payment
    from app.commercial_sales.dashboards import finance_commercial_dashboard

    staff_a, profile_a = _seed_sales_employee(app, "xs-net-a@example.com")
    staff_fin, _profile_fin = _seed_sales_employee(app, "xs-net-fin@example.com", role_codes=["FINANCE"])
    customer_id = _seed_customer(app, staff_a)
    plan_id = _seed_plan(app, "XS_NET_PLAN", currency="USD", price=Decimal("1000.00"))
    with app.app_context():
        invoice = _make_issued_invoice(app, staff_a, profile_a, customer_id, plan_id, currency="USD")
        payment = _make_confirmed_payment(app, customer_id, Decimal("400.00"), "USD", staff_a, staff_fin)
        allocate_payment(payment=payment, invoice=invoice, amount=Decimal("400.00"), actor_staff_user_id=staff_fin)

        dash = finance_commercial_dashboard()

    assert dash["outstanding_invoice_totals"]["USD"] == Decimal("600.00")  # 1000.00 total - 400.00 already allocated
    assert dash["outstanding_invoice_totals"]["USD"] != Decimal("1000.00")  # never the gross invoice total


def test_outstanding_invoice_total_never_blends_currencies(app, seeded):
    """AUDIT-owner-cross-screen bug 2: two invoices in different currencies
    must never be summed into one blended figure -- each currency gets its
    own, independent outstanding total."""
    from app.commercial_sales.dashboards import finance_commercial_dashboard

    staff_a, profile_a = _seed_sales_employee(app, "xs-curr-a@example.com")
    customer_id = _seed_customer(app, staff_a)
    plan_usd = _seed_plan(app, "XS_CURR_PLAN_USD", currency="USD", price=Decimal("1000.00"))
    plan_jod = _seed_plan(app, "XS_CURR_PLAN_JOD", currency="JOD", price=Decimal("500.00"))
    with app.app_context():
        _make_issued_invoice(app, staff_a, profile_a, customer_id, plan_usd, currency="USD")
        _make_issued_invoice(app, staff_a, profile_a, customer_id, plan_jod, currency="JOD")

        dash = finance_commercial_dashboard()

    assert dash["outstanding_invoice_totals"]["USD"] == Decimal("1000.00")
    assert dash["outstanding_invoice_totals"]["JOD"] == Decimal("500.00")
    # Never a blended 1500.00 appearing under either currency.
    assert dash["outstanding_invoice_totals"]["USD"] != Decimal("1500.00")
    assert dash["outstanding_invoice_totals"]["JOD"] != Decimal("1500.00")


# --------------------------------------------------- Bug 3: overdue invoices --

def test_overdue_partially_refunded_invoice_with_positive_balance_counted_by_both_screens(app, seeded):
    """AUDIT-owner-cross-screen bug 3: a PARTIALLY_REFUNDED invoice (a real,
    reachable INVOICE_STATUSES value) that is past due and still has a
    positive remaining balance must be counted as overdue by BOTH the
    Attention Center and the Management operational dashboard -- before
    the fix, the Attention Center's status.in_(("ISSUED", "PARTIALLY_PAID"))
    filter silently excluded PARTIALLY_REFUNDED entirely."""
    from app.attention.service import get_attention_items
    from app.commercial_sales.allocation import allocate_payment
    from app.commercial_sales.refunds import approve_refund, confirm_refund, create_refund
    from app.extensions import db_session
    from app.models.staff import StaffUser
    from app.operational_reports.dashboards import management_operational_dashboard

    staff_a, profile_a = _seed_sales_employee(app, "xs-pr-a@example.com")
    staff_fin, _profile_fin = _seed_sales_employee(app, "xs-pr-fin@example.com", role_codes=["FINANCE"])
    customer_id = _seed_customer(app, staff_a)
    plan_id = _seed_plan(app, "XS_PR_PLAN", currency="USD", price=Decimal("1000.00"))
    past_due = date.today() - timedelta(days=5)
    with app.app_context():
        invoice = _make_issued_invoice(app, staff_a, profile_a, customer_id, plan_id, currency="USD", due_date=past_due)
        payment = _make_confirmed_payment(app, customer_id, Decimal("700.00"), "USD", staff_a, staff_fin)
        allocate_payment(payment=payment, invoice=invoice, amount=Decimal("700.00"), actor_staff_user_id=staff_fin)

        # Partial refund of part of the 700.00 actually collected (never
        # the invoice's full 1000.00 total) -- refunded (200) < collected
        # (700), so the invoice becomes PARTIALLY_REFUNDED while still
        # owing 1000.00 - 700.00 = 300.00.
        refund = create_refund(
            invoice, amount=Decimal("200.00"), reason="Billing correction", payment_record_id=payment.id,
            actor_employee_profile_id=profile_a, actor_staff_user_id=staff_a,
        )
        refund = approve_refund(refund, actor_staff_user_id=staff_fin)
        confirm_refund(refund, actor_staff_user_id=staff_fin)

        assert invoice.status == "PARTIALLY_REFUNDED"
        assert (invoice.total - Decimal("700.00")) == Decimal("300.00")
        invoice_number = invoice.invoice_number

    with app.test_request_context():
        actor_fin = db_session.get(StaffUser, staff_fin)
        items = get_attention_items(actor_fin)
        overdue_titles = " ".join(i.title for i in items if i.category == "overdue_invoices")
        assert invoice_number in overdue_titles, "PARTIALLY_REFUNDED overdue invoice missing from the Attention Center"

    with app.app_context():
        dash = management_operational_dashboard("USD")
        assert dash["overdue_invoices"] == 1


def test_overdue_invoice_with_zero_balance_counted_by_neither_screen(app, seeded):
    """AUDIT-owner-cross-screen bug 3: a past-due invoice with NOTHING left
    to collect must never be counted as overdue by either screen,
    regardless of its status label -- the shared is_invoice_overdue()
    predicate always requires a strictly positive remaining balance. The
    invoice is force-labelled back to ISSUED after full allocation
    (bypassing the normal resolve_invoice_status() transition to PAID) so
    this test actually exercises the balance check itself, not merely a
    status filter that would already exclude PAID on both screens even
    before this fix."""
    from app.attention.service import get_attention_items
    from app.commercial_sales.allocation import allocate_payment
    from app.extensions import db_session
    from app.models.staff import StaffUser
    from app.operational_reports.dashboards import management_operational_dashboard

    staff_a, profile_a = _seed_sales_employee(app, "xs-zero-a@example.com")
    staff_fin, _profile_fin = _seed_sales_employee(app, "xs-zero-fin@example.com", role_codes=["FINANCE"])
    customer_id = _seed_customer(app, staff_a)
    plan_id = _seed_plan(app, "XS_ZERO_PLAN", currency="USD", price=Decimal("1000.00"))
    past_due = date.today() - timedelta(days=5)
    with app.app_context():
        invoice = _make_issued_invoice(app, staff_a, profile_a, customer_id, plan_id, currency="USD", due_date=past_due)
        payment = _make_confirmed_payment(app, customer_id, Decimal("1000.00"), "USD", staff_a, staff_fin)
        allocate_payment(payment=payment, invoice=invoice, amount=Decimal("1000.00"), actor_staff_user_id=staff_fin)

        invoice.status = "ISSUED"
        db_session.commit()
        invoice_number = invoice.invoice_number

    with app.test_request_context():
        actor_fin = db_session.get(StaffUser, staff_fin)
        items = get_attention_items(actor_fin)
        overdue_titles = " ".join(i.title for i in items if i.category == "overdue_invoices")
        assert invoice_number not in overdue_titles

    with app.app_context():
        dash = management_operational_dashboard("USD")
        assert dash["overdue_invoices"] == 0
