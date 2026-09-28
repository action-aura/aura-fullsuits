"""Aura Retail -- one FIFO allocation of a customer's debt, read three ways.

THE DEFECT (Phase 1a verifier T2, probe_f3, cases A-E). The same debt showed
two numbers:

  * `/reports/aging` bucketed a customer's WHOLE balance by the age of their
    oldest sale with `total - amount_paid > eps`. `sales.amount_paid` is
    immutable and a later account payment never touches it, so a 100-day
    charge that was paid off 50 days ago still made the 10-day charge on top
    of it read "100 days".
  * The till's Remind button ran its own client-side FIFO that DROPPED
    prepayments: pay 20, then buy 30 -> the customer owes 10 and the reminder
    said 30.

`retail_api._fifo_open_charges` is now the one allocation. It is fed by
`_customer_statement_events` -- the very events `customer_statement` lists --
so the report, the statement and the reminder cannot drift apart. These tests
drive the REAL routes (real sales, real payments, a real return) and only then
rewrite the timestamps in SQL to age the rows, exactly as the verifier's probe
did.

THE INVARIANT pinned in every case: the sum of the report's buckets for a
customer == `customers.credit_balance` == the statement's closing running
balance. `retail_api` closes the one gap that could break it (a balance with
no event behind it) by surfacing `unbooked_amount` rather than hiding it; that
gap has its own tests below.

THE CLOCK. Ages are in the SHOP's business days, not UTC. Every shop here
declares `Asia/Amman` (UTC+3 all year) so the answers do not depend on the
machine running the suite, and each row's `created_at_utc` and its device
wall-clock `created_at` are written as a coherent pair. "Now" is frozen through
`api.retail_api.datetime`, the way the sibling clock tests freeze it.

Run (one file per process; see run_all_tests.py):
    python -m pytest products/retail/tests/retail_aging_fifo_test.py -v
"""
import os
import shutil
import sys
import tempfile
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

PRODUCT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = PRODUCT_DIR / 'backend'
SUITE_ROOT = PRODUCT_DIR.parent.parent
for _p in (str(SUITE_ROOT), str(BACKEND_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

DATA = Path(tempfile.mkdtemp(prefix="aura_retail_aging_fifo_"))
(DATA / "database" / "subsystems").mkdir(parents=True, exist_ok=True)
# AURA_SITE_RELAY_ENABLED="0": a WINDOWS-platform active licence below is
# exactly the condition that elects a till the LAN hub; without this the boot
# starts background threads inside a test about one customer's ledger. Same
# reason as retail_customer_statement_reconciliation_test.py.
os.environ.update(
    AURA_STANDALONE="1", AURA_BUNDLE_DIR=str(BACKEND_DIR), AURA_APP_DATA=str(DATA),
    AURA_SITE_RELAY_ENABLED="0",
)
os.environ.pop("AURA_DEV", None)

from commercial_runtime.licensing_contracts.test_support import seed_active_license  # noqa: E402

seed_active_license(str(DATA), product_code="AURA_RETAIL", platform="WINDOWS")

import app as _app_module  # noqa: E402

app = _app_module.init_app()
app.config["TESTING"] = True

import api.retail_api as retail_api_module  # noqa: E402
from commercial_runtime.identity.registry_db import get_conn as registry_conn  # noqa: E402
from commercial_runtime.security.passwords import hash_password  # noqa: E402
from database.schema import get_retail_conn  # noqa: E402

API = '/api/sub/retail'
ZONE_SHIFT = timedelta(hours=3)          # Asia/Amman, no DST since 2022
#: 2026-06-15 12:00 UTC == 15:00 in Amman, the same calendar day both ways.
NOW_UTC = datetime(2026, 6, 15, 12, 0, 0, tzinfo=timezone.utc)
TODAY = NOW_UTC.astimezone(timezone(ZONE_SHIFT)).date()


def teardown_module(module):
    shutil.rmtree(DATA, ignore_errors=True)


# -- the clock ----------------------------------------------------------------

def _frozen_class(now_utc):
    class _FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            if tz is not None:
                return now_utc.astimezone(tz)
            return now_utc.astimezone().replace(tzinfo=None)     # the device's own wall clock
    return _FrozenDateTime


@contextmanager
def _frozen(now_utc=NOW_UTC):
    original = retail_api_module.datetime
    retail_api_module.datetime = _frozen_class(now_utc)
    try:
        yield
    finally:
        retail_api_module.datetime = original


def _stamp(utc):
    """(created_at, created_at_utc) for one instant, as a coherent pair: the
    shop's device wall clock (Amman) and the true UTC instant."""
    local = utc + ZONE_SHIFT
    return local.strftime('%Y-%m-%d %H:%M:%S'), utc.strftime('%Y-%m-%d %H:%M:%S')


def _days_ago(n, hour_utc=7):
    """`n` shop days before TODAY, at 10:00 Amman (07:00Z) unless told
    otherwise -- far from any midnight, so only the boundary tests are
    sensitive to which clock is used."""
    day = TODAY - timedelta(days=n)
    return datetime(day.year, day.month, day.day, hour_utc, 0, 0, tzinfo=timezone.utc)


# -- harness ------------------------------------------------------------------

def _make_shop(day_start_hour=None):
    """New company + admin + branch, JOD (3 dp), Asia/Amman, logged in."""
    email = f"aging-{uuid.uuid4().hex[:10]}@test.local"
    password = "AgingFifoPW1"
    company_id = str(uuid.uuid4())
    conn = registry_conn()
    conn.execute(
        "INSERT INTO users (id, company_id, employee_id, email, password_hash, role, status, require_password_change) "
        "VALUES (?,?,?,?,?,?,?,0)",
        (str(uuid.uuid4()), company_id, "EMP-0001", email, hash_password(password), "admin", "active"),
    )
    conn.commit()
    conn.close()

    rconn = get_retail_conn()
    rconn.execute("INSERT INTO branches (company_id,name) VALUES (?, 'Main')", (company_id,))
    branch_id = rconn.execute(
        "SELECT id FROM branches WHERE company_id=? ORDER BY id LIMIT 1", (company_id,)
    ).fetchone()[0]
    rconn.execute("INSERT OR REPLACE INTO retail_settings (company_id,skey,svalue) VALUES (?,?,?)",
                  (company_id, 'business_timezone', 'Asia/Amman'))
    if day_start_hour is not None:
        rconn.execute("INSERT OR REPLACE INTO retail_settings (company_id,skey,svalue) VALUES (?,?,?)",
                      (company_id, 'business_day_start_hour', str(day_start_hour)))
    rconn.commit()
    rconn.close()

    client = app.test_client()
    r = client.post("/api/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.get_json()
    client.get(f"{API}/settings/tax")
    r = client.post(f"{API}/settings/credit", json={"base_currency": "JOD"})
    assert r.status_code == 200, r.get_json()

    # sale_number / return_number carry BARE UNIQUE constraints while
    # _next_ref() restarts at 1 per company -- same trick, same reason, as
    # retail_customer_statement_reconciliation_test.py.
    import random as _random
    dconn = get_retail_conn()
    for _doc_type in ('sale', 'return'):
        dconn.execute(
            "INSERT OR REPLACE INTO doc_sequences (company_id,doc_type,last_no) VALUES (?,?,?)",
            (company_id, _doc_type, _random.randint(1, 5_000_000)),
        )
    dconn.commit()
    dconn.close()
    return client, company_id, branch_id


def _product(cid, branch_id, price):
    """One zero-tax product per price -- the server recomputes a sale's total
    from the product's price, so a 14.5 charge needs a 14.5 product."""
    sku = f'AG-{uuid.uuid4().hex[:8]}'
    rconn = get_retail_conn()
    rconn.execute(
        "INSERT INTO products (id,company_id,sku,name,cost_price,sell_price,tax_rate) VALUES (?,?,?,?,?,?,0)",
        (str(uuid.uuid4()), cid, sku, sku, price / 2, price),
    )
    pid = rconn.execute("SELECT id FROM products WHERE company_id=? AND sku=?", (cid, sku)).fetchone()[0]
    rconn.execute(
        "INSERT INTO inventory_balances (company_id,product_id,branch_id,quantity_on_hand) VALUES (?,?,?,?)",
        (cid, pid, branch_id, 500),
    )
    rconn.commit()
    rconn.close()
    return pid


def _customer(cid, name='Aging Customer'):
    """credit_mode='unlimited': the company default 'none' refuses any credit sale."""
    rconn = get_retail_conn()
    customer_id = str(uuid.uuid4())
    rconn.execute(
        "INSERT INTO customers (id,company_id,name,credit_mode,credit_limit,credit_balance) "
        "VALUES (?,?,?,'unlimited',0,0)",
        (customer_id, cid, name),
    )
    rconn.commit()
    rconn.close()
    return customer_id


def _stamp_row(table, where_col, where_val, utc, with_utc=True):
    created_at, created_at_utc = _stamp(utc)
    rconn = get_retail_conn()
    if with_utc:
        rconn.execute(f"UPDATE {table} SET created_at=?, created_at_utc=? WHERE {where_col}=?",
                      (created_at, created_at_utc, where_val))
    else:
        rconn.execute(f"UPDATE {table} SET created_at=? WHERE {where_col}=?", (created_at, where_val))
    rconn.commit()
    rconn.close()


def _credit_sale(client, cid, branch_id, customer_id, price, days, qty=1, stamp=True, at=None):
    pid = _product(cid, branch_id, price)
    r = client.post(f"{API}/sales", json={
        'items': [{'product_id': pid, 'quantity': qty}], 'customer_id': customer_id,
        'amount_paid': 0, 'payment_method': 'credit', 'idempotency_key': str(uuid.uuid4())})
    assert r.status_code == 200, r.get_json()
    sale = r.get_json()['data']
    if stamp:
        _stamp_row('sales', 'id', sale['id'], at or _days_ago(days))
    sale['_pid'] = pid
    return sale


def _pay(client, customer_id, amount, days, at=None):
    r = client.post(f"{API}/customers/{customer_id}/payments", json={'amount': amount, 'method': 'cash'})
    assert r.status_code == 200, r.get_json()
    ref = r.get_json()['data']['reference']
    rconn = get_retail_conn()
    n = rconn.execute("UPDATE payments SET created_at=? WHERE reference=? AND party_id=?",
                      (_stamp(at or _days_ago(days))[0], ref, customer_id)).rowcount
    rconn.commit()
    rconn.close()
    assert n == 1


def _return_one(client, sale):
    r = client.post(f"{API}/returns", json={
        'sale_id': sale['id'], 'items': [{'product_id': sale['_pid'], 'quantity': 1}],
        'reason': 'aging fifo test', 'idempotency_key': str(uuid.uuid4())})
    assert r.status_code == 200, r.get_json()
    return r.get_json()['data']


def _credit_balance(customer_id):
    rconn = get_retail_conn()
    bal = rconn.execute("SELECT credit_balance FROM customers WHERE id=?", (customer_id,)).fetchone()[0]
    rconn.close()
    return bal


def _aging(client, kind='receivable'):
    with _frozen():
        r = client.get(f"{API}/reports/aging?type={kind}")
    assert r.status_code == 200, r.get_json()
    return r.get_json()


def _row(aging, customer_id):
    rows = [c for c in aging['customers'] if c['id'] == customer_id]
    assert len(rows) == 1, f"{customer_id} not exactly once in {aging['customers']}"
    return rows[0]


def _statement(client, customer_id):
    with _frozen():
        r = client.get(f"{API}/customers/{customer_id}/statement")
    assert r.status_code == 200, r.get_json()
    return r.get_json()['data']


def _iso(days):
    return (TODAY - timedelta(days=days)).isoformat()


def _assert_reconciles(client, customer_id, expected_balance, unbooked=0.0):
    """THE INVARIANT: buckets == credit_balance == statement closing balance,
    read through the real routes and the real column.

    `unbooked` is the part of `credit_balance` that no ledger event explains
    (a fixture UPDATE / import). The statement's running balance is the sum of
    EVENTS, so for such a customer its closing line is exactly
    `credit_balance - unbooked` -- it cannot show money nothing booked. The
    aging buckets still add up to the full stored balance. With unbooked=0
    (every real-route case) this is the strict three-way equality."""
    stored = _credit_balance(customer_id)
    assert stored == pytest.approx(expected_balance, abs=1e-9), (stored, expected_balance)
    aging = _aging(client)
    row = _row(aging, customer_id)
    assert sum(row['buckets'].values()) == pytest.approx(stored, abs=5e-4), (
        f"buckets {row['buckets']} do not add up to credit_balance {stored}")
    stmt = _statement(client, customer_id)
    closing = stmt['events'][-1]['running_balance'] if stmt['events'] else 0.0
    assert closing == pytest.approx(stored - unbooked, abs=5e-4), (closing, stored, unbooked)
    assert stmt['balance'] == pytest.approx(stored, abs=5e-4)
    return aging, row, stmt


def _same_four(row, stmt):
    """The statement carries the SAME four fields as the aging row -- one
    allocation, several readers."""
    for k in ('oldest_unpaid_date', 'oldest_unpaid_amount', 'days_overdue', 'overdue_amount'):
        assert stmt[k] == row[k], (k, stmt[k], row[k])


# -- the verifier's cases -----------------------------------------------------

def test_a_paid_old_charge_does_not_age_the_new_one():
    """CASE A. 14.5 charged 100 days ago, 14.5 paid 50 days ago, then a NEW
    7.25 charge 10 days ago. The customer owes 7.25 and has owed it for 10
    days. The old report found the oldest sale with `amount_paid < total` --
    the 100-day one, whose payment was an account payment `amount_paid` never
    saw -- and aged the whole balance at 100 days ('90_plus')."""
    client, cid, bid = _make_shop()
    c = _customer(cid)
    _credit_sale(client, cid, bid, c, 14.5, 100)
    _pay(client, c, 14.5, 50)
    _credit_sale(client, cid, bid, c, 7.25, 10)

    aging, row, stmt = _assert_reconciles(client, c, 7.25)
    assert row['oldest_unpaid_date'] == _iso(10)
    assert row['oldest_unpaid_amount'] == 7.25
    assert row['days_overdue'] == 10
    assert row['overdue_amount'] == 7.25
    assert row['buckets'] == {'current': 0.0, '1_30': 7.25, '31_60': 0.0, '61_90': 0.0, '90_plus': 0.0}
    assert aging['data']['90_plus'] == 0.0 or aging['data']['90_plus'] != 7.25
    _same_four(row, stmt)


def test_a_partial_payment_leaves_the_rest_at_the_charges_own_age():
    """CASE B. 20 charged 45 days ago, 5.5 paid 20 days ago: 14.5 is still
    owed, and it has been owed for 45 days -- the payment reduces the amount,
    not the age."""
    client, cid, bid = _make_shop()
    c = _customer(cid)
    _credit_sale(client, cid, bid, c, 20.0, 45)
    _pay(client, c, 5.5, 20)

    _aging_all, row, stmt = _assert_reconciles(client, c, 14.5)
    assert row['oldest_unpaid_date'] == _iso(45)
    assert row['oldest_unpaid_amount'] == 14.5
    assert row['days_overdue'] == 45
    assert row['overdue_amount'] == 14.5
    assert row['buckets']['31_60'] == 14.5 and sum(row['buckets'].values()) == 14.5
    _same_four(row, stmt)


def test_a_payment_settles_the_oldest_charge_first_and_each_remainder_ages_alone():
    """CASE C. Three 10 charges at 70/40/5 days, then 15 paid 20 days ago.
    Oldest first: the 70-day charge is cleared (10) and 5 comes off the 40-day
    one. Open: 5 at 40 days and 10 at 5 days. The old report aged all 15 at
    the OLDEST charge's 70 days ('61_90')."""
    client, cid, bid = _make_shop()
    c = _customer(cid)
    for d in (70, 40, 5):
        _credit_sale(client, cid, bid, c, 10.0, d)
    _pay(client, c, 15.0, 20)

    _aging_all, row, stmt = _assert_reconciles(client, c, 15.0)
    assert row['buckets'] == {'current': 0.0, '1_30': 10.0, '31_60': 5.0, '61_90': 0.0, '90_plus': 0.0}
    assert row['oldest_unpaid_date'] == _iso(40)
    assert row['oldest_unpaid_amount'] == 5.0
    assert row['days_overdue'] == 40
    assert row['overdue_amount'] == 15.0
    _same_four(row, stmt)


def test_a_prepayment_is_carried_forward_and_consumed_by_the_next_charge():
    """CASE D. 20 paid up front (30 days ago), then a 30 charge (10 days
    ago). The customer owes 10 -- the prepayment is CARRIED FORWARD as credit
    and eaten by the charge, not dropped. The till's client-side FIFO dropped
    it and told the customer 30."""
    client, cid, bid = _make_shop()
    c = _customer(cid)
    _pay(client, c, 20.0, 30)
    _credit_sale(client, cid, bid, c, 30.0, 10)

    aging, row, stmt = _assert_reconciles(client, c, 10.0)
    assert row['oldest_unpaid_amount'] == 10.0, "the reminder must say 10, not 30"
    assert row['oldest_unpaid_date'] == _iso(10)
    assert row['days_overdue'] == 10
    assert row['overdue_amount'] == 10.0
    assert row['buckets']['1_30'] == 10.0 and sum(row['buckets'].values()) == 10.0
    _same_four(row, stmt)


def test_a_credit_note_return_settles_the_charge_it_forgave():
    """CASE E. 2 x 7.25 on credit 60 days ago, then ONE unit returned: 7.25 of
    the debt is forgiven (a `return_forgiven` settlement, no payments row).
    7.25 is left and it is the 60-day charge's remainder -- age 60 is the top
    of the '31_60' bucket."""
    client, cid, bid = _make_shop()
    c = _customer(cid)
    sale = _credit_sale(client, cid, bid, c, 7.25, 60, qty=2, stamp=False)
    ret = _return_one(client, sale)
    assert ret.get('ar_forgiven_amount') == 7.25, ret
    # Age the rows AFTER the return so no return-window rule can refuse it.
    _stamp_row('sales', 'id', sale['id'], _days_ago(60))
    _stamp_row('returns', 'id', ret['id'], _days_ago(30))

    aging, row, stmt = _assert_reconciles(client, c, 7.25)
    assert row['buckets']['31_60'] == 7.25 and sum(row['buckets'].values()) == 7.25
    assert row['oldest_unpaid_date'] == _iso(60)
    assert row['oldest_unpaid_amount'] == 7.25
    assert row['days_overdue'] == 60
    assert row['overdue_amount'] == 7.25
    _same_four(row, stmt)


def test_a_customer_in_credit_has_nothing_overdue_and_no_oldest_unpaid():
    """Prepaid 50, then a 30 charge: the shop owes THEM 20. Not in the
    debtors' list; the statement says overdue 0 and names no oldest charge."""
    client, cid, bid = _make_shop()
    c = _customer(cid)
    _pay(client, c, 50.0, 30)
    _credit_sale(client, cid, bid, c, 30.0, 10)
    assert _credit_balance(c) == -20.0

    aging = _aging(client)
    assert all(row['id'] != c for row in aging['customers']), "a customer in credit is not a debtor"
    stmt = _statement(client, c)
    assert stmt['balance'] == -20.0
    assert stmt['oldest_unpaid_date'] is None
    assert stmt['oldest_unpaid_amount'] is None
    assert stmt['days_overdue'] == 0
    assert stmt['overdue_amount'] == 0.0
    assert stmt['events'][-1]['running_balance'] == -20.0


def test_all_customers_together_the_data_map_is_the_sum_of_every_debtors_buckets():
    """`data` stays the pure bucket map every existing reader sums, and it now
    equals the sum of the per-customer buckets -- which equal the balances."""
    client, cid, bid = _make_shop()
    a, b, c = _customer(cid, 'A'), _customer(cid, 'B'), _customer(cid, 'C')
    _credit_sale(client, cid, bid, a, 14.5, 100); _pay(client, a, 14.5, 50); _credit_sale(client, cid, bid, a, 7.25, 10)
    _credit_sale(client, cid, bid, b, 20.0, 45); _pay(client, b, 5.5, 20)
    _pay(client, c, 20.0, 30); _credit_sale(client, cid, bid, c, 30.0, 10)

    aging = _aging(client)
    assert set(aging['data']) == {'current', '1_30', '31_60', '61_90', '90_plus'}
    assert all(isinstance(v, float) for v in aging['data'].values())
    assert sum(aging['data'].values()) == pytest.approx(7.25 + 14.5 + 10.0, abs=5e-4)
    for k in aging['data']:
        assert aging['data'][k] == pytest.approx(sum(r['buckets'][k] for r in aging['customers']), abs=5e-4)
    assert {r['id'] for r in aging['customers']} == {a, b, c}


def test_payable_is_untouched_and_carries_no_customers_list():
    client, _cid, _bid = _make_shop()
    aging = _aging(client, 'payable')
    assert aging['type'] == 'payable'
    assert 'customers' not in aging
    assert set(aging['data']) == {'current', '1_30', '31_60', '61_90', '90_plus'}


# -- a balance with no event behind it ---------------------------------------

def test_a_balance_nobody_booked_is_surfaced_and_buckets_still_add_up():
    """`credit_balance` written some way other than `_adjust_credit` (a
    fixture UPDATE, an import) has no ledger event. 10 really charged 20 days
    ago, then the column is set to 14.5: 4.5 is unbooked. It is undated ->
    `current`, reported as `unbooked_amount`, EXCLUDED from oldest_unpaid and
    overdue -- and the buckets still add up to the balance."""
    client, cid, bid = _make_shop()
    c = _customer(cid)
    _credit_sale(client, cid, bid, c, 10.0, 20)
    rconn = get_retail_conn()
    rconn.execute("UPDATE customers SET credit_balance=14.5 WHERE id=?", (c,))
    rconn.commit(); rconn.close()

    _aging_all, row, stmt = _assert_reconciles(client, c, 14.5, unbooked=4.5)
    assert row['unbooked_amount'] == 4.5
    assert row['buckets']['1_30'] == 10.0 and row['buckets']['current'] == 4.5
    assert row['oldest_unpaid_date'] == _iso(20)
    assert row['oldest_unpaid_amount'] == 10.0
    assert row['overdue_amount'] == 10.0


def test_a_balance_with_no_events_at_all_is_all_current_and_never_overdue():
    client, cid, _bid = _make_shop()
    c = _customer(cid)
    rconn = get_retail_conn()
    rconn.execute("UPDATE customers SET credit_balance=12.345 WHERE id=?", (c,))
    rconn.commit(); rconn.close()

    aging = _aging(client)
    row = _row(aging, c)
    assert row['buckets']['current'] == 12.345 and sum(row['buckets'].values()) == 12.345
    assert row['unbooked_amount'] == 12.345
    assert row['oldest_unpaid_date'] is None and row['overdue_amount'] == 0.0 and row['days_overdue'] == 0


# -- the allocation on its own -----------------------------------------------

def _ev(sign, amount, day=None, ref='x'):
    return {'sign': sign, 'amount': amount, 'ref': ref, 'date': day}


def test_fifo_unit_a_payment_with_no_open_charge_becomes_credit_a_later_charge_eats():
    open_, credit, unbooked = retail_api_module._fifo_open_charges(
        [_ev(-1, 20.0, ref='pay'), _ev(1, 30.0, day=TODAY, ref='inv')], 10.0)
    assert [(r['ref'], r['amount']) for r in open_] == [('inv', Decimal('10'))]
    assert credit == 0 and unbooked == 0


def test_fifo_unit_an_overpayment_stays_as_credit_and_leaves_nothing_open():
    open_, credit, unbooked = retail_api_module._fifo_open_charges(
        [_ev(1, 10.0, day=TODAY, ref='inv'), _ev(-1, 25.0, ref='pay')], -15.0)
    assert open_ == [] and credit == Decimal('15') and unbooked == 0


def test_fifo_unit_payments_go_to_the_oldest_charge_first():
    d1, d2 = TODAY - timedelta(days=9), TODAY - timedelta(days=3)
    open_, _credit, _u = retail_api_module._fifo_open_charges(
        [_ev(1, 10.0, day=d1, ref='old'), _ev(1, 10.0, day=d2, ref='new'), _ev(-1, 12.0, ref='pay')], 8.0)
    assert [(r['ref'], r['date'], r['amount']) for r in open_] == [('new', d2, Decimal('8'))]


# -- the shop's clock, not UTC -----------------------------------------------

def test_a_sale_just_after_local_midnight_ages_on_the_shops_date_not_utc():
    """A charge rung at 22:30Z on 15 May is 01:30 on the 16th in Amman -- the
    shop's date is the 16th. On 15 June (a midday 'now') that is 30 days old
    and belongs in '1_30'. Measured on the UTC date (the 15th) it is 31 days
    and lands in '31_60'. Both readings are internally consistent; only one is
    the shop's."""
    client, cid, bid = _make_shop()
    c = _customer(cid)
    _credit_sale(client, cid, bid, c, 12.0, 0, at=datetime(2026, 5, 15, 22, 30, 0, tzinfo=timezone.utc))

    _aging_all, row, _stmt = _assert_reconciles(client, c, 12.0)
    assert row['oldest_unpaid_date'] == '2026-05-16'
    assert row['days_overdue'] == 30
    assert row['buckets']['1_30'] == 12.0 and row['buckets']['31_60'] == 0.0


def test_today_is_the_shops_date_not_utc_when_now_is_just_before_utc_midnight():
    """'Now' is 22:30Z on 15 June == 01:30 on 16 June in Amman. A charge at
    midday on 16 May is 31 days old ON THE SHOP'S CLOCK ('31_60'); with
    today read off UTC it would be 30 ('1_30')."""
    client, cid, bid = _make_shop()
    c = _customer(cid)
    _credit_sale(client, cid, bid, c, 12.0, 0, at=datetime(2026, 5, 16, 9, 0, 0, tzinfo=timezone.utc))
    late = datetime(2026, 6, 15, 22, 30, 0, tzinfo=timezone.utc)
    with _frozen(late):
        r = client.get(f"{API}/reports/aging?type=receivable")
        s = client.get(f"{API}/customers/{c}/statement")
    row = _row(r.get_json(), c)
    assert row['days_overdue'] == 31
    assert row['buckets']['31_60'] == 12.0 and row['buckets']['1_30'] == 0.0
    assert s.get_json()['data']['days_overdue'] == 31


def test_the_trading_day_start_hour_moves_a_night_sale_onto_the_previous_business_day():
    """Day starts at 04:00. A charge at 01:30 Amman on 16 May belongs to
    BUSINESS day 15 May: on 15 June that is 31 days ('31_60'), where the
    plain calendar date (16 May) would say 30."""
    client, cid, bid = _make_shop(day_start_hour=4)
    c = _customer(cid)
    _credit_sale(client, cid, bid, c, 12.0, 0, at=datetime(2026, 5, 15, 22, 30, 0, tzinfo=timezone.utc))

    _aging_all, row, _stmt = _assert_reconciles(client, c, 12.0)
    assert row['oldest_unpaid_date'] == '2026-05-15'
    assert row['days_overdue'] == 31
    assert row['buckets']['31_60'] == 12.0


def test_the_start_hour_is_applied_on_the_shops_clock_not_on_utc():
    """Day starts at 04:00. A charge at 01:30Z on 16 May is 04:30 in Amman --
    AFTER the shop's 04:00 opening, so it is business day 16 May (30 days on
    15 June, '1_30'). Applying the same 4h shift to the UTC clock instead
    (21:30 on the 15th) would file it under 15 May / 31 days / '31_60'. The
    previous test cannot tell those two apart; this one can."""
    client, cid, bid = _make_shop(day_start_hour=4)
    c = _customer(cid)
    _credit_sale(client, cid, bid, c, 12.0, 0, at=datetime(2026, 5, 16, 1, 30, 0, tzinfo=timezone.utc))

    _aging_all, row, _stmt = _assert_reconciles(client, c, 12.0)
    assert row['oldest_unpaid_date'] == '2026-05-16'
    assert row['days_overdue'] == 30
    assert row['buckets']['1_30'] == 12.0
