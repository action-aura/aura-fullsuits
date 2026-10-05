"""Aura Retail -- core/retail/party_balance.py (schema v44, step 1).

Three things are pinned here, each for a reason that cost something:

  1. THE DERIVATION IS THE STATEMENT. For a mixed day driven through the REAL
     routes (credit sale, partial payment, cash sale to a named customer,
     points sale, partial return that forgives debt, supplier PO with a
     down-payment, instalment, direct supplier payment) the derived balance
     of every customer equals the signed sum of `_statement_events` AND the
     stored cache the writers produce today. If a term of the formula were
     dropped (the store-credit leg, the `related_type <> 'sale'` exclusion)
     one of these customers would disagree.
  2. THE SUM IS ORDER-INDEPENDENT. Ten thousand random fils amounts summed
     in two different row orders come back identical, and equal the Decimal
     sum. Two computers holding the same rows in a different insert order
     must show the same balance; a float SUM does not promise that.
  3. THE EPSILON IS THE ONE create_sale / create_return use. party_balance
     keeps its own copy (it cannot import retail_api), so a test pins the
     two together for every currency class -- a gate here that drifted from
     the writers' gate would call a sale a debt that create_sale did not.

Self-contained bootstrap, per this directory's convention (no conftest.py).

Run:
    pytest products/retail/tests/retail_party_balance_unit_test.py -v
"""
import os
import random
import shutil
import sqlite3
import sys
import tempfile
import uuid
from decimal import Decimal
from pathlib import Path

import pytest

PRODUCT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = PRODUCT_DIR / 'backend'
SUITE_ROOT = PRODUCT_DIR.parent.parent
for _p in (str(SUITE_ROOT), str(BACKEND_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

DATA = Path(tempfile.mkdtemp(prefix="aura_retail_party_balance_unit_"))
(DATA / "database" / "subsystems").mkdir(parents=True, exist_ok=True)
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

from commercial_runtime.identity.registry_db import get_conn as registry_conn  # noqa: E402
from commercial_runtime.security.passwords import hash_password  # noqa: E402
from database.schema import get_retail_conn  # noqa: E402
from api import retail_api  # noqa: E402
from core.retail import party_balance as pb  # noqa: E402


def teardown_module(module):
    shutil.rmtree(DATA, ignore_errors=True)


def _make_admin(price=100.0, stock=50):
    email = f"pb-{uuid.uuid4().hex[:10]}@test.local"
    password = "PartyBalancePW1"
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
        "SELECT id FROM branches WHERE company_id=? ORDER BY id LIMIT 1", (company_id,)).fetchone()[0]
    sku = f'PB-{uuid.uuid4().hex[:8]}'
    rconn.execute(
        "INSERT INTO products (id,company_id,sku,name,cost_price,sell_price,tax_rate) VALUES (?,?,?,?,?,?,0)",
        (str(uuid.uuid4()), company_id, sku, sku, price / 2, price))
    pid = rconn.execute("SELECT id FROM products WHERE company_id=? AND sku=?", (company_id, sku)).fetchone()[0]
    rconn.execute(
        "INSERT INTO inventory_balances (company_id,product_id,branch_id,quantity_on_hand) VALUES (?,?,?,?)",
        (company_id, pid, branch_id, stock))
    rconn.commit()
    rconn.close()
    client = app.test_client()
    r = client.post("/api/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.get_json()
    client.get("/api/sub/retail/settings/tax")
    # sale_number / return_number carry bare UNIQUE constraints; see
    # retail_customer_statement_reconciliation_test.py for the same trick.
    dconn = get_retail_conn()
    for _doc_type in ('sale', 'return'):
        dconn.execute("INSERT OR REPLACE INTO doc_sequences (company_id,doc_type,last_no) VALUES (?,?,?)",
                      (company_id, _doc_type, random.randint(1, 5_000_000)))
    dconn.commit()
    dconn.close()
    return client, company_id, branch_id, pid


def _customer(cid, name):
    rconn = get_retail_conn()
    customer_id = str(uuid.uuid4())
    rconn.execute(
        "INSERT INTO customers (id,company_id,name,credit_mode,credit_limit,credit_balance) "
        "VALUES (?,?,?,'unlimited',0,0)", (customer_id, cid, name))
    rconn.commit()
    rconn.close()
    return customer_id


def _supplier(cid, name):
    rconn = get_retail_conn()
    sid = str(uuid.uuid4())
    rconn.execute("INSERT INTO suppliers (id,company_id,name,credit_balance) VALUES (?,?,?,0)", (sid, cid, name))
    rconn.commit()
    rconn.close()
    return sid


def _sell(client, customer_id, pid, amount_paid, method='credit', points=0):
    body = {'items': [{'product_id': pid, 'quantity': 1}], 'customer_id': customer_id,
            'amount_paid': amount_paid, 'payment_method': method, 'idempotency_key': str(uuid.uuid4())}
    if points:
        body['points_redeemed'] = points
    r = client.post('/api/sub/retail/sales', json=body)
    assert r.status_code == 200, r.get_json()
    return r.get_json()['data']


def _signed_statement_sum(conn, cid, customer_id, eps):
    events = retail_api._statement_events(conn, cid, customer_id, eps).get(str(customer_id), [])
    total = Decimal('0')
    for e in events:
        total += Decimal(str(e['amount'] or 0)) * int(e['sign'])
    return total


# ── 1. the derivation is the statement ────────────────────────────────────────

def test_derived_balance_equals_statement_sum_and_cache_for_every_party_on_a_mixed_day():
    client, cid, _bid, pid = _make_admin(price=100.0)
    credit = _customer(cid, 'Credit')       # credit sale 100, 40 paid, then pays 25 on account
    cashy = _customer(cid, 'Cash')          # fully paid cash sale (tender must NOT count)
    pts = _customer(cid, 'Points')          # 30 points redeemed on a credit sale
    ret = _customer(cid, 'Returner')        # credit sale 100, then returns it all -> debt forgiven
    rconn = get_retail_conn()
    rconn.execute("INSERT INTO loyalty_ledger (uid,company_id,customer_id,points_delta,entry_type) VALUES (?,?,?,?,'opening')",
                  (str(uuid.uuid4()), cid, pts, 30))
    rconn.execute("INSERT OR REPLACE INTO retail_settings (company_id,skey,svalue) VALUES (?,'loyalty_point_value','1.0')", (cid,))
    rconn.commit()
    rconn.close()

    _sell(client, credit, pid, 40.0)
    r = client.post(f'/api/sub/retail/customers/{credit}/payments', json={'amount': 25})
    assert r.status_code == 200, r.get_json()
    _sell(client, cashy, pid, 100.0, method='cash')
    _sell(client, pts, pid, 0.0, points=30)
    sale = _sell(client, ret, pid, 0.0)
    rr = client.post('/api/sub/retail/returns', json={
        'sale_id': sale['id'], 'items': [{'product_id': pid, 'quantity': 1}],
        'reason': 'unit', 'idempotency_key': str(uuid.uuid4())})
    assert rr.status_code == 200, rr.get_json()

    sup_a = _supplier(cid, 'Supplier A')    # PO 200 with 50 down-payment, then 30 instalment
    sup_b = _supplier(cid, 'Supplier B')    # direct payment only -> shop is owed 12
    po = client.post('/api/sub/retail/purchase-orders', json={
        'supplier_id': sup_a, 'amount_paid': 50, 'items': [{'product_id': pid, 'quantity': 4, 'unit_cost': 50}]})
    assert po.status_code == 200, po.get_json()
    po_id = po.get_json()['data']['id']
    assert client.post(f'/api/sub/retail/purchase-orders/{po_id}/pay', json={'amount': 30}).status_code == 200
    assert client.post(f'/api/sub/retail/suppliers/{sup_b}/payments', json={'amount': 12}).status_code == 200

    conn = get_retail_conn()
    conn.row_factory = sqlite3.Row
    try:
        eps = retail_api._money_epsilon(retail_api._company_currency(conn, cid))
        cur = retail_api._company_currency(conn, cid)
        derived = pb.customer_balances(conn, cid, cur)
        for label, cust in (('credit', credit), ('cash', cashy), ('points', pts), ('returner', ret)):
            stored = Decimal(str(conn.execute("SELECT credit_balance FROM customers WHERE id=?", (cust,)).fetchone()[0]))
            stmt = _signed_statement_sum(conn, cid, cust, eps)
            assert derived[cust] == stmt, f"{label}: derived {derived[cust]} != statement {stmt}"
            assert derived[cust] == stored, f"{label}: derived {derived[cust]} != cache {stored}"
        assert derived[credit] == Decimal('35.000')
        assert derived[cashy] == Decimal('0.000')
        assert derived[pts] == Decimal('70.000')
        assert derived[ret] == Decimal('0.000')
        sd = pb.supplier_balances(conn, cid, cur)
        for sup in (sup_a, sup_b):
            stored = Decimal(str(conn.execute("SELECT credit_balance FROM suppliers WHERE id=?", (sup,)).fetchone()[0]))
            assert sd[sup] == stored, f"supplier {sup}: derived {sd[sup]} != cache {stored}"
        assert sd[sup_a] == Decimal('120.000')
        assert sd[sup_b] == Decimal('-12.000')
        # single-party form agrees with the batch form
        assert pb.customer_balance(conn, cid, credit, cur) == derived[credit]
        assert pb.supplier_balance(conn, cid, sup_a, cur) == sd[sup_a]
        # nothing drifts, so the report is empty; poisoning the cache shows up and refresh_cache repairs it
        assert pb.drift_report(conn, cid, cur) == []
        conn.execute("UPDATE customers SET credit_balance=0 WHERE id=?", (credit,))
        conn.commit()
        drift = pb.drift_report(conn, cid, cur)
        assert [d['party_id'] for d in drift] == [credit]
        assert drift[0]['stored'] == 0.0 and drift[0]['derived'] == 35.0
        assert pb.refresh_cache(conn, cid, 'customers') == 1
        conn.commit()
        assert pb.drift_report(conn, cid, cur) == []
        assert pb.refresh_cache(conn, cid, 'customers') == 0  # UPDATE only differing rows
    finally:
        conn.close()


def test_one_companys_rows_never_reach_another_companys_balance():
    client, cid, _bid, pid = _make_admin(price=100.0)
    other_client, other_cid, _b2, other_pid = _make_admin(price=100.0)
    mine = _customer(cid, 'Mine')
    _sell(client, mine, pid, 0.0)
    conn = get_retail_conn()
    try:
        assert pb.customer_balances(conn, other_cid, 'JOD') == {}
        assert pb.customer_balance(conn, cid, mine, 'JOD') == Decimal('100.000')
        # asking another company for this id is zero, never this company's debt
        assert pb.customer_balance(conn, other_cid, mine, 'JOD') == Decimal('0.000')
    finally:
        conn.close()


# ── 2. order-independent integer sum ──────────────────────────────────────────

def _mem_conn_with_payments(amounts_fils):
    conn = sqlite3.connect(':memory:')
    conn.execute("CREATE TABLE customers (id INTEGER PRIMARY KEY, company_id TEXT, credit_balance REAL)")
    conn.execute("CREATE TABLE payments (id INTEGER PRIMARY KEY, company_id TEXT, party_type TEXT, party_id TEXT, "
                 "direction TEXT, amount REAL, related_type TEXT, status TEXT)")
    conn.execute("INSERT INTO customers VALUES (1,'c',0)")
    conn.executemany("INSERT INTO payments (company_id,party_type,party_id,direction,amount,related_type,status) "
                     "VALUES ('c','customer','1','out',?,NULL,'active')",
                     [(f / 1000.0,) for f in amounts_fils])
    return conn


def test_minor_unit_sum_is_order_independent_and_equals_the_decimal_sum():
    rng = random.Random(44)
    fils = [rng.randint(1, 999_999) for _ in range(10_000)]
    exact = Decimal(sum(fils)) / Decimal(1000)
    forward = pb.customer_balance(_mem_conn_with_payments(fils), 'c', 1, 'JOD')
    shuffled = fils[:]
    rng.shuffle(shuffled)
    backward = pb.customer_balance(_mem_conn_with_payments(shuffled), 'c', 1, 'JOD')
    assert forward == backward == exact


# ── 3. the epsilon is the writers' epsilon ────────────────────────────────────

@pytest.mark.parametrize('currency', [None, 'JOD', 'USD', 'KWD', 'JPY', 'EUR', 'XXX'])
def test_epsilon_equals_retail_api_money_epsilon(currency):
    assert pb.money_epsilon(currency) == retail_api._money_epsilon(currency)
