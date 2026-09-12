"""Query-count regression tests for owner/app/attention/service.py.

get_attention_items() (and its topbar-badge wrapper count_attention_items())
runs on EVERY page view via the app.context_processor in
app/__init__.py::_inject_current_staff(), for any staff member holding at
least one Attention Center permission (see ATTENTION_CATEGORY_PERMISSIONS).
Four of its category helpers used to issue one extra query PER ROW inside a
Python loop (a classic N+1) instead of one batched query for the whole
category -- see the AUDIT-perf comments in service.py:
  - _quotes_pending_approval_items(): db_session.get() x2 per pending approval
  - _suspended_license_items(): one LicenseStatusHistory query per license
  - _overdue_invoice_items(): confirmed_allocated_amount() up to twice per invoice
  - _unallocated_payment_items(): unallocated_payment_balance() once per payment

A page rendering the same list either way is NOT proof the N+1 is gone --
correctness of these categories is already covered by
test_attention_center.py and test_owner_cross_screen_metric_bugs.py. The
only thing that can catch a silent regression back to one-query-per-row is
counting the actual SQL statements sent to Postgres, which is what every
test below does: seed a small N of the row that used to drive one extra
query, record the real statement count, seed several more of that same
row, and assert the statement count is IDENTICAL -- proof the count no
longer scales with row count.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import event
from sqlalchemy.engine import Engine

from tests.conftest import make_license, make_staff
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
    the context is open. Uses the class-level Engine event (rather than the
    module-private `_engine` in app/extensions.py) so it works regardless
    of how the active engine instance is exposed."""
    counter = {"n": 0}

    def _tally(conn, cursor, statement, parameters, context, executemany):
        counter["n"] += 1

    event.listen(Engine, "before_cursor_execute", _tally)
    try:
        yield counter
    finally:
        event.remove(Engine, "before_cursor_execute", _tally)


def test_suspended_license_items_query_count_does_not_scale_with_row_count(app, seeded):
    from app.attention.service import get_attention_items
    from app.extensions import db_session
    from app.licensing.services import transition_license
    from app.models.licensing import License
    from app.models.staff import StaffUser

    staff_id = make_staff(app, "attn-qc-lic@example.com", super_admin=True)

    def _add_suspended_license():
        with app.app_context():
            license_id, _full_key = make_license(app, staff_id)
            lic = db_session.get(License, license_id)
            transition_license(lic, "ACTIVE", staff_id)
            transition_license(lic, "SUSPENDED", staff_id)

    _add_suspended_license()
    with app.test_request_context():
        actor = db_session.get(StaffUser, staff_id)
        with count_queries() as counter:
            items = get_attention_items(actor)
        queries_for_one = counter["n"]
        assert len([i for i in items if i.category == "suspended_licenses"]) == 1

    for _ in range(4):
        _add_suspended_license()
    with app.test_request_context():
        actor = db_session.get(StaffUser, staff_id)
        with count_queries() as counter:
            items = get_attention_items(actor)
        queries_for_five = counter["n"]
        assert len([i for i in items if i.category == "suspended_licenses"]) == 5

    assert queries_for_five == queries_for_one, (
        f"query count grew with row count ({queries_for_one} -> {queries_for_five}) -- "
        "the N+1 in _suspended_license_items() is back"
    )


def test_quotes_pending_approval_items_query_count_does_not_scale_with_row_count(app, seeded):
    from app.attention.service import get_attention_items
    from app.commercial_sales.quotes import add_quote_line, create_quote
    from app.extensions import db_session
    from app.models.staff import StaffUser

    staff_id, profile_id = _seed_sales_employee(app, "attn-qc-quote@example.com")
    customer_id = _seed_customer(app, staff_id)
    plan_id = _seed_plan(app, "ATTN_QC_QUOTE_PLAN")
    admin_id = make_staff(app, "attn-qc-quote-admin@example.com", super_admin=True)

    def _add_pending_quote_approval():
        with app.app_context():
            quote = create_quote(
                {"customer_id": customer_id, "currency": "USD"},
                actor_employee_profile_id=profile_id, actor_staff_user_id=staff_id,
            )
            add_quote_line(
                quote, plan_id=plan_id, addon_id=None, quantity=1,
                override_unit_price=Decimal("0.01"), override_reason="Query-count test override",
                actor_staff_user_id=staff_id,
            )

    _add_pending_quote_approval()
    with app.test_request_context():
        actor = db_session.get(StaffUser, admin_id)
        with count_queries() as counter:
            items = get_attention_items(actor)
        queries_for_one = counter["n"]
        assert len([i for i in items if i.category == "quotes_pending_approval"]) == 1

    for _ in range(5):
        _add_pending_quote_approval()
    with app.test_request_context():
        actor = db_session.get(StaffUser, admin_id)
        with count_queries() as counter:
            items = get_attention_items(actor)
        queries_for_six = counter["n"]
        assert len([i for i in items if i.category == "quotes_pending_approval"]) == 6

    assert queries_for_six == queries_for_one, (
        f"query count grew with row count ({queries_for_one} -> {queries_for_six}) -- "
        "the N+1 in _quotes_pending_approval_items() is back"
    )


def test_overdue_invoice_items_query_count_does_not_scale_with_row_count(app, seeded):
    from app.attention.service import get_attention_items
    from app.extensions import db_session
    from app.models.staff import StaffUser

    staff_id, profile_id = _seed_sales_employee(app, "attn-qc-inv@example.com")
    customer_id = _seed_customer(app, staff_id)
    plan_id = _seed_plan(app, "ATTN_QC_INV_PLAN")
    admin_id = make_staff(app, "attn-qc-inv-admin@example.com", super_admin=True)
    past_due = date.today() - timedelta(days=5)

    def _add_overdue_invoice():
        with app.app_context():
            _make_issued_invoice(app, staff_id, profile_id, customer_id, plan_id, currency="USD", due_date=past_due)

    _add_overdue_invoice()
    with app.test_request_context():
        actor = db_session.get(StaffUser, admin_id)
        with count_queries() as counter:
            items = get_attention_items(actor)
        queries_for_one = counter["n"]
        assert len([i for i in items if i.category == "overdue_invoices"]) == 1

    for _ in range(3):
        _add_overdue_invoice()
    with app.test_request_context():
        actor = db_session.get(StaffUser, admin_id)
        with count_queries() as counter:
            items = get_attention_items(actor)
        queries_for_four = counter["n"]
        assert len([i for i in items if i.category == "overdue_invoices"]) == 4

    assert queries_for_four == queries_for_one, (
        f"query count grew with row count ({queries_for_one} -> {queries_for_four}) -- "
        "the N+1 in _overdue_invoice_items() is back"
    )


def test_unallocated_payment_items_query_count_does_not_scale_with_row_count(app, seeded):
    from app.attention.service import get_attention_items
    from app.extensions import db_session
    from app.models.staff import StaffUser

    staff_id, _profile_id = _seed_sales_employee(app, "attn-qc-pay@example.com")
    customer_id = _seed_customer(app, staff_id)
    finance_id = make_staff(app, "attn-qc-pay-fin@example.com", super_admin=True)

    def _add_unallocated_payment():
        with app.app_context():
            _make_confirmed_payment(app, customer_id, Decimal("250.00"), "USD", staff_id, finance_id)

    _add_unallocated_payment()
    with app.test_request_context():
        actor = db_session.get(StaffUser, finance_id)
        with count_queries() as counter:
            items = get_attention_items(actor)
        queries_for_one = counter["n"]
        assert len([i for i in items if i.category == "unallocated_payments"]) == 1

    for _ in range(4):
        _add_unallocated_payment()
    with app.test_request_context():
        actor = db_session.get(StaffUser, finance_id)
        with count_queries() as counter:
            items = get_attention_items(actor)
        queries_for_five = counter["n"]
        assert len([i for i in items if i.category == "unallocated_payments"]) == 5

    assert queries_for_five == queries_for_one, (
        f"query count grew with row count ({queries_for_one} -> {queries_for_five}) -- "
        "the N+1 in _unallocated_payment_items() is back"
    )
