"""Aura Retail -- JoFotara e-invoicing (docs/einvoicing/phase1/) regression
safety net.

Written BEFORE any e-invoicing feature code exists (Step 3 of this wave's
implementation plan), against the real, currently-shipping backend. Every
frozen literal below (response key lists, column lists) was captured live
from the actual running app, not hand-derived -- see this wave's baseline in
docs/einvoicing/phase1/phase1-scope-and-baseline.md.

The point of this file: as e-invoicing feature code lands in later steps,
these tests keep passing UNCHANGED as long as the feature stays at its
default (OFF). If any of these ever fail after this wave's feature code
lands, that is a real regression against a Wave1C-cleared, pilot-live
product -- not a test to "fix" by updating the frozen literal, unless the
underlying product behavior change was itself deliberate and reviewed.

Bootstrap pattern copied from
products/retail/tests/wave1c_financial_gate_test.py.

Run:
    python -m pytest products/retail/tests/retail_einvoicing_regression_test.py -v
"""
import os
import shutil
import sys
import tempfile
import threading
import uuid
from pathlib import Path

PRODUCT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = PRODUCT_DIR / 'backend'
SUITE_ROOT = PRODUCT_DIR.parent.parent
for _p in (str(SUITE_ROOT), str(BACKEND_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

DATA = Path(tempfile.mkdtemp(prefix="aura_retail_einvoice_regression_"))
(DATA / "database" / "subsystems").mkdir(parents=True, exist_ok=True)
os.environ.update(AURA_STANDALONE="1", AURA_BUNDLE_DIR=str(BACKEND_DIR), AURA_APP_DATA=str(DATA))
os.environ.pop("AURA_DEV", None)

from commercial_runtime.licensing_contracts.test_support import seed_active_license  # noqa: E402
seed_active_license(str(DATA), product_code="AURA_RETAIL", platform="WINDOWS")
os.environ.pop("AURA_RETAIL_DEMO_MODE", None)

import app as _app_module  # noqa: E402

app = _app_module.init_app()
app.config["TESTING"] = True

from commercial_runtime.identity.registry_db import get_conn as registry_conn  # noqa: E402
from database.schema import get_retail_conn  # noqa: E402
from commercial_runtime.security.passwords import hash_password  # noqa: E402


def teardown_module(module):
    shutil.rmtree(DATA, ignore_errors=True)


# ─── Frozen literals -- captured live from the real app before any
# e-invoicing code existed. Do not "fix" a failing assertion by editing these
# without confirming the underlying behavior change was deliberate. ─────────

SALE_RESPONSE_KEYS = [
    'amount_paid', 'balance_due', 'calculation_version', 'change', 'currency',
    'discount_amount', 'id', 'idempotency_key', 'lines', 'sale_number',
    'subtotal', 'tax_amount', 'total', 'warning',
]
RETURN_RESPONSE_KEYS = [
    'calculation_version', 'id', 'idempotency_key', 'items', 'refund_amount',
    'return_number',
]
DASHBOARD_DATA_KEYS = [
    'hourly_data', 'hourly_labels', 'low_stock_alerts', 'month_returns',
    'month_sales', 'month_transactions', 'payment_methods', 'recent_sales',
    'sales_change_pct', 'today_returns', 'today_sales', 'today_transactions',
    'total_customers', 'total_products', 'yesterday_sales',
]
SALES_TABLE_COLUMNS = [
    'id', 'company_id', 'sale_number', 'branch_id', 'customer_id', 'cashier',
    'subtotal', 'discount_amount', 'tax_amount', 'total', 'amount_paid',
    'change_amount', 'payment_method', 'status', 'idempotency_key', 'notes',
    'created_at',
    # feat/shift-cash-drawer (schema v10, database/schema.py's
    # _migrate_add_shift_cash_drawer): sales.session_id -- a real, expected
    # additive shape change. It lands BEFORE due_date in this list (not
    # after) because it's added by the versioned schema migration that runs
    # at app boot (init_app() -> init_retail() -> ensure_schema_version()),
    # while due_date is added later, lazily, the first time any request
    # calls _ensure_credit_schema() -- see that function's own addcol('sales',
    # 'due_date', ...) call. SQLite's PRAGMA table_info always reflects
    # physical ALTER TABLE order, not declaration/logical order.
    'session_id', 'due_date',
]
SALE_ITEMS_TABLE_COLUMNS = [
    'id', 'sale_id', 'product_id', 'quantity', 'unit_price', 'discount_pct',
    'tax_rate', 'line_total',
]
CUSTOMERS_TABLE_COLUMNS = [
    # 'status' and the created_at reorder are a real, expected shape change
    # from the multi-device sync foundation's customers.id -> UUID migration
    # (database/schema.py::_migrate_customers_to_uuid folds in a new status
    # column customers never had, plus the credit_mode/credit_limit/
    # credit_balance columns _ensure_credit_schema previously added at
    # runtime, as first-class columns on the rebuilt table) -- merged in via
    # feat/retail-mobile-build-baseline, 2026-08-10, unrelated to e-invoicing.
    'id', 'company_id', 'name', 'phone', 'email', 'address', 'loyalty_points',
    'total_spent', 'status', 'credit_mode', 'credit_limit', 'credit_balance',
    'created_at',
]
RETURNS_TABLE_COLUMNS = [
    'id', 'company_id', 'return_number', 'sale_id', 'branch_id', 'cashier',
    'reason', 'refund_method', 'refund_amount', 'status', 'created_at',
    # feat/shift-cash-drawer (schema v10): returns.session_id, same boot-time-
    # migration-vs-lazy-addcol ordering reasoning as SALES_TABLE_COLUMNS
    # above -- idempotency_key is also a lazy _ensure_credit_schema addcol.
    'session_id', 'idempotency_key',
]
BASELINE_THREAD_NAMES = {'MainThread'}


def _make_admin_and_product(price=100.0, tax_rate=10.0, stock=50):
    email = f"einv-reg-{uuid.uuid4().hex[:10]}@test.local"
    password = "EinvRegPW1"
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
    # products.id is a client-generated TEXT UUID (multi-device sync
    # foundation), not an autoincrement integer -- unlike the old schema this
    # file was originally written against, a raw INSERT that omits id gets a
    # NULL primary key, not an auto-assigned one.
    product_id = str(uuid.uuid4())
    rconn.execute(
        "INSERT INTO products (id,company_id,sku,name,cost_price,sell_price,tax_rate) VALUES (?, ?, 'ER-1','Regression Item',5,?,?)",
        (product_id, company_id, price, tax_rate),
    )
    rconn.execute(
        "INSERT INTO inventory_balances (company_id,product_id,branch_id,quantity_on_hand) VALUES (?,?,?,?)",
        (company_id, product_id, branch_id, stock),
    )
    rconn.commit()
    rconn.close()

    client = app.test_client()
    client.post("/api/auth/login", json={"email": email, "password": password})
    client.get("/api/sub/retail/settings/tax")

    import random as _random
    dconn = get_retail_conn()
    dconn.execute(
        "INSERT OR REPLACE INTO doc_sequences (company_id,doc_type,last_no) VALUES (?,'sale',?)",
        (company_id, _random.randint(1, 5_000_000)),
    )
    dconn.commit()
    dconn.close()

    return client, company_id, product_id, branch_id


def test_sale_response_keys_unchanged():
    client, cid, pid, bid = _make_admin_and_product()
    r = client.post('/api/sub/retail/sales', json={
        'items': [{'product_id': pid, 'quantity': 1}],
        'amount_paid': 999999, 'payment_method': 'cash', 'idempotency_key': str(uuid.uuid4()),
    })
    assert r.status_code == 200, r.get_json()
    assert sorted(r.get_json()['data'].keys()) == sorted(SALE_RESPONSE_KEYS)


def test_return_response_keys_unchanged():
    client, cid, pid, bid = _make_admin_and_product()
    sale = client.post('/api/sub/retail/sales', json={
        'items': [{'product_id': pid, 'quantity': 1}],
        'amount_paid': 999999, 'payment_method': 'cash', 'idempotency_key': str(uuid.uuid4()),
    }).get_json()['data']
    r = client.post('/api/sub/retail/returns', json={
        'sale_id': sale['id'],
        'items': [{'product_id': pid, 'quantity': 1}],
        'reason': 'regression check',
        'idempotency_key': str(uuid.uuid4()),
    })
    assert r.status_code == 200, r.get_json()
    assert sorted(r.get_json()['data'].keys()) == sorted(RETURN_RESPONSE_KEYS)


def test_dashboard_stats_response_keys_unchanged():
    client, cid, pid, bid = _make_admin_and_product()
    r = client.get('/api/sub/retail/dashboard/stats')
    assert r.status_code == 200
    assert sorted(r.get_json()['data'].keys()) == sorted(DASHBOARD_DATA_KEYS)


def test_no_alter_ran_on_existing_tables():
    """Proves the einvoicing migration only added new tables -- it never
    touched an existing Retail table's column set."""
    conn = get_retail_conn()
    assert [c[1] for c in conn.execute("PRAGMA table_info(sales)").fetchall()] == SALES_TABLE_COLUMNS
    assert [c[1] for c in conn.execute("PRAGMA table_info(sale_items)").fetchall()] == SALE_ITEMS_TABLE_COLUMNS
    assert [c[1] for c in conn.execute("PRAGMA table_info(customers)").fetchall()] == CUSTOMERS_TABLE_COLUMNS
    assert [c[1] for c in conn.execute("PRAGMA table_info(returns)").fetchall()] == RETURNS_TABLE_COLUMNS
    conn.close()


def test_einvoice_outbox_stays_empty_through_a_full_sale_and_return_cycle():
    """With the feature at its default (OFF, nothing has enabled it), a full
    sale + return cycle must not enqueue anything -- proves the eventual
    enqueue hook (Step 13) is correctly gated and cannot fire before the
    feature is wired at all, let alone before it's turned on."""
    client, cid, pid, bid = _make_admin_and_product(stock=10)
    sale = client.post('/api/sub/retail/sales', json={
        'items': [{'product_id': pid, 'quantity': 2}],
        'amount_paid': 999999, 'payment_method': 'cash', 'idempotency_key': str(uuid.uuid4()),
    }).get_json()['data']
    client.post('/api/sub/retail/returns', json={
        'sale_id': sale['id'],
        'items': [{'product_id': pid, 'quantity': 1}],
        'reason': 'regression check',
        'idempotency_key': str(uuid.uuid4()),
    })
    conn = get_retail_conn()
    count = conn.execute("SELECT COUNT(*) FROM einvoice_outbox").fetchone()[0]
    conn.close()
    assert count == 0


def test_no_background_thread_exists_when_feature_never_enabled():
    """No einvoicing worker thread exists anywhere in this process -- proves
    that even after the app has booted and processed real sales, nothing
    started a background job. This test is meaningful NOW (trivially true,
    no worker module exists yet) and stays meaningful after Step 11/13 land
    a real worker: it will only keep passing if that worker's startup is
    correctly gated on the feature actually being enabled."""
    client, cid, pid, bid = _make_admin_and_product()
    client.post('/api/sub/retail/sales', json={
        'items': [{'product_id': pid, 'quantity': 1}],
        'amount_paid': 999999, 'payment_method': 'cash', 'idempotency_key': str(uuid.uuid4()),
    })
    names = {t.name for t in threading.enumerate()}
    assert names.issubset(BASELINE_THREAD_NAMES | {'MainThread'}), (
        f"unexpected background thread(s) present with e-invoicing never enabled: {names - BASELINE_THREAD_NAMES}"
    )
