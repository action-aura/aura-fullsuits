"""Query-count regression + agreement test for
owner/app/operational_reports/aggregation.py::outstanding_receivables_total().

management_operational_dashboard() (operational_reports/dashboards.py) and
build_operational_summary() both call outstanding_receivables_total() for a
money total shown on finance dashboards. It used to compute that total by
loading every non-DRAFT/non-VOID invoice in the currency and calling
confirmed_allocated_amount() -- a per-invoice query -- inside a Python loop,
with NO LIMIT. This is the third occurrence of the exact same N+1 shape
already fixed in:
  - app/attention/service.py::_overdue_invoice_items()
    (tests/test_attention_query_counts.py)
  - app/operational_reports/dashboards.py::_overdue_invoice_count()
    (tests/test_operational_dashboard_query_counts.py)

It was found while measuring those two fixes -- see
test_operational_dashboard_query_counts.py's module docstring, which
explicitly calls out this function as "discovered while measuring this fix,
out of scope for this change, and reported rather than fixed" -- and is
fixed here the same way: one query for the candidate invoices (currency,
non-DRAFT/non-VOID status) and one GROUP BY
SUM(PaymentAllocation.allocated_amount) instead of one
confirmed_allocated_amount() query per candidate.

Two things are tested:
  1. Query count no longer scales with row count (the N+1 itself).
  2. The batched SQL reimplementation returns EXACTLY the same money total
     as the canonical per-invoice definition (confirmed_allocated_amount(),
     app/commercial_sales/invoices.py) over a fixture that exercises
     partial payments, a full settlement, and a refund against a fully
     collected invoice -- a page rendering the same-looking number is not
     proof the two agree; only comparing the two implementations' output
     over the same rows is.
"""
from __future__ import annotations

from contextlib import contextmanager
from decimal import Decimal

from sqlalchemy import event
from sqlalchemy.engine import Engine

from tests.test_owner_cross_screen_metric_bugs import (
    _make_confirmed_payment,
    _make_issued_invoice,
    _seed_customer,
    _seed_plan,
    _seed_sales_employee,
)


@contextmanager
def count_queries():
    """Counts the real SQL statements issued to the database engine while
    the context is open -- same approach as
    tests/test_attention_query_counts.py::count_queries() and
    tests/test_operational_dashboard_query_counts.py::count_queries()."""
    counter = {"n": 0}

    def _tally(conn, cursor, statement, parameters, context, executemany):
        counter["n"] += 1

    event.listen(Engine, "before_cursor_execute", _tally)
    try:
        yield counter
    finally:
        event.remove(Engine, "before_cursor_execute", _tally)


def test_outstanding_receivables_total_query_count_does_not_scale_with_row_count(app, seeded):
    from app.operational_reports.aggregation import outstanding_receivables_total

    staff_id, profile_id = _seed_sales_employee(app, "agg-qc-inv@example.com")
    customer_id = _seed_customer(app, staff_id)
    plan_id = _seed_plan(app, "AGG_QC_INV_PLAN")

    def _add_unpaid_invoice():
        with app.app_context():
            _make_issued_invoice(app, staff_id, profile_id, customer_id, plan_id, currency="USD")

    _add_unpaid_invoice()
    with app.app_context():
        with count_queries() as counter:
            total_for_one = outstanding_receivables_total("USD")
        queries_for_one = counter["n"]
        assert total_for_one == Decimal("1000.00")

    for _ in range(3):
        _add_unpaid_invoice()
    with app.app_context():
        with count_queries() as counter:
            total_for_four = outstanding_receivables_total("USD")
        queries_for_four = counter["n"]
        assert total_for_four == Decimal("4000.00")

    assert queries_for_four == queries_for_one, (
        f"query count grew with row count ({queries_for_one} -> {queries_for_four}) -- "
        "the N+1 in outstanding_receivables_total() is back"
    )


def test_the_batched_outstanding_receivables_total_agrees_with_the_canonical_per_invoice_definition(app, seeded):
    """The guard for the coupling outstanding_receivables_total() documents
    but does not otherwise defend -- same shape as
    test_operational_dashboard_query_counts.py::
    test_the_batched_count_agrees_with_the_canonical_per_invoice_definition().

    confirmed_allocated_amount() (app/commercial_sales/invoices.py) stays
    the canonical "how much of this invoice has actually been collected"
    definition. The batched function reproduces its "non-reversed
    PaymentAllocation rows" condition as a SQL WHERE/GROUP BY instead of
    calling it per invoice. Nothing in the suite fails if the two drift --
    both return a Decimal, the dashboard renders it, and a total that is
    quietly wrong by a few cents looks exactly like a total that is right.

    So this asserts the two implementations AGREE, in exact Decimal value,
    over a population where every clause is load-bearing for at least one
    row: an unpaid invoice (full balance), a partially paid invoice (some
    balance), a fully settled invoice (zero balance, must not be summed in
    even though it's a candidate row), an invoice that was fully collected
    and then partially refunded (refunds never reverse a PaymentAllocation,
    so this must behave exactly like the fully-settled case -- zero
    outstanding, not negative and not re-inflated), a DRAFT invoice (status
    clause, never even a candidate), a VOID invoice (status clause), and a
    same-customer invoice in a different currency (currency clause, must
    not bleed into the USD total).
    """
    from app.commercial_sales.allocation import allocate_payment
    from app.commercial_sales.invoices import confirmed_allocated_amount, void_invoice
    from app.commercial_sales.refunds import approve_refund, confirm_refund, create_refund
    from app.commercial_sales.sales_orders import confirm_order, create_order_from_quote
    from app.commercial_sales.quotes import add_quote_line, create_quote, record_customer_decision, submit_quote
    from app.commercial_sales.invoices import create_invoice_from_order
    from app.extensions import db_session
    from app.models.commercial_sales import CommercialInvoice
    from app.operational_reports.aggregation import outstanding_receivables_total

    import uuid

    staff_a, profile_a = _seed_sales_employee(app, "agg-agree-a@example.com")
    staff_fin, _profile_fin = _seed_sales_employee(app, "agg-agree-fin@example.com", role_codes=["FINANCE"])
    customer_id = _seed_customer(app, staff_a)
    plan_id = _seed_plan(app, "AGG_AGREE_PLAN", currency="USD", price=Decimal("1000.00"))

    with app.app_context():
        # (1) Unpaid -- full 1000.00 outstanding.
        _make_issued_invoice(app, staff_a, profile_a, customer_id, plan_id, currency="USD")

        # (2) Partially paid -- 1000.00 - 400.00 = 600.00 outstanding.
        partial = _make_issued_invoice(app, staff_a, profile_a, customer_id, plan_id, currency="USD")
        partial_payment = _make_confirmed_payment(app, customer_id, Decimal("400.00"), "USD", staff_a, staff_fin)
        allocate_payment(payment=partial_payment, invoice=partial, amount=Decimal("400.00"), actor_staff_user_id=staff_fin)

        # (3) Fully settled -- zero balance. A candidate row (status PAID is
        # not DRAFT/VOID) that must NOT be summed since outstanding <= 0.
        settled = _make_issued_invoice(app, staff_a, profile_a, customer_id, plan_id, currency="USD")
        settle_payment = _make_confirmed_payment(app, customer_id, settled.total, "USD", staff_a, staff_fin)
        allocate_payment(payment=settle_payment, invoice=settled, amount=settled.total, actor_staff_user_id=staff_fin)

        # (4) Fully collected, then partially refunded. The refund is a
        # separate CommercialRefund record -- it never reverses the
        # PaymentAllocation row, so confirmed_allocated_amount() is
        # unchanged by it. This invoice must therefore land at zero
        # outstanding too, exactly like (3), even though invoice.status is
        # now PARTIALLY_REFUNDED rather than PAID.
        refunded = _make_issued_invoice(app, staff_a, profile_a, customer_id, plan_id, currency="USD")
        refunded_payment = _make_confirmed_payment(app, customer_id, refunded.total, "USD", staff_a, staff_fin)
        allocate_payment(payment=refunded_payment, invoice=refunded, amount=refunded.total, actor_staff_user_id=staff_fin)
        refund = create_refund(
            refunded, amount=Decimal("300.00"), reason="Query-count agreement test refund",
            payment_record_id=refunded_payment.id, actor_employee_profile_id=profile_a, actor_staff_user_id=staff_a,
        )
        refund = approve_refund(refund, actor_staff_user_id=staff_fin)
        confirm_refund(refund, actor_staff_user_id=staff_fin)
        assert refunded.status == "PARTIALLY_REFUNDED"

        # (5) DRAFT -- created but never issued. Excluded by the status
        # filter before either implementation would even look at its
        # balance.
        draft_quote = create_quote({"customer_id": customer_id, "currency": "USD"}, actor_employee_profile_id=profile_a, actor_staff_user_id=staff_a)
        add_quote_line(draft_quote, plan_id=plan_id, addon_id=None, quantity=1, actor_staff_user_id=staff_a)
        submit_quote(draft_quote, actor_staff_user_id=staff_a)
        record_customer_decision(draft_quote, accepted=True, actor_staff_user_id=staff_a)
        draft_order = create_order_from_quote(draft_quote, actor_employee_profile_id=profile_a, actor_staff_user_id=staff_a, idempotency_key=str(uuid.uuid4()))
        confirm_order(draft_order, actor_staff_user_id=staff_a)
        draft_invoice = create_invoice_from_order(draft_order, actor_employee_profile_id=profile_a, actor_staff_user_id=staff_a, idempotency_key=str(uuid.uuid4()))
        assert draft_invoice.status == "DRAFT"

        # (6) VOID -- issued, never paid, then voided. Excluded by the
        # status filter.
        voided = _make_issued_invoice(app, staff_a, profile_a, customer_id, plan_id, currency="USD")
        void_invoice(voided, reason="Query-count agreement test void", actor_staff_user_id=staff_a)
        assert voided.status == "VOID"

        # (7) Different currency, same customer -- must not bleed into the
        # USD total.
        jod_plan_id = _seed_plan(app, "AGG_AGREE_PLAN_JOD", currency="JOD", price=Decimal("500.00"))
        _make_issued_invoice(app, staff_a, profile_a, customer_id, jod_plan_id, currency="JOD")

        batched = outstanding_receivables_total("USD")

        # The canonical definition, applied one invoice at a time, over
        # exactly the rows the batched version's own WHERE clause selects.
        usd_candidates = db_session.execute(
            db_session.query(CommercialInvoice).filter(
                CommercialInvoice.currency == "USD",
                CommercialInvoice.status.notin_(("DRAFT", "VOID")),
            ).statement
        ).scalars().all()
        per_invoice_total = Decimal("0")
        for invoice in usd_candidates:
            outstanding = invoice.total - confirmed_allocated_amount(invoice)
            if outstanding > 0:
                per_invoice_total += outstanding

    # Anti-vacuity: a population where both sides are zero would pass this
    # while proving nothing about agreement.
    assert per_invoice_total > 0, (
        "the fixture produced no outstanding balance, so agreeing on zero proves nothing"
    )
    assert batched == per_invoice_total, (
        f"outstanding_receivables_total() returned {batched} but the canonical "
        f"per-invoice confirmed_allocated_amount() definition returned {per_invoice_total} "
        "over the same rows -- the SQL reimplementation has drifted from the definition it mirrors"
    )
    # Pin the exact expected figure too, not just cross-implementation
    # agreement, so a bug shared by both implementations still fails this:
    # 1000.00 (unpaid) + 600.00 (partial) + 0 (settled) + 0 (fully
    # collected then partially refunded).
    assert batched == Decimal("1600.00")
