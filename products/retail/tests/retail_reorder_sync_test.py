"""Aura Retail -- reorder automation foundation
(feat/reorder-automation-foundation): post-sale trigger, Admin Center
accept/decline routes, and their sync_outbox wiring.

Covers exactly the pieces database/schema.py's
`_migrate_add_reorder_automation_foundation` and
core/retail/reorder_hook.py's module docstrings describe:

  - A sale that drops a product's stock to/below its `reorder_level`, on a
    product that opted in (`reorder_method != 'none'`), creates ONE pending
    `reorder_requests` row and queues it to `sync_outbox` as entity_type
    `reorder_request` -- following retail_product_sync_test.py's own
    "queues a sync_outbox row" test shape.
  - The idempotency guard: two sales that each independently drop the same
    product to/below its reorder_level must only ever produce ONE pending
    request, not two.
  - `reorder_method='none'` (the default -- opt-in, not opt-out) never
    creates anything, on any sale, ever.
  - Accept creates a purchase_order LOCALLY ONLY -- this is the single most
    safety-critical property in this whole feature (see the explicit
    non-goal in database/schema.py's migration docstring: syncing
    purchase_orders would 400-reject Owner's whole push batch on its
    non-UUID entity_id and permanently jam every other entity's sync behind
    it) -- so this file asserts NOT just that accept works, but that
    sync_outbox NEVER gains a `purchase_order` entity_type row, ever.
  - Decline marks the request declined, queues that status change, and
    creates nothing else.

This file follows the same self-contained bootstrap convention as
retail_product_sync_test.py / retail_einvoicing_regression_test.py (no
shared conftest.py exists for products/retail/tests/): its own temp
app-data dir, its own license seed, its own Flask app boot, its own
fixtures local to this file.

Run:
    pytest products/retail/tests/retail_reorder_sync_test.py -v
"""
import json
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

DATA = Path(tempfile.mkdtemp(prefix="aura_retail_reordersync_"))
(DATA / "database" / "subsystems").mkdir(parents=True, exist_ok=True)
os.environ.update(AURA_STANDALONE="1", AURA_BUNDLE_DIR=str(BACKEND_DIR), AURA_APP_DATA=str(DATA))
os.environ.pop("AURA_DEV", None)

from commercial_runtime.licensing_contracts.test_support import seed_active_license  # noqa: E402

# Seeded BEFORE the app is built (creating a product / accepting-declining a
# reorder request are all capability-guarded) -- matches every other
# route-level test file in this suite.
seed_active_license(str(DATA), product_code="AURA_RETAIL", platform="WINDOWS")

import app as _app_module  # noqa: E402

app = _app_module.init_app()
app.config["TESTING"] = True

from commercial_runtime.identity.registry_db import get_conn as registry_conn  # noqa: E402
from commercial_runtime.security.passwords import hash_password  # noqa: E402
from database.schema import get_retail_conn  # noqa: E402


def teardown_module(module):
    shutil.rmtree(DATA, ignore_errors=True)


@pytest.fixture
def client():
    """A fresh, logged-in Flask test client for its own newly-created
    company -- one company per test, so reorder_requests/sync_outbox
    assertions never see another test's rows."""
    email = f'reordersync-admin-{uuid.uuid4().hex[:8]}@test.local'
    password = 'ReorderSyncPW1'
    company_id = str(uuid.uuid4())
    conn = registry_conn()
    conn.execute(
        "INSERT INTO users (id, company_id, employee_id, email, password_hash, role, status, require_password_change) "
        "VALUES (?,?,?,?,?,?,?,0)",
        (str(uuid.uuid4()), company_id, 'EMP-0001', email, hash_password(password), 'admin', 'active'),
    )
    conn.commit()
    conn.close()

    c = app.test_client()
    r = c.post('/api/auth/login', json={'email': email, 'password': password})
    assert r.status_code == 200, r.get_json()

    # `doc_sequences` (like `retail_settings`/`payment_methods`) is created
    # LAZILY by _ensure_credit_schema on first use, not at app boot -- a
    # harmless GET primes it, same as
    # retail_einvoicing_regression_test.py's `_make_admin_and_product` does
    # via its own `client.get('/api/sub/retail/settings/tax')` call before
    # touching doc_sequences directly.
    c.get('/api/sub/retail/settings/tax')

    # purchase_orders.po_number carries a BARE (not company-scoped) UNIQUE
    # constraint (database/schema.py's init_retail) -- a PRE-EXISTING
    # limitation of create_purchase_order's _next_ref(conn, cid, 'po') call
    # that this file's accept route reuses verbatim (same call shape, not a
    # new bug this feature introduces). Every fresh company's doc_sequences
    # counter starts at 0, so two different companies' FIRST purchase order
    # would both compute "PO-000001" and collide across this shared,
    # multi-company test database -- exactly the same hazard
    # retail_einvoicing_regression_test.py's `_make_admin_and_product`
    # already works around for `sales.sale_number`, using the identical
    # random-seed technique here.
    import random as _random
    from database.schema import get_retail_conn as _get_retail_conn_for_seed
    seed_conn = _get_retail_conn_for_seed()
    seed_conn.execute(
        "INSERT OR REPLACE INTO doc_sequences (company_id,doc_type,last_no) VALUES (?,'po',?)",
        (company_id, _random.randint(1, 5_000_000)),
    )
    seed_conn.commit()
    seed_conn.close()

    return c


@pytest.fixture
def db_conn():
    conn = get_retail_conn()
    yield conn
    conn.close()


def _make_product(client, *, reorder_level=5, reorder_method='none', initial_stock=10, sell_price=10.0):
    resp = client.post('/api/sub/retail/products', json={
        'name': f'Reorder Test Widget {uuid.uuid4().hex[:6]}',
        'sku': f'REORD-{uuid.uuid4().hex[:8]}',
        'cost_price': 2, 'sell_price': sell_price,
        'reorder_level': reorder_level, 'reorder_method': reorder_method,
        'initial_stock': initial_stock,
    })
    assert resp.status_code == 200, resp.get_json()
    return resp.get_json()['data']['id']


def _sell(client, product_id, qty):
    r = client.post('/api/sub/retail/sales', json={
        'items': [{'product_id': product_id, 'quantity': qty}],
        'amount_paid': 999999, 'payment_method': 'cash', 'idempotency_key': str(uuid.uuid4()),
    })
    assert r.status_code == 200, r.get_json()
    return r.get_json()['data']


def _reorder_outbox_rows(db_conn, entity_id=None):
    q = "SELECT * FROM sync_outbox WHERE entity_type='reorder_request'"
    params = []
    if entity_id:
        q += " AND entity_id=?"
        params.append(entity_id)
    return [dict(r) for r in db_conn.execute(q, params).fetchall()]


# ── Post-sale trigger ─────────────────────────────────────────────────────

def test_low_stock_sale_creates_a_pending_reorder_request_and_queues_sync_event(client, db_conn):
    pid = _make_product(client, reorder_level=5, reorder_method='whatsapp', initial_stock=6)
    _sell(client, pid, 2)  # 6 - 2 = 4, at/below reorder_level=5 -> should trigger

    rows = db_conn.execute("SELECT * FROM reorder_requests WHERE product_id=?", (pid,)).fetchall()
    assert len(rows) == 1
    assert rows[0]['status'] == 'pending'
    assert isinstance(rows[0]['id'], str) and len(rows[0]['id']) == 36  # real UUID, not autoincrement

    outbox = _reorder_outbox_rows(db_conn, entity_id=rows[0]['id'])
    assert len(outbox) == 1
    assert outbox[0]['event_type'] == 'create'
    payload = json.loads(outbox[0]['payload'])
    assert payload['id'] == rows[0]['id']
    assert payload['product_id'] == pid
    assert payload['status'] == 'pending'
    assert 'company_id' not in payload  # never on the wire -- same precedent as product/category events


def test_sale_that_stays_above_reorder_level_never_creates_a_request(client, db_conn):
    pid = _make_product(client, reorder_level=5, reorder_method='whatsapp', initial_stock=20)
    _sell(client, pid, 2)  # 20 - 2 = 18, well above reorder_level=5

    rows = db_conn.execute("SELECT * FROM reorder_requests WHERE product_id=?", (pid,)).fetchall()
    assert rows == []


def test_reorder_method_none_never_creates_a_request_even_when_stock_crosses_the_threshold(client, db_conn):
    pid = _make_product(client, reorder_level=5, reorder_method='none', initial_stock=6)
    _sell(client, pid, 2)  # 6 - 2 = 4, at/below reorder_level=5, but opted OUT

    rows = db_conn.execute("SELECT * FROM reorder_requests WHERE product_id=?", (pid,)).fetchall()
    assert rows == []


def test_two_low_stock_sales_back_to_back_only_create_one_pending_request(client, db_conn):
    """The idempotency guard, exercised end to end through two real sales
    (not a direct call into core/retail/reorder_hook.py) -- the second sale
    independently satisfies the SAME trigger condition (stock still at/below
    reorder_level after it), and must be a no-op against the existing
    pending request, not a second row."""
    pid = _make_product(client, reorder_level=5, reorder_method='whatsapp', initial_stock=10)
    _sell(client, pid, 6)  # 10 - 6 = 4 <= 5 -> creates the first (and only) request
    _sell(client, pid, 1)  # 4 - 1 = 3 <= 5 -> must NOT create a second one

    rows = db_conn.execute("SELECT * FROM reorder_requests WHERE product_id=?", (pid,)).fetchall()
    assert len(rows) == 1
    assert rows[0]['status'] == 'pending'

    # Scoped to THIS test's request id -- the shared sync_outbox table
    # accumulates rows from every test in this module (no per-test reset,
    # same convention as every other file in this suite), so an unscoped
    # COUNT(*) would be polluted by earlier tests' own reorder_request
    # events.
    outbox = _reorder_outbox_rows(db_conn, entity_id=rows[0]['id'])
    assert len(outbox) == 1  # only the first sale's create event was ever queued


def test_a_declined_request_frees_the_product_for_a_future_reorder_request(client, db_conn):
    """The idempotency guard only blocks OPEN (pending/accepted) requests --
    once declined, a later sale dropping the same product below its
    reorder_level again must be free to open a new one."""
    pid = _make_product(client, reorder_level=5, reorder_method='whatsapp', initial_stock=10)
    _sell(client, pid, 6)  # 10 - 6 = 4 <= 5 -> first request

    first = db_conn.execute("SELECT id FROM reorder_requests WHERE product_id=?", (pid,)).fetchone()
    r = client.post(f'/api/sub/retail/reorder-requests/{first["id"]}/decline')
    assert r.status_code == 200, r.get_json()

    _sell(client, pid, 1)  # 4 - 1 = 3 <= 5, and the only open slot was just freed

    rows = db_conn.execute("SELECT status FROM reorder_requests WHERE product_id=?", (pid,)).fetchall()
    assert sorted(row['status'] for row in rows) == ['declined', 'pending']


# ── Admin Center: accept / decline ───────────────────────────────────────

def test_accept_creates_a_local_only_purchase_order_and_never_queues_it_to_sync(client, db_conn):
    """The single most safety-critical property in this feature -- see this
    file's module docstring and database/schema.py's migration docstring
    for exactly why purchase_orders must never reach sync_outbox."""
    pid = _make_product(client, reorder_level=5, reorder_method='whatsapp', initial_stock=6)
    _sell(client, pid, 2)  # triggers the pending request
    req = db_conn.execute("SELECT id FROM reorder_requests WHERE product_id=?", (pid,)).fetchone()

    po_count_before = db_conn.execute("SELECT COUNT(*) c FROM purchase_orders").fetchone()['c']

    r = client.post(f'/api/sub/retail/reorder-requests/{req["id"]}/accept')
    assert r.status_code == 200, r.get_json()
    po_id = r.get_json()['data']['purchase_order_id']

    po_count_after = db_conn.execute("SELECT COUNT(*) c FROM purchase_orders").fetchone()['c']
    assert po_count_after == po_count_before + 1

    po = db_conn.execute("SELECT * FROM purchase_orders WHERE id=?", (po_id,)).fetchone()
    assert po is not None
    assert po['status'] == 'pending'

    reorder_row = db_conn.execute("SELECT status, resolved_at FROM reorder_requests WHERE id=?", (req['id'],)).fetchone()
    assert reorder_row['status'] == 'accepted'
    assert reorder_row['resolved_at'] is not None

    # The critical assertion: NOT ONE row in sync_outbox is ever a
    # purchase_order entity, even though a real PO was just created.
    po_outbox_rows = db_conn.execute(
        "SELECT * FROM sync_outbox WHERE entity_type='purchase_order'"
    ).fetchall()
    assert po_outbox_rows == []

    # The reorder_request's own status change, by contrast, IS queued.
    outbox = _reorder_outbox_rows(db_conn, entity_id=req['id'])
    update_events = [o for o in outbox if o['event_type'] == 'update']
    assert len(update_events) == 1
    payload = json.loads(update_events[0]['payload'])
    assert payload['status'] == 'accepted'


def test_accept_on_an_already_resolved_request_is_rejected_not_double_applied(client, db_conn):
    pid = _make_product(client, reorder_level=5, reorder_method='whatsapp', initial_stock=6)
    _sell(client, pid, 2)
    req = db_conn.execute("SELECT id FROM reorder_requests WHERE product_id=?", (pid,)).fetchone()

    first = client.post(f'/api/sub/retail/reorder-requests/{req["id"]}/accept')
    assert first.status_code == 200

    second = client.post(f'/api/sub/retail/reorder-requests/{req["id"]}/accept')
    assert second.status_code == 409

    # Scoped to THIS test's product -- purchase_order_items is shared across
    # the whole test module (no per-test reset), so an unscoped COUNT(*)
    # over purchase_orders would be polluted by earlier tests' own POs.
    po_item_count = db_conn.execute(
        "SELECT COUNT(*) c FROM purchase_order_items WHERE product_id=?", (pid,)
    ).fetchone()['c']
    assert po_item_count == 1  # the second (rejected) accept never created a second PO


def test_decline_marks_declined_and_queues_sync_event_and_creates_no_purchase_order(client, db_conn):
    pid = _make_product(client, reorder_level=5, reorder_method='whatsapp', initial_stock=6)
    _sell(client, pid, 2)
    req = db_conn.execute("SELECT id FROM reorder_requests WHERE product_id=?", (pid,)).fetchone()

    po_count_before = db_conn.execute("SELECT COUNT(*) c FROM purchase_orders").fetchone()['c']

    r = client.post(f'/api/sub/retail/reorder-requests/{req["id"]}/decline')
    assert r.status_code == 200, r.get_json()

    po_count_after = db_conn.execute("SELECT COUNT(*) c FROM purchase_orders").fetchone()['c']
    assert po_count_after == po_count_before  # decline creates nothing

    reorder_row = db_conn.execute("SELECT status, resolved_at FROM reorder_requests WHERE id=?", (req['id'],)).fetchone()
    assert reorder_row['status'] == 'declined'
    assert reorder_row['resolved_at'] is not None

    outbox = _reorder_outbox_rows(db_conn, entity_id=req['id'])
    update_events = [o for o in outbox if o['event_type'] == 'update']
    assert len(update_events) == 1
    assert json.loads(update_events[0]['payload'])['status'] == 'declined'


# ── List route ────────────────────────────────────────────────────────────

def test_list_reorder_requests_only_returns_pending_ones_for_this_company(client, db_conn):
    pid_pending = _make_product(client, reorder_level=5, reorder_method='whatsapp', initial_stock=6)
    _sell(client, pid_pending, 2)
    pid_resolved = _make_product(client, reorder_level=5, reorder_method='whatsapp', initial_stock=6)
    _sell(client, pid_resolved, 2)
    resolved_req = db_conn.execute(
        "SELECT id FROM reorder_requests WHERE product_id=?", (pid_resolved,)
    ).fetchone()
    client.post(f'/api/sub/retail/reorder-requests/{resolved_req["id"]}/decline')

    r = client.get('/api/sub/retail/reorder-requests')
    assert r.status_code == 200
    data = r.get_json()['data']
    product_ids = [row['product_id'] for row in data]
    assert pid_pending in product_ids
    assert pid_resolved not in product_ids  # declined -- no longer pending
