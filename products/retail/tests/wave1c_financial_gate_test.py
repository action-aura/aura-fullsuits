"""
Aura Retail -- Wave 1C release-gate re-audit: independent financial-authority
regression suite.

This is a fresh, independently-written verification pass against the
already-shipped Wave 1B backend (commit 157521c / tag
commercial-packaging-wave1b-complete). It does NOT re-derive totals by hand
and assert against that hand math -- every assertion is against the value
the real /api/sub/retail/sales and /api/sub/retail/returns endpoints
actually returned, exercising the real backend service/gateway in-process.

Bootstrap pattern, fixtures, and helper shape are deliberately copied from
products/retail/tests/retail_financial_authority_test.py and
products/retail/tests/retail_returns_wave0_test.py (same isolated
temp-DB-per-process app instance, same per-company doc_sequence reseed
workaround for the bare-UNIQUE sale_number column).

Covers 4 gate cases:
  1. Worked example: price=100.00, discount=20%, tax=10% -> total=88.00,
     and subtotal/discount/tax breakdown is internally consistent.
  2. Server ignores a client-manipulated subtotal/tax/total in the payload
     and computes its own authoritative values from price/discount/tax.
  3. Same sale submitted twice with the same idempotency_key -> exactly one
     sale row, stock decremented exactly once.
  4. Full return, then an excessive second return (partial-then-excess and
     full-then-again) -> rejected, stock not double-restored.

Run (own subprocess, per repo convention -- see products/run_all_tests.py):
    python -m pytest products/retail/tests/wave1c_financial_gate_test.py -v
"""
import os
import shutil
import sys
import tempfile
import uuid
from pathlib import Path

import pytest

PRODUCT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = PRODUCT_DIR / 'backend'
SUITE_ROOT = PRODUCT_DIR.parent.parent
for _p in (str(SUITE_ROOT), str(BACKEND_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

DATA = Path(tempfile.mkdtemp(prefix="aura_retail_wave1c_"))
(DATA / "database" / "subsystems").mkdir(parents=True, exist_ok=True)
os.environ.update(AURA_STANDALONE="1", AURA_BUNDLE_DIR=str(BACKEND_DIR), AURA_APP_DATA=str(DATA))
os.environ.pop("AURA_DEV", None)
os.environ.pop("AURA_RETAIL_DEMO_MODE", None)

import app as _app_module  # noqa: E402

app = _app_module.init_app()
app.config["TESTING"] = True

from commercial_runtime.identity.registry_db import get_conn as registry_conn  # noqa: E402
from database.schema import get_retail_conn  # noqa: E402
from commercial_runtime.security.passwords import hash_password  # noqa: E402


def teardown_module(module):
    shutil.rmtree(DATA, ignore_errors=True)


def _make_admin_and_product(price=100.0, tax_rate=10.0, stock=50):
    email = f"g1c-{uuid.uuid4().hex[:10]}@test.local"
    password = "Gate1cPW1"
    company_id = str(uuid.uuid4())
    user_id = str(uuid.uuid4())
    conn = registry_conn()
    conn.execute(
        "INSERT INTO users (id, company_id, employee_id, email, password_hash, role, status, require_password_change) "
        "VALUES (?,?,?,?,?,?,?,0)",
        (user_id, company_id, "EMP-0001", email, hash_password(password), "admin", "active"),
    )
    conn.commit()
    conn.close()

    rconn = get_retail_conn()
    rconn.execute("INSERT INTO branches (company_id,name) VALUES (?, 'Main')", (company_id,))
    branch_id = rconn.execute(
        "SELECT id FROM branches WHERE company_id=? ORDER BY id LIMIT 1", (company_id,)
    ).fetchone()[0]
    rconn.execute(
        "INSERT INTO products (company_id,sku,name,cost_price,sell_price,tax_rate) VALUES (?, 'G1C-1','Gate Item',5,?,?)",
        (company_id, price, tax_rate),
    )
    product_id = rconn.execute(
        "SELECT id FROM products WHERE company_id=? AND sku='G1C-1'", (company_id,)
    ).fetchone()[0]
    rconn.execute(
        "INSERT INTO inventory_balances (company_id,product_id,branch_id,quantity_on_hand) VALUES (?,?,?,?)",
        (company_id, product_id, branch_id, stock),
    )
    rconn.commit()
    rconn.close()

    client = app.test_client()
    client.post("/api/auth/login", json={"email": email, "password": password})
    client.get("/api/sub/retail/settings/tax")  # ensures doc_sequences/_ensure_credit_schema has run

    # sales.sale_number carries a bare (not company-scoped) UNIQUE constraint,
    # but _next_ref()'s counter starts at 1 per company -- every test's fresh
    # company would otherwise generate "SALE-000001" as its first sale and
    # collide with every other test's first sale in this shared temp DB.
    import random as _random
    dconn = get_retail_conn()
    dconn.execute(
        "INSERT OR REPLACE INTO doc_sequences (company_id,doc_type,last_no) VALUES (?,'sale',?)",
        (company_id, _random.randint(1, 5_000_000)),
    )
    dconn.commit()
    dconn.close()

    return client, company_id, product_id, branch_id


def _sell(client, pid, quantity, discount_pct=0, extra=None):
    payload = {
        'items': [{'product_id': pid, 'quantity': quantity, 'discount_pct': discount_pct}],
        'amount_paid': 999999, 'payment_method': 'cash', 'idempotency_key': str(uuid.uuid4()),
    }
    if extra:
        payload.update(extra)
    r = client.post('/api/sub/retail/sales', json=payload)
    assert r.status_code == 200, r.get_json()
    return r.get_json()['data']


def _return(client, sale_id, pid, quantity, idem=None, reason='wave1c gate'):
    return client.post('/api/sub/retail/returns', json={
        'sale_id': sale_id,
        'items': [{'product_id': pid, 'quantity': quantity}],
        'reason': reason,
        'idempotency_key': idem or str(uuid.uuid4()),
    })


# ─── Case 1: worked example, price 100, discount 20, tax 10% -> 88.00 ─────────

def test_case1_worked_example_100_discount20_tax10_total_88():
    """(100-20)*1.10 = 88.00, and the breakdown the server returns is
    internally consistent (subtotal 100, discount 20, tax 8, total 88)."""
    client, cid, pid, bid = _make_admin_and_product(price=100.0, tax_rate=10.0)
    r = client.post('/api/sub/retail/sales', json={
        'items': [{'product_id': pid, 'quantity': 1, 'discount_pct': 20}],
        'amount_paid': 88.0, 'payment_method': 'cash', 'idempotency_key': str(uuid.uuid4()),
    })
    assert r.status_code == 200, r.get_json()
    data = r.get_json()['data']
    assert data['subtotal'] == 100.0
    assert data['discount_amount'] == 20.0
    assert data['tax_amount'] == 8.0
    assert data['total'] == 88.0
    # internal consistency: (subtotal - discount) * 1.10 == total
    assert round((data['subtotal'] - data['discount_amount']) * 1.10, 2) == data['total']


# ─── Case 2: server ignores client-manipulated subtotal/tax/total ────────────

def test_case2_server_ignores_manipulated_subtotal_tax_total():
    """Client claims subtotal=1, tax_amount=0, total=1 for a $100/20%-discount
    /10%-tax line. The server must recompute authoritatively (88.00), not
    trust the client-submitted breakdown."""
    client, cid, pid, bid = _make_admin_and_product(price=100.0, tax_rate=10.0)
    r = client.post('/api/sub/retail/sales', json={
        'items': [{'product_id': pid, 'quantity': 1, 'discount_pct': 20,
                    'unit_price': 1, 'tax_rate': 0, 'line_total': 1}],
        'subtotal': 1, 'discount_amount': 0, 'tax_amount': 0, 'total': 1,
        'amount_paid': 999999, 'payment_method': 'cash', 'idempotency_key': str(uuid.uuid4()),
    })
    assert r.status_code == 200, r.get_json()
    data = r.get_json()['data']
    assert data['subtotal'] == 100.0, "server must ignore client subtotal=1"
    assert data['tax_amount'] == 8.0, "server must ignore client tax_amount=0"
    assert data['total'] == 88.0, "server must ignore client total=1"
    assert data['total'] != 1


# ─── Case 3: duplicate idempotency key -> exactly one sale ───────────────────

def test_case3_duplicate_idempotency_key_creates_only_one_sale():
    client, cid, pid, bid = _make_admin_and_product(price=100.0, tax_rate=10.0, stock=10)
    key = str(uuid.uuid4())
    payload = {
        'items': [{'product_id': pid, 'quantity': 2}],
        'amount_paid': 220.0, 'payment_method': 'cash', 'idempotency_key': key,
    }
    r1 = client.post('/api/sub/retail/sales', json=payload)
    r2 = client.post('/api/sub/retail/sales', json=payload)
    r3 = client.post('/api/sub/retail/sales', json=payload)  # triple-submit for good measure
    assert r1.status_code == 200 and r2.status_code == 200 and r3.status_code == 200
    id1, id2, id3 = (r.get_json()['data']['id'] for r in (r1, r2, r3))
    assert id1 == id2 == id3, "all three submissions must resolve to the same sale id"

    rconn = get_retail_conn()
    sale_count = rconn.execute(
        "SELECT COUNT(*) FROM sales WHERE company_id=? AND idempotency_key=?", (cid, key)
    ).fetchone()[0]
    remaining_stock = rconn.execute(
        "SELECT quantity_on_hand FROM inventory_balances WHERE company_id=? AND product_id=? AND branch_id=?",
        (cid, pid, bid)
    ).fetchone()[0]
    rconn.close()
    assert sale_count == 1, "no duplicate sale record was created"
    assert remaining_stock == 8, "stock decremented exactly once (10 - 2), not 3x"


# ─── Case 4: full return then return-again / over-return rejected ────────────

def test_case4_full_return_then_second_return_rejected_no_double_restore():
    client, cid, pid, bid = _make_admin_and_product(price=100.0, tax_rate=10.0, stock=10)
    sale = _sell(client, pid, quantity=2)  # total = 220.0
    assert sale['total'] == 220.0

    rconn = get_retail_conn()
    stock_after_sale = rconn.execute(
        "SELECT quantity_on_hand FROM inventory_balances WHERE company_id=? AND product_id=? AND branch_id=?",
        (cid, pid, bid)
    ).fetchone()[0]
    rconn.close()
    assert stock_after_sale == 8

    r_full = _return(client, sale['id'], pid, quantity=2)
    assert r_full.status_code == 200, r_full.get_json()
    assert r_full.get_json()['data']['refund_amount'] == 220.0

    rconn = get_retail_conn()
    stock_after_return = rconn.execute(
        "SELECT quantity_on_hand FROM inventory_balances WHERE company_id=? AND product_id=? AND branch_id=?",
        (cid, pid, bid)
    ).fetchone()[0]
    rconn.close()
    assert stock_after_return == 10, "full return restored exactly the 2 units sold"

    # Attempt to return the same sale again -- nothing remains returnable.
    r_again = _return(client, sale['id'], pid, quantity=1)
    assert r_again.status_code == 400, r_again.get_json()
    assert 'remain returnable' in r_again.get_json()['message'].lower()

    # Attempt to return more than was ever sold in one shot on a fresh sale.
    sale2 = _sell(client, pid, quantity=1)
    r_excess = _return(client, sale2['id'], pid, quantity=99)
    assert r_excess.status_code == 400, r_excess.get_json()
    assert 'remain returnable' in r_excess.get_json()['message'].lower()

    rconn = get_retail_conn()
    stock_final = rconn.execute(
        "SELECT quantity_on_hand FROM inventory_balances WHERE company_id=? AND product_id=? AND branch_id=?",
        (cid, pid, bid)
    ).fetchone()[0]
    returns_count = rconn.execute(
        "SELECT COUNT(*) FROM returns WHERE company_id=? AND sale_id=?", (cid, sale['id'])
    ).fetchone()[0]
    rconn.close()
    # stock: started 10 -> sold 2 (8) -> fully returned (10) -> sold 1 for sale2 (9);
    # the rejected second/excess return attempts must not have touched stock further.
    assert stock_final == 9, "rejected returns must not double-restore or over-restore stock"
    assert returns_count == 1, "only the one successful full return exists for the first sale"
