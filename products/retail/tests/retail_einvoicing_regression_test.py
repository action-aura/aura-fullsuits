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
#
# `oversold_past_recorded_stock` added launch-readiness Phase 7 stage
# 7d-iii (docs/launch-readiness/phase7-offline-ux.md "Correction to
# Decision 1") -- confirmed deliberate: create_sale now always reports
# whether a sale was allowed past its recorded on-hand figure. E-invoicing
# still only conditionally adds its own 'einvoice' key; that claim is
# unaffected.

# AUDIT (2026-09-08): e-invoicing now defaults ON (settings.py
# DEFAULTS['enabled']='1' -- the Jordanian mandate means a fresh company
# records the obligation without anyone touching a toggle). A sale response
# now carries an additional 'einvoice' key ({'invoice_ref':..., 'status':
# 'queued'}) whenever the enqueue succeeds -- see retail_api.py::create_sale's
# own comment on that block. This is a deliberate, reviewed shape change,
# not a regression to "fix" back to the old list: verified the three actual
# consumers of this response tolerate an unknown extra key before updating
# this frozen literal --
#   1. Desktop (products/retail/frontend/subsystem-retail.js): _showReceipt/
#      _einvoiceReceiptBlock already access saleData.einvoice by name and
#      explicitly no-op when it's absent -- built for exactly this
#      conditional-key shape, never a strict destructure or key-set check.
#   2. Android (android/aura-retail/.../net/Models.kt's SaleResult data
#      class + ApiClient.kt's Retrofit GsonConverterFactory): Gson silently
#      ignores JSON fields with no matching property -- SaleResult has no
#      `einvoice` field, so it is dropped on parse, not an error. (The
#      "non-lenient GsonConverterFactory throws" comment elsewhere in
#      Models.kt is about TYPE MISMATCHES, e.g. a UUID string into an Int
#      field -- unrelated to an extra, unmapped key.)
#   3. commercial_runtime/sync/: replicates DB ROWS (sync_outbox/sync_cursor
#      against `sales` etc.), never this HTTP response body -- unaffected
#      by any change to what POST /sales returns.
SALE_RESPONSE_KEYS = [
    'amount_paid', 'balance_due', 'calculation_version', 'change', 'currency',
    'discount_amount', 'einvoice', 'id', 'idempotency_key', 'lines', 'oversold_past_recorded_stock',
    'sale_number', 'subtotal', 'tax_amount', 'total', 'warning',
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
    'session_id',
    # launch-readiness Phase 2 (schema v13, database/schema.py's
    # _migrate_add_identity_and_attribution_columns): the wire identity plus
    # the actor/terminal/UTC-clock triple. Same "real, expected additive
    # shape change" category as session_id above, and it lands in the same
    # position in the list for the same reason -- it is applied by the
    # boot-time versioned migration, so physically before due_date's lazy
    # _ensure_credit_schema addcol. v13 is the first migration to ALTER
    # `sales` at all; the claim this test makes -- that the E-INVOICING
    # migration (v7) added only new tables -- is untouched by it.
    'uid', 'actor_user_uid', 'terminal_id', 'created_at_utc',
    'due_date',
]
SALE_ITEMS_TABLE_COLUMNS = [
    'id', 'sale_id', 'product_id', 'quantity', 'unit_price', 'discount_pct',
    'tax_rate', 'line_total',
    # schema v13: sale_items is one of the seven tables given a wire
    # identity, so a relayed line item can be named by something other than
    # this install's private autoincrement id.
    'uid',
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
    # launch-readiness Phase 2 (schema v13): customers is one of the four
    # catalogue tables given the reject-stale marker and soft tombstone --
    # the same triple registry v3 put on `users`. No uid here: catalogue
    # tables already carry a client-generated UUID primary key (see
    # _migrate_customers_to_uuid), so their `id` IS already wire-safe.
    'row_version', 'updated_at_utc', 'deleted_at_utc',
]
RETURNS_TABLE_COLUMNS = [
    'id', 'company_id', 'return_number', 'sale_id', 'branch_id', 'cashier',
    'reason', 'refund_method', 'refund_amount', 'status', 'created_at',
    # feat/shift-cash-drawer (schema v10): returns.session_id, same boot-time-
    # migration-vs-lazy-addcol ordering reasoning as SALES_TABLE_COLUMNS
    # above -- idempotency_key is also a lazy _ensure_credit_schema addcol.
    'session_id',
    # schema v13, same four columns and same ordering reasoning as
    # SALES_TABLE_COLUMNS above.
    'uid', 'actor_user_uid', 'terminal_id', 'created_at_utc',
    'idempotency_key',
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


def test_einvoice_outbox_gets_exactly_one_row_for_the_sale_not_the_return():
    """AUDIT (2026-09-08): renamed from
    test_einvoice_outbox_stays_empty_through_a_full_sale_and_return_cycle.
    That claim described the OLD default (OFF) and is now false by design
    -- e-invoicing defaults ON, so the sale in this same cycle DOES enqueue.
    What's still true, and still worth a regression test: a return never
    enqueues anything of its own -- Retail has no credit-note/e-invoice
    handling for returns at all (grep core/retail/einvoice_adapter.py and
    retail_api.py's create_return: neither references an enqueue call), so
    exactly ONE outbox row -- the sale's -- must exist after a sale+return
    cycle, not two and not zero.

    Scoped to THIS test's own company_id: einvoice_outbox has no company
    filter of its own, and other tests in this same module share one
    physical db across the whole process -- with the feature defaulting ON,
    every other test's own sale also adds a row to this same table, so an
    unscoped COUNT(*) here would assert on cross-test leakage, not on this
    test's own behavior (it happened to read 3 before this fix, which was
    never a stable number)."""
    client, cid, pid, bid = _make_admin_and_product(stock=10)
    sale = client.post('/api/sub/retail/sales', json={
        'items': [{'product_id': pid, 'quantity': 2}],
        'amount_paid': 999999, 'payment_method': 'cash', 'idempotency_key': str(uuid.uuid4()),
    }).get_json()['data']
    assert 'einvoice' in sale, "the sale itself must have enqueued -- e-invoicing defaults ON"
    client.post('/api/sub/retail/returns', json={
        'sale_id': sale['id'],
        'items': [{'product_id': pid, 'quantity': 1}],
        'reason': 'regression check',
        'idempotency_key': str(uuid.uuid4()),
    })
    conn = get_retail_conn()
    rows = conn.execute(
        "SELECT source_type, source_id FROM einvoice_outbox WHERE company_id=?", (cid,)
    ).fetchall()
    conn.close()
    assert len(rows) == 1, "a return must never enqueue its own e-invoice/credit-note row (not implemented in Phase 1)"
    assert rows[0][0] == 'sale' and rows[0][1] == sale['id'], "the one row must be the sale itself, not the return"


def test_no_worker_thread_starts_for_a_company_with_no_enqueued_rows_at_boot():
    """Renamed and re-scoped 2026-09-08. This was
    test_no_background_thread_exists_when_feature_never_enabled, and BOTH
    halves of that name were false by the time anyone read it:

      * "never enabled" -- e-invoicing defaults ON
        (commercial_runtime/einvoicing/settings.py DEFAULTS 'enabled': '1'),
        which is why the test above this one asserts `'einvoice' in sale`.
        Every company in this process has the feature on.
      * the old docstring's "trivially true, no worker module exists yet" --
        app.py:248 defines _get_or_create_einvoicing_worker and app.py:944
        starts real OutboxWorkers through _resume_einvoicing_workers().

    So the failure message was a statement about a fixture that does not
    exist, and the obvious next move -- starting the worker on enqueue --
    would have turned it red for doing the right thing.

    What is actually pinned here, and it is worth pinning: the boot sweep,
    _resume_einvoicing_workers() at app.py:952. It runs once at init_app()
    (import time, top of this file), selects companies with an explicit
    enabled='1' row UNIONed with companies that already have
    einvoice_outbox rows, and this process's database was empty at that
    moment -- so zero threads. The sale below then enqueues a real row and
    starts nothing, because enqueue_sale (retail_api.py:4816) writes the
    outbox and does not touch the worker registry.

    That is deliberate, not an oversight: the worker for a company enabled
    only by the default resumes at the NEXT boot, which is what the AUDIT
    note in _resume_einvoicing_workers' own docstring is about. If someone
    later starts the worker on enqueue instead, this test goes red and the
    right response is to update it, not to work around it -- the message
    below says so.

    Scope, stated plainly because threading.enumerate() is process-global
    while the fixture is company-scoped: this proves a fact about the whole
    process, not about `cid`. What it can no longer catch -- and never
    could -- is a worker started for ONE company while another company's
    stayed off."""
    client, cid, pid, bid = _make_admin_and_product()
    client.post('/api/sub/retail/sales', json={
        'items': [{'product_id': pid, 'quantity': 1}],
        'amount_paid': 999999, 'payment_method': 'cash', 'idempotency_key': str(uuid.uuid4()),
    })

    # Anti-vacuity. Without this the assertion below is satisfied just as
    # well by a sale that enqueued NOTHING -- which is precisely the state
    # the old version of this test silently measured, and the first failure
    # shape in ENGINEERING.md section 1.
    conn = get_retail_conn()
    queued = conn.execute(
        "SELECT COUNT(*) c FROM einvoice_outbox WHERE company_id=?", (cid,)
    ).fetchone()['c']
    conn.close()
    assert queued == 1, (
        f"fixture is not exercising the thing under test: {queued} outbox row(s) "
        "for this company. This check must run against a sale that really did "
        "enqueue, or 'no worker started' proves nothing."
    )

    names = {t.name for t in threading.enumerate()}
    assert names.issubset(BASELINE_THREAD_NAMES | {'MainThread'}), (
        "an e-invoicing worker thread is running in a process whose boot sweep "
        "(_resume_einvoicing_workers, app.py:952) found an empty outbox: "
        f"{names - BASELINE_THREAD_NAMES}. Either the boot sweep's gate "
        "regressed, or enqueueing now starts a worker -- if the latter is "
        "deliberate, rewrite this test to pin the new startup rule; do not "
        "widen BASELINE_THREAD_NAMES."
    )
