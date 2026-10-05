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
import time
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


def _plant_balance(cid, customer_id, amount):
    """v44: put `amount` on a customer's balance the only way a balance with no
    sale/payment behind it can exist now -- an append-only
    `party_balance_adjustments` row (what the v44 upgrade carry is), followed by
    the cache refresh the boot / post-pull sweep does in production.

    This REPLACES the old `UPDATE customers SET credit_balance=...` fixtures:
    since v44 every report derives the balance from shop-wide rows, so a
    directly poked column is simply overwritten by the next refresh and tells
    these tests nothing. An adjustment row is a real, derivable fact."""
    from core.retail import party_balance
    rconn = get_retail_conn()
    party_balance.insert_carry(rconn, cid, 'customer', customer_id, amount)
    party_balance.refresh_cache(rconn, cid, 'customers', ids=[customer_id])
    rconn.commit(); rconn.close()


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
    # v44 (plan step 8): the 4.5 is now planted as an adjustment row, not a
    # poked column. Every expected FIGURE below is unchanged (stored 14.5,
    # buckets 1_30=10 / current=4.5, oldest 10 at 20 days, overdue 10). The one
    # thing that moved: the 4.5 now has an event behind it (a carried-forward
    # line, undated), so `unbooked_amount` is 0 by construction and the
    # statement closes at the full 14.5. What this can no longer catch: the
    # old "stored balance exceeds the events" gap -- it cannot exist any more
    # because the balance IS the events. What it still catches: a carried
    # amount aging as overdue, or being dropped from the buckets.
    _plant_balance(cid, c, 4.5)

    _aging_all, row, stmt = _assert_reconciles(client, c, 14.5, unbooked=0.0)
    assert row['unbooked_amount'] == 0.0
    assert stmt['unbooked_amount'] == 0.0
    carried = [e for e in stmt['events'] if e['kind'] == 'balance_carried_owed']
    assert len(carried) == 1 and carried[0]['amount'] == 4.5, stmt['events']
    assert row['buckets']['1_30'] == 10.0 and row['buckets']['current'] == 4.5
    assert row['oldest_unpaid_date'] == _iso(20)
    assert row['oldest_unpaid_amount'] == 10.0
    assert row['overdue_amount'] == 10.0


def test_a_balance_with_no_events_at_all_is_all_current_and_never_overdue():
    client, cid, _bid = _make_shop()
    c = _customer(cid)
    # v44 (plan step 8): planted as an adjustment row (see _plant_balance); the
    # figure 12.345 and "all current, never overdue" are unchanged. Only
    # `unbooked_amount` differs: the balance now has a carried event behind it,
    # so nothing is unexplained (0). It can no longer catch an unexplained
    # stored balance -- that state no longer exists.
    _plant_balance(cid, c, 12.345)

    aging = _aging(client)
    row = _row(aging, c)
    assert row['buckets']['current'] == 12.345 and sum(row['buckets'].values()) == 12.345
    assert row['unbooked_amount'] == 0.0
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


# =============================================================================
# ROUND 7 -- the verifier's findings on this allocation (F1-F5). Evidence:
# jobs/c5a4f4c4/tmp/p1a/V/AGING (probes p2, p4, p4b, p4c, p7, p11, p13, p14b).
# =============================================================================

# -- helpers for rows the routes will not write for us ------------------------

def _plant_sale(rconn, company_id, branch_id, customer_id, total, days):
    """A completed credit sale written by raw INSERT, like a sync-applied one:
    it moves NO credit_balance."""
    created_at, created_at_utc = _stamp(_days_ago(days))
    return rconn.execute(
        "INSERT INTO sales (company_id, sale_number, branch_id, customer_id, subtotal, total, amount_paid, "
        "payment_method, status, created_at, created_at_utc) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (company_id, f'PL-{uuid.uuid4().hex[:10]}', branch_id, customer_id, total, total, 0,
         'credit', 'completed', created_at, created_at_utc)).lastrowid


def _plant_payment(rconn, company_id, party_type, party_id, direction, amount, days, related_type=None):
    created_at, _utc = _stamp(_days_ago(days))
    return rconn.execute(
        "INSERT INTO payments (company_id, reference, party_type, party_id, direction, amount, currency, fx_rate, "
        "method, status, related_type, created_at) VALUES (?,?,?,?,?,?,?,1,?,?,?,?)",
        (company_id, f'PLP-{uuid.uuid4().hex[:10]}', party_type, party_id, direction, amount, 'JOD',
         'cash', 'active', related_type, created_at)).lastrowid


def _plant_return(rconn, company_id, branch_id, sale_id, ar_forgiven, store_credit, days=3):
    created_at, created_at_utc = _stamp(_days_ago(days))
    return rconn.execute(
        "INSERT INTO returns (company_id, return_number, sale_id, branch_id, refund_amount, status, created_at, "
        "created_at_utc, ar_forgiven_amount, store_credit_amount, tender_refund_amount) VALUES (?,?,?,?,?,?,?,?,?,?,0)",
        (company_id, f'PLR-{uuid.uuid4().hex[:10]}', sale_id, branch_id, ar_forgiven + store_credit,
         'completed', created_at, created_at_utc, ar_forgiven, store_credit)).lastrowid


def _set_balance(customer_id, value):
    rconn = get_retail_conn()
    rconn.execute("UPDATE customers SET credit_balance=? WHERE id=?", (value, customer_id))
    rconn.commit(); rconn.close()


def _cheque(client, customer_id, amount, number='CHQ-1'):
    """A real customer cheque through the real route (this writes the cheque,
    its 'created' event and the money leg: a payment 'in', crossing 0)."""
    r = client.post(f"{API}/cheques", json={
        'direction': 'in', 'party_id': customer_id, 'amount': amount,
        'due_date': '2026-07-01', 'cheque_number': number})
    assert r.status_code == 201, r.get_json()
    rconn = get_retail_conn()
    cheque_id = rconn.execute("SELECT id FROM cheques WHERE party_id=? AND cheque_number=?",
                              (customer_id, number)).fetchone()[0]
    rconn.close()
    return cheque_id


def _cheque_action(client, cheque_id, action, **body):
    r = client.post(f"{API}/cheques/{cheque_id}/{action}", json=body)
    assert r.status_code == 200, r.get_json()


def _restamp_crossing(cheque_id, k, days):
    """Date the money leg of crossing `k` (payments carry only a wall-clock
    `created_at`), so a test never depends on the real date the suite runs."""
    uid = retail_api_module.cheque_engine.money_uid(cheque_id, k)
    rconn = get_retail_conn()
    n = rconn.execute("UPDATE payments SET created_at=? WHERE uid=?", (_stamp(_days_ago(days))[0], uid)).rowcount
    rconn.commit(); rconn.close()
    assert n == 1, f"crossing {k} of cheque {cheque_id} wrote {n} payment rows, expected exactly 1"


def _payment_row(pid):
    rconn = get_retail_conn()
    row = rconn.execute("SELECT * FROM payments WHERE id=?", (pid,)).fetchone()
    rconn.close()
    return dict(row)


def _cheque_payment_id(cheque_id, k):
    uid = retail_api_module.cheque_engine.money_uid(cheque_id, k)
    rconn = get_retail_conn()
    pid = rconn.execute("SELECT id FROM payments WHERE uid=?", (uid,)).fetchone()[0]
    rconn.close()
    return pid


def _void(client, pid, reason='r7 test'):
    return client.post(f"{API}/payments/{pid}/void", json={'reason': reason})


# -- F1: the report is set-based ----------------------------------------------

def _seed_debtors(company_id, branch_id, n):
    """`n` debtors, each: 6 credit sales of 10 (120,100,...,20 days old), 3
    payments of 5 (100/70/40 days), one 2.0 forgiven return (15 days). Every
    one owes 43 and is bucketed {1_30: 10, 31_60: 20, 61_90: 10, 90_plus: 3}
    (worked by hand in the timing test below). Raw INSERTs -- the point is
    the shape of the data, not the routes that would have written it."""
    rconn = get_retail_conn()
    ids = []
    for i in range(n):
        c = str(uuid.uuid4())
        rconn.execute("INSERT INTO customers (id,company_id,name,credit_mode,credit_limit,credit_balance) "
                      "VALUES (?,?,?,'unlimited',0,0)", (c, company_id, f'Perf {i}'))
        sale_ids = [_plant_sale(rconn, company_id, branch_id, c, 10.0, 120 - j * 20) for j in range(6)]
        for j in range(3):
            _plant_payment(rconn, company_id, 'customer', c, 'in', 5.0, 100 - j * 30)
        _plant_return(rconn, company_id, branch_id, sale_ids[-1], 2.0, 0.0, days=15)
        rconn.execute("UPDATE customers SET credit_balance=? WHERE id=?", (43.0, c))
        ids.append(c)
    rconn.commit(); rconn.close()
    return ids


def _seed_noise(company_id, branch_id, n_sales):
    """`n_sales` walk-in cash sales, every 7th with a tender-refund return --
    the rest of a real shop's tables that the debtors' queries must not scan
    once per debtor."""
    rconn = get_retail_conn()
    for k in range(n_sales):
        created_at, created_at_utc = _stamp(_days_ago(k % 300))
        cur = rconn.execute(
            "INSERT INTO sales (company_id, sale_number, branch_id, customer_id, subtotal, total, amount_paid, "
            "payment_method, status, created_at, created_at_utc) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (company_id, f'NS-{uuid.uuid4().hex[:10]}', branch_id, None, 3.0, 3.0, 3.0, 'cash', 'completed',
             created_at, created_at_utc))
        if k % 7 == 0:
            rconn.execute(
                "INSERT INTO returns (company_id, return_number, sale_id, branch_id, refund_amount, status, "
                "created_at, created_at_utc, ar_forgiven_amount, store_credit_amount, tender_refund_amount) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (company_id, f'NR-{uuid.uuid4().hex[:10]}', cur.lastrowid, branch_id, 3.0, 'completed',
                 created_at, created_at_utc, 0, 0, 3.0))
    rconn.commit(); rconn.close()


def test_the_report_over_500_debtors_20k_sales_and_3k_returns_is_fast_and_exact():
    """F1. The report ran the four ledger queries once PER DEBTOR; the returns
    UNION scans `returns` from the top each time: 1.45 s for 500 debtors, 4.0 s
    once a busy shop's returns were in. The verifier's dataset, and the
    milliseconds are PRINTED (run with -s). The ceiling is deliberately loose
    (a slow CI box must not flake it) -- the query-count test below is the
    machine-independent guard; this one guards the real end-to-end figure.

    Hand-worked allocation for each debtor (charges 10 @120,100,80,60,40,20d;
    pays 5 @100,70,40d; forgive 2 @15d): open = 3 @100d, 10 @80d, 10 @60d,
    10 @40d, 10 @20d -> 90_plus 3, 61_90 10, 31_60 20 (60d is the top of that
    bucket), 1_30 10 -- 43 in all."""
    client, cid, bid = _make_shop()
    _seed_debtors(cid, bid, 500)
    _seed_noise(cid, bid, 20000)
    rconn = get_retail_conn()
    n_returns = rconn.execute("SELECT COUNT(*) FROM returns WHERE company_id=?", (cid,)).fetchone()[0]
    rconn.close()
    assert n_returns > 3000, n_returns
    _aging(client)                       # warm: schema check, zone database, page cache
    timings = []
    for _ in range(3):
        started = time.perf_counter()
        aging = _aging(client)
        timings.append(round((time.perf_counter() - started) * 1000))
    print(f"\nAGING_MS 500 debtors / 20k sales / {n_returns} returns: {timings} ms (best {min(timings)})")

    assert len(aging['customers']) == 500
    assert aging['data'] == {'current': 0.0, '1_30': 5000.0, '31_60': 10000.0, '61_90': 5000.0, '90_plus': 1500.0}
    assert all(r['buckets'] == {'current': 0.0, '1_30': 10.0, '31_60': 20.0, '61_90': 10.0, '90_plus': 3.0}
               and r['unbooked_amount'] == 0.0 and r['oldest_unpaid_amount'] == 3.0
               and r['days_overdue'] == 100 for r in aging['customers'])
    assert min(timings) < 1000, f"aging report took {timings} ms over 500 debtors"


def _statements_run(client, n_debtors):
    """The SQL statements one aging report issues for a fresh shop with
    `n_debtors` debtors (after a warm-up call)."""
    _c, cid, bid = None, None, None
    client, cid, bid = _make_shop()
    _seed_debtors(cid, bid, n_debtors)
    _aging(client)
    seen = []
    original = retail_api_module.get_retail_conn

    def counting():
        conn = original()
        conn.set_trace_callback(seen.append)
        return conn

    retail_api_module.get_retail_conn = counting
    try:
        _aging(client)
    finally:
        retail_api_module.get_retail_conn = original
    return seen


def test_the_report_issues_a_fixed_number_of_queries_however_many_debtors_there_are():
    """F1, machine-independent. A per-debtor loop is the defect; its signature
    is a statement count that grows with the debtor count and a `returns`
    scan per debtor. 4 debtors and 40 debtors must cost the SAME number of
    statements, and `returns` is read by exactly one of them."""
    few = _statements_run(None, 4)
    many = _statements_run(None, 40)
    print(f"\nAGING_STATEMENTS 4 debtors: {len(few)}, 40 debtors: {len(many)}")
    assert len(few) == len(many), (len(few), len(many))
    # v44 (plan step 8): was `== 1`. The shop-wide derivation (party_balance)
    # reads `returns` twice more, once for the batch customer balances and once
    # for the statement events' all-debtor pass -- a CONSTANT 3, measured. The
    # 4-debtor == 40-debtor equality above is still the N+1 guard and is
    # untouched. What this can no longer catch: up to two EXTRA constant
    # `returns` scans sneaking in. It still catches any per-debtor query (the
    # count would grow with the debtor count and break the equality above).
    assert sum(1 for s in many if 'FROM returns' in s) == 3, [s[:80] for s in many if 'FROM returns' in s]


# -- F2: a balance LOWER than its events -------------------------------------

def test_fifo_unit_a_negative_unbooked_amount_comes_off_the_newest_charge_never_the_oldest():
    d_old, d_new = TODAY - timedelta(days=80), TODAY - timedelta(days=5)
    # events say 30 owed, the column says 10: 20 is missing. It cannot be the
    # 80-day debt that vanished -- the newest charge is the one not on the books.
    open_, credit, unbooked = retail_api_module._fifo_open_charges(
        [_ev(1, 10.0, day=d_old, ref='old'), _ev(1, 20.0, day=d_new, ref='new')], 10.0)
    assert [(r['ref'], r['date'], r['amount']) for r in open_] == [('old', d_old, Decimal('10'))]
    assert credit == 0 and unbooked == Decimal('-20')


def test_fifo_unit_a_negative_unbooked_amount_spills_backwards_and_the_rest_is_credit():
    d1, d2 = TODAY - timedelta(days=80), TODAY - timedelta(days=5)
    open_, credit, unbooked = retail_api_module._fifo_open_charges(
        [_ev(1, 10.0, day=d1, ref='a'), _ev(1, 20.0, day=d2, ref='b')], 4.0)
    # 26 missing: all of 'b' (20) then 6 of 'a' -> 4 left of the OLDEST, which is right:
    # what survives is the oldest, everything newer is what "isn't on the books".
    assert [(r['ref'], r['amount']) for r in open_] == [('a', Decimal('4'))]
    assert credit == 0 and unbooked == Decimal('-26')
    open_, credit, _u = retail_api_module._fifo_open_charges(
        [_ev(1, 10.0, day=d1, ref='a'), _ev(1, 20.0, day=d2, ref='b')], -5.0)
    assert open_ == [] and credit == Decimal('5')     # more missing than owed: the rest is credit


def test_a_synced_sale_the_balance_never_booked_does_not_shorten_the_oldest_debt():
    """F2 / probe p14b. A sale that ARRIVED BY SYNC moves no `credit_balance`
    on the receiving device (same INSERT shape as sync_service). Local 10.0
    charged 80 days ago is really owed; a synced 20.0 from 5 days ago is in
    the events but not in the balance (10.0). The old code took the 20 off the
    OLDEST charge first and reported the 80-day debt gone -- ageing 10 owed at
    5 days instead of 80. It must stay in 61_90, and the buckets still add
    up to the balance."""
    client, cid, bid = _make_shop()
    c = _customer(cid)
    _credit_sale(client, cid, bid, c, 10.0, 80)
    rconn = get_retail_conn()
    _plant_sale(rconn, cid, bid, c, 20.0, 5)
    # v44 (plan step 8): the premise of this test -- "a synced sale moves no
    # balance on the receiving computer" -- IS the bug v44 fixes: the balance is
    # now derived from the shop-wide rows, so the synced 20 counts. The refresh
    # below is what the post-pull hook does in production. Expected figures
    # therefore CHANGE (this is the one test in the file where they must):
    # balance 30 (was 10), unbooked 0 (was -20), buckets 61_90=10 + 1_30=20.
    # The behaviour this test exists for is unchanged and still asserted: the
    # 80-day debt is NOT shortened by the newer sale (oldest 10 @ 80 days stays
    # in 61_90). What it can no longer catch at route level: the NEGATIVE
    # unbooked path (balance lower than events) -- it cannot occur any more;
    # that allocation rule stays pinned by the two fifo_unit tests above, which
    # feed `_fifo_open_charges` a lower balance directly.
    from core.retail import party_balance
    party_balance.refresh_cache(rconn, cid, 'customers', ids=[c])
    rconn.commit(); rconn.close()

    _aging_all, row, stmt = _assert_reconciles(client, c, 30.0, unbooked=0.0)
    assert row['unbooked_amount'] == 0.0
    assert stmt['unbooked_amount'] == 0.0
    assert row['buckets'] == {'current': 0.0, '1_30': 20.0, '31_60': 0.0, '61_90': 10.0, '90_plus': 0.0}
    assert row['oldest_unpaid_date'] == _iso(80) and row['days_overdue'] == 80
    assert row['oldest_unpaid_amount'] == 10.0 and row['overdue_amount'] == 30.0
    _same_four(row, stmt)


# -- F3: the guards that had no test -----------------------------------------

def test_another_companys_rows_naming_this_customers_id_never_reach_their_ledger():
    """F3 / M5, probe p7. Every event query keeps its own `company_id` filter
    (the debtors subselect is a narrowing, not a tenant guard). Plant, for
    company B, one row per table that NAMES company A's customer id -- the
    shape a sync or import mis-map would leave -- plus BOTH return shapes on
    each side of the JOIN (`r.company_id` and `s.company_id` are separate
    filters, and each UNION half has its own copy). Every planted return
    carries BOTH an ar_forgiven and a store-credit amount, so dropping any one
    of the four settlement filters shows up. Buckets alone would NOT catch a
    leak (a phantom event just moves `unbooked_amount`), so the assertion is
    the event LIST and `unbooked_amount == 0`."""
    client_a, cid_a, bid_a = _make_shop()
    client_b, cid_b, bid_b = _make_shop()
    a1 = _customer(cid_a, 'A1')
    sale_a = _credit_sale(client_a, cid_a, bid_a, a1, 11.0, 15)
    rconn = get_retail_conn()
    b_sale = _plant_sale(rconn, cid_b, bid_b, a1, 999.0, 200)          # charges: sales.company_id
    _plant_payment(rconn, cid_b, 'customer', a1, 'in', 5.0, 100)        # receipts: payments.company_id
    _plant_return(rconn, cid_b, bid_b, sale_a['id'], 3.0, 2.0)          # settlements: returns.company_id
    _plant_return(rconn, cid_a, bid_a, b_sale, 3.0, 2.0)                # settlements: sales.company_id
    rconn.commit(); rconn.close()

    _aging_all, row, stmt = _assert_reconciles(client_a, a1, 11.0)
    assert [e['kind'] for e in stmt['events']] == ['charge'], stmt['events']
    assert row['unbooked_amount'] == 0.0
    assert row['buckets'] == {'current': 0.0, '1_30': 11.0, '31_60': 0.0, '61_90': 0.0, '90_plus': 0.0}
    assert all(r['id'] != a1 for r in _aging(client_b)['customers'])


def test_a_return_that_paid_out_as_store_credit_settles_the_debt_and_stays_on_the_ledger():
    """F3 / M4b, probe p2. The settlements query is a UNION of two halves:
    `ar_forgiven_amount` and `store_credit_amount`. The earlier cases only
    ever forgave debt, so dropping the store-credit half went unnoticed.
    Sale 10, then 15 paid (the customer is 5 in credit), then the 10 sale is
    returned: nothing is owed on it, so the whole 10 goes back as STORE CREDIT
    (a `return_credit` event, no payments row, the balance moves to -15). A
    new 20 charge then leaves 5 owed. The ledger must carry the credit event:
    with the half missing the statement closes 10 higher than the column."""
    client, cid, bid = _make_shop()
    c = _customer(cid)
    sale = _credit_sale(client, cid, bid, c, 10.0, 0, stamp=False)
    _pay(client, c, 15.0, 0, at=datetime.now(timezone.utc) - timedelta(minutes=1))
    ret = _return_one(client, sale)
    assert ret['ar_forgiven_amount'] == 0 and ret['store_credit_amount'] == 10.0, ret
    _stamp_row('sales', 'id', sale['id'], _days_ago(50))
    _stamp_row('returns', 'id', ret['id'], _days_ago(40))
    rconn = get_retail_conn()
    rconn.execute("UPDATE payments SET created_at=? WHERE party_id=?", (_stamp(_days_ago(45))[0], c))
    rconn.commit(); rconn.close()
    _credit_sale(client, cid, bid, c, 20.0, 5)

    _aging_all, row, stmt = _assert_reconciles(client, c, 5.0)
    kinds = [e['kind'] for e in stmt['events']]
    assert kinds == ['charge', 'payment', 'return_credit', 'charge'], kinds
    assert row['unbooked_amount'] == 0.0
    assert row['buckets'] == {'current': 0.0, '1_30': 5.0, '31_60': 0.0, '61_90': 0.0, '90_plus': 0.0}
    assert row['oldest_unpaid_date'] == _iso(5) and row['days_overdue'] == 5
    _same_four(row, stmt)


def test_overdue_starts_at_one_full_shop_day_not_at_a_calendar_edge():
    """F3 / M6, probe p11. 'now' is 15:00 on 15 June in Amman. A charge at
    23:59 on the 14th is ONE day old (overdue, `1_30`); one at 00:01 on the
    15th is today's (`current`, not overdue). `_AGING_CURRENT_MAX_DAYS = 1`
    would fold the first into `current` as well; the 12 non-boundary cases
    above never sit on either side of that edge."""
    client, cid, bid = _make_shop()
    c = _customer(cid)
    _credit_sale(client, cid, bid, c, 1.0, 0, at=datetime(2026, 6, 14, 20, 59, tzinfo=timezone.utc))   # 23:59 Amman
    _credit_sale(client, cid, bid, c, 2.0, 0, at=datetime(2026, 6, 14, 21, 1, tzinfo=timezone.utc))    # 00:01 Amman

    _aging_all, row, stmt = _assert_reconciles(client, c, 3.0)
    assert row['buckets'] == {'current': 2.0, '1_30': 1.0, '31_60': 0.0, '61_90': 0.0, '90_plus': 0.0}
    assert row['oldest_unpaid_date'] == '2026-06-14' and row['days_overdue'] == 1
    assert row['overdue_amount'] == 1.0
    _same_four(row, stmt)


# -- F4: a bounced cheque UNDOES the payment ----------------------------------

def test_fifo_unit_a_reversal_that_names_its_payment_cancels_both_rows():
    d1, d2 = TODAY - timedelta(days=80), TODAY - timedelta(days=10)
    events = [_ev(1, 50.0, day=d1, ref='a'), _ev(1, 20.0, day=d2, ref='b'),
              dict(_ev(-1, 50.0, day=TODAY, ref='chq'), uid='u-pay'),
              dict(_ev(1, 50.0, day=TODAY, ref='chq-back'), uid='u-rev', undoes='u-pay')]
    open_, credit, unbooked = retail_api_module._fifo_open_charges(events, 70.0)
    assert [(r['ref'], r['date'], r['amount']) for r in open_] == [
        ('a', d1, Decimal('50')), ('b', d2, Decimal('20'))]
    assert credit == 0 and unbooked == 0


def test_fifo_unit_a_reversal_whose_payment_is_not_in_the_set_stays_an_ordinary_charge():
    """No target row -> nothing to cancel: the reversal is a +50 dated where it
    happened (the only honest reading of a row that names nothing we hold)."""
    d1 = TODAY - timedelta(days=80)
    events = [_ev(1, 50.0, day=d1, ref='a'), dict(_ev(1, 50.0, day=TODAY, ref='rev'), uid='u-rev', undoes='gone')]
    open_, _credit, _u = retail_api_module._fifo_open_charges(events, 100.0)
    assert [(r['ref'], r['amount']) for r in open_] == [('a', Decimal('50')), ('rev', Decimal('50'))]


def test_a_bounced_cheque_takes_the_payment_back_it_does_not_open_a_charge_dated_on_the_bounce():
    """F4 / probe p4. 50 charged 80 days ago, 20 charged 10 days ago, then a
    50 cheque is taken and bounces. The bounce takes the cheque's payment
    back: the customer owes exactly what they did before the cheque -- the 50
    at ITS OWN 80 days, and the 20 at 10. The old reading kept the payment
    (eating the 80-day charge) and added a NEW 50 dated on the bounce day, so
    the 80-day debt read as brand new."""
    client, cid, bid = _make_shop()
    c = _customer(cid)
    _credit_sale(client, cid, bid, c, 50.0, 80)
    _credit_sale(client, cid, bid, c, 20.0, 10)
    chq = _cheque(client, c, 50.0)
    _restamp_crossing(chq, 0, 6)

    _a, row, _s = _assert_reconciles(client, c, 20.0)          # while the cheque stands: paid the 80-day one
    assert row['buckets'] == {'current': 0.0, '1_30': 20.0, '31_60': 0.0, '61_90': 0.0, '90_plus': 0.0}

    _cheque_action(client, chq, 'bounce', reason='NSF')
    _restamp_crossing(chq, 1, 2)
    _aging_all, row, stmt = _assert_reconciles(client, c, 70.0)
    assert row['buckets'] == {'current': 0.0, '1_30': 20.0, '31_60': 0.0, '61_90': 50.0, '90_plus': 0.0}
    assert row['oldest_unpaid_date'] == _iso(80) and row['oldest_unpaid_amount'] == 50.0
    assert row['days_overdue'] == 80 and row['overdue_amount'] == 70.0
    assert row['unbooked_amount'] == 0.0
    # the LEDGER still tells the truth: both money rows are listed, just not allocated
    assert [e['kind'] for e in stmt['events']] == ['charge', 'charge', 'payment', 'reversal']
    _same_four(row, stmt)


def test_a_reversal_that_names_no_cheque_we_hold_is_still_a_charge_and_the_books_still_add_up():
    """F4 fallback. Sever the reversal's link (its uid no longer derives from
    any cheque of this company -- a peer's row, a cheque never synced here).
    The engine must not guess: the row is an ordinary +50 dated when it was
    written, and the buckets still add up to the balance."""
    client, cid, bid = _make_shop()
    c = _customer(cid)
    _credit_sale(client, cid, bid, c, 50.0, 80)
    _credit_sale(client, cid, bid, c, 20.0, 10)
    chq = _cheque(client, c, 50.0)
    _restamp_crossing(chq, 0, 6)
    _cheque_action(client, chq, 'bounce', reason='NSF')
    _restamp_crossing(chq, 1, 2)
    rconn = get_retail_conn()
    n = rconn.execute("UPDATE payments SET uid=? WHERE uid=?",
                      (str(uuid.uuid4()), retail_api_module.cheque_engine.money_uid(chq, 1))).rowcount
    rconn.commit(); rconn.close()
    assert n == 1

    _aging_all, row, _stmt = _assert_reconciles(client, c, 70.0)
    # 50@80d is eaten by the cheque payment, then the unlinked reversal opens 50 on day -2
    assert row['buckets'] == {'current': 0.0, '1_30': 70.0, '31_60': 0.0, '61_90': 0.0, '90_plus': 0.0}
    assert row['oldest_unpaid_date'] == _iso(10)


def test_a_cheque_reinstated_and_bounced_again_still_undoes_each_payment_with_its_own_reversal():
    """F4. A cheque crossing 4 times writes 4 money rows: in(k0), out(k1),
    in(k2), out(k3). Each reversal cancels the payment just before it: after
    the reinstatement only k2 stands (the customer is 20 in debt), and after
    the second bounce nothing does (70 again, the 80-day charge still 80)."""
    client, cid, bid = _make_shop()
    c = _customer(cid)
    _credit_sale(client, cid, bid, c, 50.0, 80)
    _credit_sale(client, cid, bid, c, 20.0, 10)
    chq = _cheque(client, c, 50.0)
    _restamp_crossing(chq, 0, 6)
    _cheque_action(client, chq, 'bounce', reason='NSF')
    _restamp_crossing(chq, 1, 5)
    _cheque_action(client, chq, 'reinstate')
    _restamp_crossing(chq, 2, 4)

    _aging_all, row, _stmt = _assert_reconciles(client, c, 20.0)
    assert row['buckets'] == {'current': 0.0, '1_30': 20.0, '31_60': 0.0, '61_90': 0.0, '90_plus': 0.0}

    _cheque_action(client, chq, 'bounce', reason='NSF again')
    _restamp_crossing(chq, 3, 3)
    _aging_all, row, _stmt = _assert_reconciles(client, c, 70.0)
    assert row['buckets'] == {'current': 0.0, '1_30': 20.0, '31_60': 0.0, '61_90': 50.0, '90_plus': 0.0}
    assert row['oldest_unpaid_date'] == _iso(80) and row['days_overdue'] == 80


# -- F5: void_payment and cheque money rows -----------------------------------

def test_a_cheque_bounce_reversal_row_cannot_be_voided_it_would_double_count_the_debt():
    """F5 / probe p4b (PRE-EXISTING defect). `void_payment` ADDED the amount
    for every customer row. The bounce's 'out' reversal had already raised the
    debt by 50; voiding it added 50 again -- 100 owed on a 50 charge."""
    client, cid, bid = _make_shop()
    c = _customer(cid)
    _credit_sale(client, cid, bid, c, 50.0, 80)
    chq = _cheque(client, c, 50.0)
    _cheque_action(client, chq, 'bounce', reason='NSF')
    rev = _cheque_payment_id(chq, 1)
    assert _payment_row(rev)['direction'] == 'out' and _credit_balance(c) == 50.0

    r = _void(client, rev)
    assert r.status_code == 409, r.get_json()
    assert r.get_json()['code'] == 'CHEQUE_PAYMENT_NOT_VOIDABLE'
    assert _credit_balance(c) == 50.0
    assert _payment_row(rev)['status'] == 'active'


def test_a_cheque_receipt_cannot_be_voided_and_then_bounced_that_took_the_money_back_twice():
    """F5 / probe p4c (PRE-EXISTING defect). Void the cheque's 'in' row (the
    customer owes 50 again), then bounce the still-live cheque: the bounce
    reverses a payment that was already gone -> 100 owed on a 50 charge."""
    client, cid, bid = _make_shop()
    c = _customer(cid)
    _credit_sale(client, cid, bid, c, 50.0, 80)
    chq = _cheque(client, c, 50.0)
    receipt = _cheque_payment_id(chq, 0)
    assert _payment_row(receipt)['direction'] == 'in' and _credit_balance(c) == 0.0

    r = _void(client, receipt)
    assert r.status_code == 409, r.get_json()
    assert r.get_json()['code'] == 'CHEQUE_PAYMENT_NOT_VOIDABLE'
    assert _credit_balance(c) == 0.0 and _payment_row(receipt)['status'] == 'active'
    _cheque_action(client, chq, 'bounce', reason='NSF')
    assert _credit_balance(c) == 50.0, "50 was charged, 50 owed -- never 100"


def test_an_ordinary_account_payment_still_voids_and_puts_the_debt_back():
    """F5, the ALLOW half: refusing cheque rows must not cost the till its own
    correction. A customer 'in' payment voids -> the debt is back."""
    client, cid, bid = _make_shop()
    c = _customer(cid)
    _credit_sale(client, cid, bid, c, 30.0, 40)
    _pay(client, c, 10.0, 20)
    rconn = get_retail_conn()
    pid = rconn.execute("SELECT id FROM payments WHERE party_id=? AND direction='in'", (c,)).fetchone()[0]
    rconn.close()
    assert _credit_balance(c) == 20.0

    r = _void(client, pid)
    assert r.status_code == 200, r.get_json()
    _aging_all, row, _stmt = _assert_reconciles(client, c, 30.0)
    assert row['buckets']['31_60'] == 30.0 and sum(row['buckets'].values()) == 30.0


def test_the_void_sign_follows_the_rows_direction_for_a_customer_row_that_raised_the_balance():
    """F5, direction. A customer row with direction 'out' (that is not a
    cheque) moved the balance UP when it was written -- a peer's synced row,
    say. Voiding it must take it back DOWN. The old hardcoded +amount added it
    a second time (30 owed + 8 written = 38; the void made 46, not 30)."""
    client, cid, bid = _make_shop()
    c = _customer(cid)
    _credit_sale(client, cid, bid, c, 30.0, 40)
    rconn = get_retail_conn()
    pid = _plant_payment(rconn, cid, 'customer', c, 'out', 8.0, 10)
    rconn.execute("UPDATE customers SET credit_balance=38.0 WHERE id=?", (c,))
    rconn.commit(); rconn.close()

    r = _void(client, pid)
    assert r.status_code == 200, r.get_json()
    assert _credit_balance(c) == 30.0


def _supplier(cid, balance):
    rconn = get_retail_conn()
    sid = str(uuid.uuid4())
    # v44 (plan step 8): the opening payable is an adjustment row (what the v44
    # upgrade carry is), not a poked `credit_balance` -- the supplier balance is
    # derived from shop-wide rows now, so a bare column is overwritten by the
    # next refresh. Same figure, same polarity (+ = we owe the supplier).
    from core.retail import party_balance
    rconn.execute("INSERT INTO suppliers (id,company_id,name,credit_balance) VALUES (?,?,?,0)",
                  (sid, cid, f'Sup {sid[:6]}'))
    party_balance.insert_carry(rconn, cid, 'supplier', sid, balance)
    party_balance.refresh_cache(rconn, cid, 'suppliers', ids=[sid])
    rconn.commit(); rconn.close()
    return sid


def _supplier_balance(sid):
    rconn = get_retail_conn()
    bal = rconn.execute("SELECT credit_balance FROM suppliers WHERE id=?", (sid,)).fetchone()[0]
    rconn.close()
    return bal


def test_a_supplier_payment_still_voids_and_reopens_the_payable_and_the_opposite_row_goes_down():
    """F5, direction on the supplier side. An ordinary supplier payment is
    'out' and REDUCED the payable: voiding reopens it (+). A supplier row with
    direction 'in' is the other polarity and raised it: voiding takes it down."""
    client, cid, _bid = _make_shop()
    sid = _supplier(cid, 40.0)
    r = client.post(f"{API}/suppliers/{sid}/payments", json={'amount': 15.0, 'method': 'cash'})
    assert r.status_code == 200, r.get_json()
    assert _supplier_balance(sid) == 25.0
    rconn = get_retail_conn()
    pid = rconn.execute("SELECT id FROM payments WHERE party_type='supplier' AND party_id=?", (sid,)).fetchone()[0]
    rconn.close()
    assert _void(client, pid).status_code == 200
    assert _supplier_balance(sid) == 40.0

    rconn = get_retail_conn()
    odd = _plant_payment(rconn, cid, 'supplier', sid, 'in', 8.0, 3)
    # v44: the planted 'in' row raises the DERIVED payable to 48; the refresh
    # stands in for the boot / post-pull sweep (was `UPDATE ... =48.0`).
    from core.retail import party_balance
    party_balance.refresh_cache(rconn, cid, 'suppliers', ids=[sid])
    rconn.commit(); rconn.close()
    assert _supplier_balance(sid) == 48.0
    assert _void(client, odd).status_code == 200
    assert _supplier_balance(sid) == 40.0
