"""Regression tests for confirmed cross-screen metric-consistency bugs --
two screens reporting different answers about the same money.

The first three: (1) Attention Center's 'Pending Commission Approvals' filtered on a
CommissionLedgerEntry.status value ("PENDING") no real code path ever
assigns, so the section silently never rendered; (2)
finance_commercial_dashboard()'s outstanding_invoice_total summed gross
invoice totals, never netting confirmed payment allocations, and blended
every currency into one figure; (3) the Attention Center and the
Management operational dashboard used two different definitions of
"overdue invoice" (one didn't require a positive remaining balance, the
other's status filter silently excluded PARTIALLY_REFUNDED).

A later wave added five more, all of the same family: (4) the Finance
dashboard template still bound the singular outstanding_invoice_total
after the service renamed it, so the row was permanently blank while its
JSON twin showed real money; (5) an employee's own commission earnings
were summed across currencies; (6) Management expense totals counted
DRAFT/SUBMITTED/RETURNED/REJECTED expenses, so they could never
reconcile with the paid-expenses figure beside them; (7) the expense
approval and duplicate queues were company-wide on a currency-scoped
screen; (8) the Attention Center's commission badge counted one status
and deep-linked to another, so clicking it always landed on an empty
list.

Each test is written to fail against the pre-fix code and pass against
the fix -- see the AUDIT comments in app/attention/service.py,
app/commercial_sales/dashboards.py, and
app/operational_reports/dashboards.py for the corresponding fixes.
tests/test_owner_metric_contracts.py is the structural counterpart: it
stops a renamed key from ever silently blanking a screen again.
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


# ------------------------------------ Bug 4: the Finance screen's blank row --

def test_finance_dashboard_screen_renders_the_outstanding_total_per_currency(app, client, seeded):
    """AUDIT-owner-cross-screen bug 4: finance_dashboard.html read
    `data.outstanding_invoice_total` (singular) after the service renamed
    the key to the per-currency `outstanding_invoice_totals`. Jinja's
    default Undefined renders as the empty string, so the row was
    permanently BLANK on the screen while the JSON twin and the
    Management dashboard both reported real money. Asserted through the
    rendered HTML, because the service was never the broken half."""
    from tests.conftest import force_login

    staff_a, profile_a = _seed_sales_employee(app, "xs-blank-a@example.com")
    staff_fin, _profile_fin = _seed_sales_employee(app, "xs-blank-fin@example.com", role_codes=["FINANCE"])
    customer_id = _seed_customer(app, staff_a)
    plan_id = _seed_plan(app, "XS_BLANK_PLAN", currency="USD", price=Decimal("1000.00"))
    with app.app_context():
        _make_issued_invoice(app, staff_a, profile_a, customer_id, plan_id, currency="USD")

    force_login(client, app, staff_fin)
    body = client.get("/commercial-dashboard/finance").get_data(as_text=True)

    assert "1,000" in body, "the Finance dashboard's outstanding invoice total rendered blank"
    assert "USD" in body, "the outstanding total rendered without naming its currency"


# ------------------------------ Bug 5: blended own-commission earnings --

def test_employee_commission_earnings_are_scoped_to_one_currency(app, seeded):
    """AUDIT-owner-cross-screen bug 5: own_commission_sum() had no currency
    filter, so an employee earning in USD and in JOD saw the two ADDED
    together on "My commission earnings" -- a number in no currency, on
    the one screen the employee checks against their own payslip."""
    from app.commercial_sales.allocation import allocate_payment
    from app.commercial_sales.dashboards import employee_commercial_dashboard

    staff_a, profile_a = _seed_sales_employee(app, "xs-blend-a@example.com")
    staff_fin, _profile_fin = _seed_sales_employee(app, "xs-blend-fin@example.com", role_codes=["FINANCE"])
    customer_id = _seed_customer(app, staff_a)
    plan_usd = _seed_plan(app, "XS_BLEND_USD", currency="USD", price=Decimal("1000.00"))
    plan_jod = _seed_plan(app, "XS_BLEND_JOD", currency="JOD", price=Decimal("500.00"))
    with app.app_context():
        _grant_commission_plan(app, profile_a, staff_a)  # 10% of every allocated payment

        invoice_usd = _make_issued_invoice(app, staff_a, profile_a, customer_id, plan_usd, currency="USD")
        payment_usd = _make_confirmed_payment(app, customer_id, Decimal("1000.00"), "USD", staff_a, staff_fin)
        allocate_payment(payment=payment_usd, invoice=invoice_usd, amount=Decimal("1000.00"), actor_staff_user_id=staff_fin)

        invoice_jod = _make_issued_invoice(app, staff_a, profile_a, customer_id, plan_jod, currency="JOD")
        payment_jod = _make_confirmed_payment(app, customer_id, Decimal("500.00"), "JOD", staff_a, staff_fin)
        allocate_payment(payment=payment_jod, invoice=invoice_jod, amount=Decimal("500.00"), actor_staff_user_id=staff_fin)

        usd_dash = employee_commercial_dashboard(profile_a, staff_a, "USD")
        jod_dash = employee_commercial_dashboard(profile_a, staff_a, "JOD")

    assert usd_dash["commission_earned_unapproved"] == Decimal("100.00")  # 10% of 1000 USD
    assert jod_dash["commission_earned_unapproved"] == Decimal("50.00")   # 10% of 500 JOD
    # Never the blended 150.00 the un-filtered SUM used to report to both.
    assert usd_dash["commission_earned_unapproved"] != Decimal("150.00")
    assert jod_dash["commission_earned_unapproved"] != Decimal("150.00")
    # Scoping the headline figures hides nothing: every currency the
    # employee actually earned in is still reported, keyed by currency.
    assert usd_dash["commission_totals_by_currency"]["USD"]["earned_unapproved"] == Decimal("100.00")
    assert usd_dash["commission_totals_by_currency"]["JOD"]["earned_unapproved"] == Decimal("50.00")


# ------------------------- Bug 6: unapproved expenses inflating Management --

def test_management_expense_totals_count_authorized_spend_only(app, seeded):
    """AUDIT-owner-cross-screen bug 6: the totals excluded only VOID, so a
    DRAFT nobody submitted, a SUBMITTED request nobody approved, a
    RETURNED one and an outright REJECTED one all inflated the company's
    category/per-employee totals -- which could then never reconcile with
    the paid-expenses figure on the same screen, since only APPROVED+
    expenses can ever have a recorded payment.

    Statuses are set directly (rather than driven through the approval
    flow) precisely so this test exercises the aggregation's status
    filter itself and nothing else -- the same technique, and the same
    reason, as the zero-balance overdue test above."""
    from app.expenses.lifecycle import create_expense
    from app.expenses.payees import create_payee
    from app.extensions import db_session
    from app.models.expenses import ExpenseCategory
    from app.operational_reports.dashboards import management_operational_dashboard

    staff_a, profile_a = _seed_sales_employee(app, "xs-exp-a@example.com")
    with app.app_context():
        category = ExpenseCategory(category_code=f"XS-{uuid.uuid4().hex[:8]}", name="Cross-Screen Category", is_active=True)
        db_session.add(category)
        db_session.flush()
        payee = create_payee(
            payee_type="EXTERNAL", display_name="Cross-Screen Vendor", employee_profile_id=None,
            external_contact_reference=None, created_by_staff_user_id=staff_a,
        )
        for amount, status in (
            ("100.00", "APPROVED"), ("10.00", "DRAFT"), ("20.00", "SUBMITTED"),
            ("30.00", "RETURNED"), ("40.00", "REJECTED"), ("50.00", "VOID"),
        ):
            expense = create_expense(
                category_id=category.id, payee_id=payee.id, amount=Decimal(amount), currency="USD",
                expense_date=date.today(), description=f"cross-screen {status}", external_reference=None,
                payment_method="CASH", payment_reference=None, entered_by_employee_profile_id=profile_a,
            )
            expense.status = status
        db_session.commit()
        category_name = category.name

        dash = management_operational_dashboard("USD")

    # Only the APPROVED 100.00 is authorized spend; 10+20+30+40 must not
    # appear anywhere in the total (the old filter reported 200.00).
    assert dash["expense_totals_by_category"][category_name] == "100.00"
    assert dash["expense_totals_by_employee"][str(profile_a)] == "100.00"


# --------------------------- Bug 7: un-scoped queues on a currency screen --

def test_expense_approval_and_duplicate_queues_are_currency_scoped(app, seeded):
    """AUDIT-owner-cross-screen bug 7: both queue counts were company-wide
    while every figure beside them on the same screen was scoped to one
    currency, so the USD dashboard silently counted JOD work its viewer
    could see nowhere else on the page."""
    from app.expenses.lifecycle import create_expense, submit_expense
    from app.expenses.payees import create_payee
    from app.extensions import db_session
    from app.models.expenses import ExpenseCategory
    from app.operational_reports.dashboards import finance_operational_dashboard, management_operational_dashboard

    staff_a, profile_a = _seed_sales_employee(app, "xs-cur-a@example.com")
    with app.app_context():
        category = ExpenseCategory(category_code=f"XS-{uuid.uuid4().hex[:8]}", name="Cross-Screen Currency", is_active=True)
        db_session.add(category)
        db_session.flush()
        payee = create_payee(
            payee_type="EXTERNAL", display_name="Cross-Screen Vendor 2", employee_profile_id=None,
            external_contact_reference=None, created_by_staff_user_id=staff_a,
        )
        shared_reference = f"XS-REF-{uuid.uuid4().hex[:8]}"

        def _new_expense(amount, currency, external_reference=None):
            return create_expense(
                category_id=category.id, payee_id=payee.id, amount=Decimal(amount), currency=currency,
                expense_date=date.today(), description="cross-screen queue", external_reference=external_reference,
                payment_method="CASH", payment_reference=None, entered_by_employee_profile_id=profile_a,
            )

        # One PENDING approval, in JOD only -- the USD screens must not see it.
        submit_expense(_new_expense("75.00", "JOD"), actor_staff_user_id=staff_a)
        # A duplicate external_reference pair, in JOD only -- likewise.
        _new_expense("15.00", "JOD", shared_reference)
        _new_expense("15.00", "JOD", shared_reference)
        db_session.commit()

        usd_management = management_operational_dashboard("USD")
        jod_management = management_operational_dashboard("JOD")
        usd_finance = finance_operational_dashboard("USD")

    assert usd_management["approval_queue_count"] == 0, "USD dashboard counted a JOD approval request"
    assert usd_management["duplicate_review_queue_count"] == 0, "USD dashboard counted a JOD duplicate pair"
    assert usd_finance["expense_approvals_pending"] == 0, "USD Finance dashboard counted a JOD approval request"
    # The work is not lost -- it is counted under the currency it belongs to.
    assert jod_management["approval_queue_count"] == 1
    assert jod_management["duplicate_review_queue_count"] == 1


# ------------------- Bug 8: a badge that always linked to an empty list --

def test_pending_commission_badge_links_to_the_status_it_counted(app, seeded):
    """AUDIT-owner-cross-screen bug 8: the Attention Center counted
    status == "EARNED" but deep-linked to list_commissions?status=PENDING
    -- a value no code path assigns. The badge showed N, the user clicked
    it, and the list came back empty, every time. The link is followed
    for real here (through the same list query the route uses) rather
    than string-matched, so the count and the list must genuinely agree."""
    from urllib.parse import parse_qs, urlparse

    from app.attention.service import get_attention_items
    from app.commercial_sales import list_queries
    from app.commercial_sales.allocation import allocate_payment
    from app.extensions import db_session
    from app.models.staff import StaffUser

    staff_a, profile_a = _seed_sales_employee(app, "xs-badge-a@example.com")
    staff_fin, _profile_fin = _seed_sales_employee(app, "xs-badge-fin@example.com", role_codes=["FINANCE"])
    customer_id = _seed_customer(app, staff_a)
    plan_id = _seed_plan(app, "XS_BADGE_PLAN")
    with app.app_context():
        _grant_commission_plan(app, profile_a, staff_a)
        invoice = _make_issued_invoice(app, staff_a, profile_a, customer_id, plan_id)
        payment = _make_confirmed_payment(app, customer_id, Decimal("1000.00"), "USD", staff_a, staff_fin)
        allocate_payment(payment=payment, invoice=invoice, amount=Decimal("1000.00"), actor_staff_user_id=staff_fin)

    with app.test_request_context():
        actor_fin = db_session.get(StaffUser, staff_fin)
        commission_items = [i for i in get_attention_items(actor_fin) if i.category == "pending_commissions"]
        assert commission_items, "no 'Pending Commission Approvals' item was produced at all"
        linked_status = parse_qs(urlparse(commission_items[0].action_url).query)["status"][0]

    with app.app_context():
        listed = list_queries.list_commissions(
            page=1, page_size=50, status=linked_status, employee_profile_id=None, view_all=True,
        )

    assert len(listed["rows"]) == len(commission_items), (
        f"the badge counted {len(commission_items)} entr(ies) but its own link "
        f"(?status={linked_status}) lists {len(listed['rows'])}"
    )


# ------------- Bug 9: currency scoping silently DELETED a duplicate pair --
#
# Follow-up to bug 7. Scoping a flat COUNT to one currency loses nothing.
# Scoping the duplicate queue does, because its currency filter sat INSIDE
# a GROUP BY external_reference / HAVING count(id) > 1: a USD expense and a
# JOD expense sharing one external reference were split into two groups of
# one, neither group cleared HAVING, and the pair vanished from BOTH
# currencies' screens instead of appearing on one. Duplicate detection is a
# fraud/error control, so a silent drop is the worst available failure.

def test_cross_currency_duplicate_pair_stays_visible_on_both_currency_screens(app, seeded):
    """The grouping that decides "is this a duplicate?" must stay
    currency-agnostic (matching expenses/duplicates.py::find_duplicate_signals,
    which matches on external_reference alone); only the ATTRIBUTION of an
    already-detected group to a screen is currency-scoped. A pair straddling
    two currencies is therefore reviewable from either currency's dashboard,
    never from neither."""
    from app.expenses.lifecycle import create_expense
    from app.expenses.payees import create_payee
    from app.extensions import db_session
    from app.models.expenses import ExpenseCategory
    from app.operational_reports.dashboards import management_operational_dashboard

    staff_a, profile_a = _seed_sales_employee(app, "xs-dup-mixed@example.com")
    with app.app_context():
        category = ExpenseCategory(category_code=f"XS-{uuid.uuid4().hex[:8]}", name="Cross-Currency Duplicate", is_active=True)
        db_session.add(category)
        db_session.flush()
        payee = create_payee(
            payee_type="EXTERNAL", display_name="Cross-Currency Vendor", employee_profile_id=None,
            external_contact_reference=None, created_by_staff_user_id=staff_a,
        )
        mixed_reference = f"XS-MIXED-{uuid.uuid4().hex[:8]}"
        jod_only_reference = f"XS-JODONLY-{uuid.uuid4().hex[:8]}"

        def _new_expense(amount, currency, external_reference):
            return create_expense(
                category_id=category.id, payee_id=payee.id, amount=Decimal(amount), currency=currency,
                expense_date=date.today(), description="cross-currency duplicate", external_reference=external_reference,
                payment_method="CASH", payment_reference=None, entered_by_employee_profile_id=profile_a,
            )

        # The pair the old filter deleted: same external reference, two currencies.
        _new_expense("15.00", "USD", mixed_reference)
        _new_expense("15.00", "JOD", mixed_reference)
        # A single-currency pair alongside it, so the JOD screen's count has
        # to be 2 rather than trivially matching the USD screen's 1 -- this
        # is what proves attribution is still currency-scoped and the fix
        # did not simply revert bug 7 by making both counts company-wide.
        _new_expense("25.00", "JOD", jod_only_reference)
        _new_expense("25.00", "JOD", jod_only_reference)
        db_session.commit()

        usd = management_operational_dashboard("USD")
        jod = management_operational_dashboard("JOD")

    assert usd["duplicate_review_queue_count"] == 1, "the cross-currency duplicate pair disappeared from the USD review queue"
    assert jod["duplicate_review_queue_count"] == 2, (
        "the JOD review queue must show both its own single-currency pair and the "
        "cross-currency pair it is half of"
    )


# ------- Bug 10: a single-currency commission figure with no currency on it --

def test_employee_commission_screen_names_its_currency_and_lets_the_user_change_it(app, client, seeded):
    """Fixing bug 5 scoped the four commission figures to one currency but
    left the screen showing them completely unlabelled, with no selector --
    a number silently dependent on a hidden default, which is worse than
    the visibly-wrong blended number it replaced. Asserted through the
    rendered HTML, because the service half was already correct."""
    from tests.conftest import force_login

    from app.commercial_sales.allocation import allocate_payment

    staff_a, profile_a = _seed_sales_employee(app, "xs-label-a@example.com")
    staff_fin, _profile_fin = _seed_sales_employee(app, "xs-label-fin@example.com", role_codes=["FINANCE"])
    customer_id = _seed_customer(app, staff_a)
    plan_usd = _seed_plan(app, "XS_LABEL_USD", currency="USD", price=Decimal("1000.00"))
    plan_jod = _seed_plan(app, "XS_LABEL_JOD", currency="JOD", price=Decimal("500.00"))
    with app.app_context():
        _grant_commission_plan(app, profile_a, staff_a)  # 10% of every allocated payment

        invoice_usd = _make_issued_invoice(app, staff_a, profile_a, customer_id, plan_usd, currency="USD")
        payment_usd = _make_confirmed_payment(app, customer_id, Decimal("1000.00"), "USD", staff_a, staff_fin)
        allocate_payment(payment=payment_usd, invoice=invoice_usd, amount=Decimal("1000.00"), actor_staff_user_id=staff_fin)

        invoice_jod = _make_issued_invoice(app, staff_a, profile_a, customer_id, plan_jod, currency="JOD")
        payment_jod = _make_confirmed_payment(app, customer_id, Decimal("500.00"), "JOD", staff_a, staff_fin)
        allocate_payment(payment=payment_jod, invoice=invoice_jod, amount=Decimal("500.00"), actor_staff_user_id=staff_fin)

    force_login(client, app, staff_a)
    usd_body = client.get("/commercial-dashboard?currency=USD").get_data(as_text=True)
    jod_body = client.get("/commercial-dashboard?currency=JOD").get_data(as_text=True)

    # 1. The headline figure carries its own currency, never a bare number.
    assert "100 USD" in usd_body, "the commission headline rendered without naming the currency it is scoped to"
    assert "50 JOD" in jod_body
    assert "100 USD" not in jod_body, "the JOD view showed the USD figure"

    # 2. There is a way to change the currency, and it is a plain GET
    #    control -- no inline JS, which CSP script-src 'self' drops silently.
    assert 'name="currency"' in usd_body, "the screen offers no way to change the currency it is scoped to"
    assert "onchange=" not in usd_body

    # 3. Scoping the headline hides nothing: the other currency the employee
    #    actually earned in is still on the page.
    assert "JOD" in usd_body, "the employee's JOD earnings vanished from the screen entirely"
    assert "USD" in jod_body
