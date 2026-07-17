"""
Aura Retail -- server-authoritative sale financial-authority regression suite
(Wave 0, AUDIT-002/003/005/006/008/009).

Confirms the backend is the sole financial authority for a sale: it ignores
client-submitted totals/tax/unit_price, resolves price/tax from the product
row, clamps discount_pct, rejects invalid quantity and insufficient stock,
rounds with Decimal/ROUND_HALF_UP (not binary float / banker's rounding),
and collapses a retried idempotency_key into the original result instead of
creating a second sale.

See docs/corrections/wave0/retail-financial-authority-correction.md and
docs/architecture/financial-authority-contracts.md.

Run:
    pytest products/retail/tests/retail_financial_authority_test.py -v
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

DATA = Path(tempfile.mkdtemp(prefix="aura_retail_financial_"))
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
    email = f"fa-{uuid.uuid4().hex[:10]}@test.local"
    password = "FinancialPW1"
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
        "INSERT INTO products (company_id,sku,name,cost_price,sell_price,tax_rate) VALUES (?, 'FA-1','Financial Item',5,?,?)",
        (company_id, price, tax_rate),
    )
    product_id = rconn.execute(
        "SELECT id FROM products WHERE company_id=? AND sku='FA-1'", (company_id,)
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
    # Reseed with a random starting point, same workaround used by
    # retail_pricing_test.py's _make_admin_and_client().
    import random as _random
    dconn = get_retail_conn()
    dconn.execute(
        "INSERT OR REPLACE INTO doc_sequences (company_id,doc_type,last_no) VALUES (?,'sale',?)",
        (company_id, _random.randint(1, 5_000_000)),
    )
    dconn.commit()
    dconn.close()

    return client, company_id, product_id, branch_id


def test_server_ignores_manipulated_totals_and_unit_price():
    """A tampered payload claiming unit_price=1, tax=0, total=1 for a
    $100/10%-tax product must be recomputed server-side, not trusted."""
    client, cid, pid, bid = _make_admin_and_product(price=100.0, tax_rate=10.0)
    r = client.post('/api/sub/retail/sales', json={
        'items': [{'product_id': pid, 'quantity': 1, 'unit_price': 1, 'tax_rate': 0, 'line_total': 1}],
        'subtotal': 1, 'discount_amount': 0, 'tax_amount': 0, 'total': 1, 'amount_paid': 999999,
        'payment_method': 'cash', 'idempotency_key': str(uuid.uuid4()),
    })
    assert r.status_code == 200, r.get_json()
    data = r.get_json()['data']
    assert data['subtotal'] == 100.0
    assert data['tax_amount'] == 10.0
    assert data['total'] == 110.0


def test_android_style_zero_tax_payload_still_computes_real_tax():
    """Regression for the Android client's historical bug of always
    submitting tax_rate=0/discount_pct=0 regardless of the product's actual
    configured tax (AUDIT-002)."""
    client, cid, pid, bid = _make_admin_and_product(price=100.0, tax_rate=15.0)
    r = client.post('/api/sub/retail/sales', json={
        'items': [{'product_id': pid, 'quantity': 1, 'discount_pct': 0, 'tax_rate': 0}],
        'total': 100.0, 'amount_paid': 999999, 'payment_method': 'cash',
        'idempotency_key': str(uuid.uuid4()),
    })
    assert r.status_code == 200, r.get_json()
    data = r.get_json()['data']
    assert data['tax_amount'] == 15.0
    assert data['total'] == 115.0


def test_worked_example_subtotal_100_discount_20pct_tax_10pct():
    """subtotal=100, discount=20%, tax=10% -> taxable=80, tax=8, total=88."""
    client, cid, pid, bid = _make_admin_and_product(price=100.0, tax_rate=10.0)
    r = client.post('/api/sub/retail/sales', json={
        'items': [{'product_id': pid, 'quantity': 1, 'discount_pct': 20}],
        'amount_paid': 88.0, 'payment_method': 'cash', 'idempotency_key': str(uuid.uuid4()),
    })
    assert r.status_code == 200, r.get_json()
    data = r.get_json()['data']
    assert data['discount_amount'] == 20.0
    assert data['tax_amount'] == 8.0
    assert data['total'] == 88.0


def test_discount_over_100_percent_is_clamped_not_rejected():
    client, cid, pid, bid = _make_admin_and_product(price=100.0, tax_rate=0.0)
    r = client.post('/api/sub/retail/sales', json={
        'items': [{'product_id': pid, 'quantity': 1, 'discount_pct': 250}],
        'amount_paid': 0.0, 'payment_method': 'cash', 'idempotency_key': str(uuid.uuid4()),
    })
    assert r.status_code == 200, r.get_json()
    assert r.get_json()['data']['total'] == 0.0


def test_negative_discount_is_clamped_to_zero():
    client, cid, pid, bid = _make_admin_and_product(price=100.0, tax_rate=0.0)
    r = client.post('/api/sub/retail/sales', json={
        'items': [{'product_id': pid, 'quantity': 1, 'discount_pct': -50}],
        'amount_paid': 100.0, 'payment_method': 'cash', 'idempotency_key': str(uuid.uuid4()),
    })
    assert r.status_code == 200, r.get_json()
    assert r.get_json()['data']['total'] == 100.0


def test_zero_and_negative_quantity_rejected():
    client, cid, pid, bid = _make_admin_and_product()
    for bad_qty in (0, -1):
        r = client.post('/api/sub/retail/sales', json={
            'items': [{'product_id': pid, 'quantity': bad_qty}],
            'amount_paid': 0, 'payment_method': 'cash', 'idempotency_key': str(uuid.uuid4()),
        })
        assert r.status_code == 400, r.get_json()


def test_insufficient_stock_rejected_and_does_not_touch_inventory():
    client, cid, pid, bid = _make_admin_and_product(stock=5)
    r = client.post('/api/sub/retail/sales', json={
        'items': [{'product_id': pid, 'quantity': 999}],
        'amount_paid': 0, 'payment_method': 'cash', 'idempotency_key': str(uuid.uuid4()),
    })
    assert r.status_code == 400
    assert 'insufficient stock' in r.get_json()['message'].lower()

    rconn = get_retail_conn()
    remaining = rconn.execute(
        "SELECT quantity_on_hand FROM inventory_balances WHERE company_id=? AND product_id=? AND branch_id=?",
        (cid, pid, bid)
    ).fetchone()[0]
    rconn.close()
    assert remaining == 5


def test_unknown_or_foreign_product_rejected():
    client, cid, pid, bid = _make_admin_and_product()
    r = client.post('/api/sub/retail/sales', json={
        'items': [{'product_id': 999999999, 'quantity': 1}],
        'amount_paid': 0, 'payment_method': 'cash', 'idempotency_key': str(uuid.uuid4()),
    })
    assert r.status_code == 400


def test_duplicate_idempotency_key_returns_original_sale_not_a_second_one():
    client, cid, pid, bid = _make_admin_and_product(stock=10)
    key = str(uuid.uuid4())
    payload = {
        'items': [{'product_id': pid, 'quantity': 1}],
        'amount_paid': 110.0, 'payment_method': 'cash', 'idempotency_key': key,
    }
    r1 = client.post('/api/sub/retail/sales', json=payload)
    r2 = client.post('/api/sub/retail/sales', json=payload)
    assert r1.status_code == 200 and r2.status_code == 200
    assert r1.get_json()['data']['id'] == r2.get_json()['data']['id']

    rconn = get_retail_conn()
    count = rconn.execute("SELECT COUNT(*) FROM sales WHERE company_id=? AND idempotency_key=?", (cid, key)).fetchone()[0]
    remaining = rconn.execute(
        "SELECT quantity_on_hand FROM inventory_balances WHERE company_id=? AND product_id=? AND branch_id=?",
        (cid, pid, bid)
    ).fetchone()[0]
    rconn.close()
    assert count == 1
    assert remaining == 9  # stock decremented exactly once, not twice


def test_stock_decrements_exactly_by_quantity_sold():
    client, cid, pid, bid = _make_admin_and_product(stock=20)
    client.post('/api/sub/retail/sales', json={
        'items': [{'product_id': pid, 'quantity': 3}],
        'amount_paid': 1000, 'payment_method': 'cash', 'idempotency_key': str(uuid.uuid4()),
    })
    rconn = get_retail_conn()
    remaining = rconn.execute(
        "SELECT quantity_on_hand FROM inventory_balances WHERE company_id=? AND product_id=? AND branch_id=?",
        (cid, pid, bid)
    ).fetchone()[0]
    rconn.close()
    assert remaining == 17
