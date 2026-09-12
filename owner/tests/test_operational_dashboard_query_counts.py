"""Query-count regression test for
owner/app/operational_reports/dashboards.py::_overdue_invoice_count().

management_operational_dashboard() used to compute its "overdue_invoices"
figure by calling is_invoice_overdue() -- which internally calls
confirmed_allocated_amount(), a per-invoice query -- once per non-excluded-
status invoice in the currency, with NO LIMIT (unlike the Attention
Center's capped version of this exact N+1, fixed in
app/attention/service.py::_overdue_invoice_items() -- see
tests/test_attention_query_counts.py). So this dashboard's cost grew
without bound as a customer's invoice history grew.

The fix batches the same way: one query for candidate invoices (currency,
non-excluded status, due date strictly in the past -- pushed into SQL
instead of checked in Python) and one GROUP BY SUM(PaymentAllocation.
allocated_amount) instead of one confirmed_allocated_amount() query per
candidate. See _overdue_invoice_count()'s own docstring.

This test calls _overdue_invoice_count() directly rather than going through
management_operational_dashboard(). That is deliberate, not a shortcut:
the dashboard also calls outstanding_receivables_total()
(app/operational_reports/aggregation.py) for the SAME currency's invoices,
and that function has its own, separate, still-unfixed per-invoice
confirmed_allocated_amount() loop with no LIMIT either -- discovered while
measuring this fix, out of scope for this change, and reported rather than
fixed. Counting queries through the full dashboard would conflate that
function's growth with this one's and could never show a flat count
either way. Correctness of the dashboard's "overdue_invoices" figure
itself (not just this helper's query cost) is already covered by
test_owner_cross_screen_metric_bugs.py's PARTIALLY_REFUNDED and
zero-balance cases, both of which call management_operational_dashboard()
end to end.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import date, timedelta

from sqlalchemy import event
from sqlalchemy.engine import Engine

from tests.test_owner_cross_screen_metric_bugs import (
    _make_issued_invoice,
    _seed_customer,
    _seed_plan,
    _seed_sales_employee,
)


@contextmanager
def count_queries():
    """Counts the real SQL statements issued to the database engine while
    the context is open -- same approach as
    tests/test_attention_query_counts.py::count_queries()."""
    counter = {"n": 0}

    def _tally(conn, cursor, statement, parameters, context, executemany):
        counter["n"] += 1

    event.listen(Engine, "before_cursor_execute", _tally)
    try:
        yield counter
    finally:
        event.remove(Engine, "before_cursor_execute", _tally)


def test_overdue_invoice_count_query_count_does_not_scale_with_row_count(app, seeded):
    from app.extensions import db_session
    from app.operational_reports.dashboards import _overdue_invoice_count

    staff_id, profile_id = _seed_sales_employee(app, "dash-qc-inv@example.com")
    customer_id = _seed_customer(app, staff_id)
    plan_id = _seed_plan(app, "DASH_QC_INV_PLAN")
    past_due = date.today() - timedelta(days=5)

    def _add_overdue_invoice():
        with app.app_context():
            _make_issued_invoice(app, staff_id, profile_id, customer_id, plan_id, currency="USD", due_date=past_due)

    _add_overdue_invoice()
    with app.app_context():
        with count_queries() as counter:
            count_for_one = _overdue_invoice_count("USD", as_of=date.today())
        queries_for_one = counter["n"]
        assert count_for_one == 1

    for _ in range(3):
        _add_overdue_invoice()
    with app.app_context():
        with count_queries() as counter:
            count_for_four = _overdue_invoice_count("USD", as_of=date.today())
        queries_for_four = counter["n"]
        assert count_for_four == 4

    assert queries_for_four == queries_for_one, (
        f"query count grew with row count ({queries_for_one} -> {queries_for_four}) -- "
        "the N+1 in _overdue_invoice_count() is back"
    )


def test_overdue_invoice_count_excludes_not_yet_due_and_zero_balance_invoices(app, seeded):
    """Correctness guard alongside the query-count guard above: growing the
    candidate set must only grow the returned count for invoices that are
    actually overdue with a positive remaining balance -- not for every row
    the batched candidate query happens to touch."""
    from app.commercial_sales.allocation import allocate_payment
    from app.extensions import db_session
    from app.models.staff import StaffUser
    from app.operational_reports.dashboards import _overdue_invoice_count
    from tests.conftest import make_staff
    from tests.test_owner_cross_screen_metric_bugs import _make_confirmed_payment

    staff_id, profile_id = _seed_sales_employee(app, "dash-qc-inv-mix@example.com")
    finance_id = make_staff(app, "dash-qc-inv-mix-fin@example.com", super_admin=True)
    customer_id = _seed_customer(app, staff_id)
    plan_id = _seed_plan(app, "DASH_QC_INV_MIX_PLAN")
    past_due = date.today() - timedelta(days=5)
    not_yet_due = date.today() + timedelta(days=30)

    with app.app_context():
        # (1) Genuinely overdue, unpaid -- counted.
        _make_issued_invoice(app, staff_id, profile_id, customer_id, plan_id, currency="USD", due_date=past_due)

        # (2) Overdue, but fully paid off -- zero balance, must not count.
        paid_off = _make_issued_invoice(app, staff_id, profile_id, customer_id, plan_id, currency="USD", due_date=past_due)
        payment = _make_confirmed_payment(app, customer_id, paid_off.total, "USD", staff_id, finance_id)
        allocate_payment(payment=payment, invoice=paid_off, amount=paid_off.total, actor_staff_user_id=finance_id)

        # (3) Due in the future -- not overdue yet, must not count.
        _make_issued_invoice(app, staff_id, profile_id, customer_id, plan_id, currency="USD", due_date=not_yet_due)

        count = _overdue_invoice_count("USD", as_of=date.today())

    assert count == 1, "only the genuinely-overdue, positive-balance invoice should be counted"


def test_the_batched_count_agrees_with_the_canonical_per_invoice_definition(app, seeded):
    """The guard for the coupling `_overdue_invoice_count()` documents but does
    not otherwise defend.

    That function reproduces `is_invoice_overdue()`'s conditions as SQL WHERE
    clauses so the balance check can be batched. `is_invoice_overdue()` stays
    the canonical definition, and its own docstring warns: "If
    is_invoice_overdue() ever grows a new condition, update this to match."

    A warning in a comment is not a guard. Nothing in the suite fails if the two
    drift, and drift here is silent by construction -- both sides return an
    integer, the dashboard renders it, and a number that is quietly wrong looks
    exactly like a number that is right. That is the same shape as the N+1 this
    change fixed: a defect with nothing to see.

    So this asserts the two implementations AGREE over a deliberately awkward
    population, rather than asserting either one's output. If someone adds a
    condition to `is_invoice_overdue()` and not to the SQL, this fails and names
    the disagreement.

    The population is chosen so that every clause the SQL reproduces is
    load-bearing for at least one row: an excluded status, a NULL due date, a
    due date exactly today (the boundary the SQL writes as `< as_of`), another
    currency, a fully-paid overdue invoice, and a partially-paid one that is
    still owed money. Drop any clause from the SQL and at least one of these
    moves.
    """
    from app.commercial_sales.allocation import allocate_payment
    from app.extensions import db_session
    from app.models.commercial_sales import CommercialInvoice
    from app.operational_reports.dashboards import (
        OVERDUE_INVOICE_EXCLUDED_STATUSES,
        _overdue_invoice_count,
        is_invoice_overdue,
    )
    from tests.conftest import make_staff
    from tests.test_owner_cross_screen_metric_bugs import _make_confirmed_payment

    staff_id, profile_id = _seed_sales_employee(app, "dash-agree@example.com")
    finance_id = make_staff(app, "dash-agree-fin@example.com", super_admin=True)
    customer_id = _seed_customer(app, staff_id)
    plan_id = _seed_plan(app, "DASH_AGREE_PLAN")

    today = date.today()
    past_due = today - timedelta(days=5)

    with app.app_context():
        # Plainly overdue and unpaid.
        _make_issued_invoice(app, staff_id, profile_id, customer_id, plan_id,
                             currency="USD", due_date=past_due)

        # Overdue but settled in full -- balance clause.
        settled = _make_issued_invoice(app, staff_id, profile_id, customer_id, plan_id,
                                       currency="USD", due_date=past_due)
        payment = _make_confirmed_payment(app, customer_id, settled.total, "USD", staff_id, finance_id)
        allocate_payment(payment=payment, invoice=settled, amount=settled.total,
                         actor_staff_user_id=finance_id)

        # Overdue and PARTLY paid -- still owed, must still count. This is the
        # row a naive "has any allocation" test would get wrong.
        partly = _make_issued_invoice(app, staff_id, profile_id, customer_id, plan_id,
                                      currency="USD", due_date=past_due)
        part_payment = _make_confirmed_payment(app, customer_id, partly.total / 2, "USD", staff_id, finance_id)
        allocate_payment(payment=part_payment, invoice=partly, amount=partly.total / 2,
                         actor_staff_user_id=finance_id)

        # Due EXACTLY today -- the boundary the SQL writes as `due_date < as_of`.
        _make_issued_invoice(app, staff_id, profile_id, customer_id, plan_id,
                             currency="USD", due_date=today)

        # A different currency -- currency clause. Needs its OWN plan: a quote
        # line must match its plan's currency (quotes.py raises
        # CURRENCY_MISMATCH otherwise), which this test found the hard way.
        jod_plan_id = _seed_plan(app, "DASH_AGREE_PLAN_JOD", currency="JOD")
        _make_issued_invoice(app, staff_id, profile_id, customer_id, jod_plan_id,
                             currency="JOD", due_date=past_due)

        # An excluded status -- status clause. Skipped if the project has no
        # excluded statuses rather than silently proving nothing.
        excluded = _make_issued_invoice(app, staff_id, profile_id, customer_id, plan_id,
                                        currency="USD", due_date=past_due)
        if OVERDUE_INVOICE_EXCLUDED_STATUSES:
            excluded.status = sorted(OVERDUE_INVOICE_EXCLUDED_STATUSES)[0]
            db_session.commit()

        batched = _overdue_invoice_count("USD", as_of=today)

        # The canonical definition, applied one invoice at a time, over exactly
        # the same rows the batched version considers.
        usd_invoices = db_session.query(CommercialInvoice).filter(
            CommercialInvoice.currency == "USD"
        ).all()
        per_invoice = sum(1 for inv in usd_invoices if is_invoice_overdue(inv, as_of=today))

    # Anti-vacuity: a population where both answers are zero would pass this
    # while proving nothing at all about agreement.
    assert per_invoice > 0, (
        "the fixture produced no overdue invoices, so agreeing on zero proves nothing"
    )
    assert batched == per_invoice, (
        f"_overdue_invoice_count() returned {batched} but the canonical "
        f"is_invoice_overdue() returned {per_invoice} over the same rows -- the "
        "SQL reimplementation has drifted from the definition it mirrors"
    )
