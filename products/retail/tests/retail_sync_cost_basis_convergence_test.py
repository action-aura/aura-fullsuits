"""Aura Retail -- schema v39 cost-basis + loyalty-sync RECEIVE-side proof
(commercial_runtime/sync/sync_service.py's `_apply_event`).

Root cause this file exists to close: schema v39 (`products.avg_cost`,
`sale_items.unit_cost`) and a pre-existing loyalty gap (`sales.points_
redeemed_amount`) were both put on the wire correctly by retail_api.py --
proved by retail_sync_payload_coverage_test.py's 18-test ratchet -- but the
RECEIVE side (`_apply_event`'s product/sale/sale_item branches) never named
any of the three columns in its INSERT column list, its bound VALUES tuple,
or (for `avg_cost`, the one column with an update path at all) the delta-
gated column list `_delta_set_clause` reads. A column absent from that list
is invisible to the generated SQL by design -- see `_delta_set_clause`'s own
docstring ("code-owned and is the ONLY source of the column names that reach
the SQL string") -- so a peer device silently never converged on cost basis
or a redeemed sale's true tender, no matter what the sender put on the wire.

retail_sync_payload_coverage_test.py proves the payload DICT carries every
column. It does not, and cannot, prove a value survives a full push-then-
pull round trip into a SECOND device's own database -- that is what
"payload coverage" leaves uncovered, and what this file exists to close.
Duplicating that ratchet here would prove nothing new; every test below
instead reads the value back from a REAL second SQLite database after a
REAL SyncService.pull_once().

Harness: the exact two-install shape retail_money_sync_test.py and
retail_changed_field_delta_test.py already established (see either file's
own module docstring for the full "AURA_APP_DATA trap" / "Postgres trap"
reasoning, not repeated here) -- install A is a real, fully-booted Flask
app; install B is a bare temp SQLite database built straight from
`database.schema.init_retail()`, never a second Flask app; `InMemoryRelay`
stands in for Owner's real Postgres-backed relay. Product/PO/sale/customer/
loyalty helpers below follow retail_gl_cogs_test.py's own helper shapes
(same request bodies, same routes) since that file already proved this
exact HTTP surface produces a real, nonzero cost basis and a real redeemed
sale -- this file adds the SECOND device and proves the value survives the
wire.

`test_product_avg_cost_converges_for_a_device_that_has_never_seen_the_row`
below is deliberately NOT a plain `service_a.push_once()` -- it hand-pushes
ONLY the `product`/`update` event straight from A's own outbox, skipping the
earlier `product`/`create` event entirely. That isolates the INSERT half of
`_apply_event`'s product branch specifically: with the create event skipped,
B has never seen this id at all, so the ON CONFLICT branch never fires and
the plain INSERT is what has to carry `avg_cost` -- exactly the bug's other
half, and the one a `push_once()`-then-`pull_once()` round trip (which
would apply create-then-update in the same batch, exercising the DELTA path
for the observable value) would never isolate.

CRITICAL: exactly ONE pytest process per file (AUDIT-010) -- these test
files pollute each other in a shared pytest process; see products/
run_all_tests.py.

Run:
    pytest products/retail/tests/retail_sync_cost_basis_convergence_test.py -v
"""
import json
import os
import shutil
import sqlite3
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

DATA = Path(tempfile.mkdtemp(prefix="aura_retail_costbasissync_"))
(DATA / "database" / "subsystems").mkdir(parents=True, exist_ok=True)
os.environ.update(AURA_STANDALONE="1", AURA_BUNDLE_DIR=str(BACKEND_DIR), AURA_APP_DATA=str(DATA))
os.environ.pop("AURA_DEV", None)

from commercial_runtime.licensing_contracts.test_support import seed_active_license  # noqa: E402

seed_active_license(str(DATA), product_code="AURA_RETAIL", platform="WINDOWS")

import app as _app_module  # noqa: E402

app = _app_module.init_app()  # install A: a real, fully-booted Flask app
app.config["TESTING"] = True

from api.retail_api import _ensure_credit_schema  # noqa: E402
from commercial_runtime.identity.registry_db import get_conn as registry_conn  # noqa: E402
from commercial_runtime.security.passwords import hash_password  # noqa: E402
from commercial_runtime.sync.sync_service import SyncService  # noqa: E402
import database.schema as schema  # noqa: E402
from database.schema import get_retail_conn  # noqa: E402

API = '/api/sub/retail'


def teardown_module(module):
    shutil.rmtree(DATA, ignore_errors=True)


# ── Install A: the real Flask app + its own real database ──────────────────

def _new_admin():
    """A fresh company + logged-in admin client. Plain function, not a
    fixture -- matches retail_money_sync_test.py's own `_new_shop` (this
    file's tests need different combinations of product/supplier/customer
    per test, so seeding is composed inline rather than forced through one
    fixture shape)."""
    email = f'costbasis-admin-{uuid.uuid4().hex[:8]}@test.local'
    password = 'CostBasisSyncPW1'
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
    return c, company_id


def _create_product(c, **kw):
    # initial_stock defaults to 0, matching retail_gl_cogs_test.py's own
    # `_seed_product` -- every avg_cost assertion below hand-computes its
    # expected weighted average from a known starting `qty_on_hand`, so a
    # nonzero opening balance this helper did not put there would silently
    # change the numbers.
    payload = {'name': 'Cost Basis Widget', 'sku': f'CBS-{uuid.uuid4().hex[:8]}', 'cost_price': 1.0,
               'sell_price': 10.0, 'tax_rate': 0, 'initial_stock': 0}
    payload.update(kw)
    r = c.post(f'{API}/products', json=payload)
    assert r.status_code == 200, r.get_json()
    return r.get_json()['data']['id']


def _create_supplier(c):
    r = c.post(f'{API}/suppliers', json={'name': f'CBS Supplier {uuid.uuid4().hex[:6]}'})
    assert r.status_code == 200, r.get_json()
    return r.get_json()['data']['id']


def _create_customer(c, name='CBS Customer'):
    r = c.post(f'{API}/customers', json={'name': f'{name} {uuid.uuid4().hex[:6]}'})
    assert r.status_code == 200, r.get_json()
    return r.get_json()['data']['id']


def _create_po(c, supplier_id, pid, qty, unit_cost, amount_paid=0):
    r = c.post(f'{API}/purchase-orders', json={
        'supplier_id': supplier_id,
        'items': [{'product_id': pid, 'quantity': qty, 'unit_cost': unit_cost}],
        'amount_paid': amount_paid})
    assert r.status_code == 200, r.get_json()
    return r.get_json()['data']['id']


def _receive_po(c, po_id):
    r = c.post(f'{API}/purchase-orders/{po_id}/receive')
    assert r.status_code == 200, r.get_json()


def _po_receipt(c, supplier_id, pid, qty, unit_cost):
    """Create + receive in one call -- `products.avg_cost` is maintained
    ONLY by receive_purchase_order (see that route's own moving-average
    comment); create_purchase_order alone never moves it. With qty_on_hand
    at 0 (this file's products always start there), the weighted-average
    formula collapses to exactly `unit_cost` -- "the first receipt simply
    sets the average to the received cost" -- so callers can assert an
    exact expected avg_cost with no hand-rolled averaging."""
    po_id = _create_po(c, supplier_id, pid, qty, unit_cost)
    _receive_po(c, po_id)


def _sell(c, pid, qty, customer_id=None, points_redeemed=None):
    payload = {'items': [{'product_id': pid, 'quantity': qty}],
               'payment_method': 'cash', 'idempotency_key': str(uuid.uuid4())}
    if customer_id is not None:
        payload['customer_id'] = customer_id
    if points_redeemed is not None:
        payload['points_redeemed'] = points_redeemed
    r = c.post(f'{API}/sales', json=payload)
    assert r.status_code == 200, r.get_json()
    return r.get_json()['data']


def _set_loyalty_point_value(c, value):
    r = c.post(f'{API}/settings/credit', json={'loyalty_point_value': value})
    assert r.status_code == 200, r.get_json()


def _grant_ledger_points(company_id, customer_id, points):
    conn = get_retail_conn()
    conn.execute(
        "INSERT INTO loyalty_ledger (uid,company_id,customer_id,points_delta,entry_type,created_by) "
        "VALUES (?,?,?,?,?,?)",
        (str(uuid.uuid4()), company_id, customer_id, points, 'adjust', 'test-fixture'))
    conn.commit()
    conn.close()


def _single_outbox_event(entity_type, event_type):
    """Reads exactly ONE matching row straight from install A's own
    sync_outbox, in the wire shape `read_outbox()`/relay.push() both use
    (id/entity_type/entity_id/event_type/payload/created_at) -- used only by
    the insert-path test below, which deliberately pushes a single hand-
    picked event rather than draining the whole outbox via push_once()."""
    conn = get_retail_conn()
    row = conn.execute(
        "SELECT id, entity_type, entity_id, event_type, payload, created_at FROM sync_outbox "
        "WHERE entity_type=? AND event_type=? ORDER BY rowid", (entity_type, event_type)
    ).fetchone()
    conn.close()
    assert row is not None, f"no {entity_type}/{event_type} event was queued"
    return {
        "id": row["id"], "entity_type": row["entity_type"], "entity_id": row["entity_id"],
        "event_type": row["event_type"], "payload": json.loads(row["payload"]),
        "created_at": row["created_at"],
    }


# ── The relay double (identical to retail_money_sync_test.py's own) ────────

class InMemoryRelay:
    def __init__(self):
        self.events = []
        self._next_seq = 1

    def push(self, events):
        for ev in events:
            self.events.append(dict(ev, seq=self._next_seq))
            self._next_seq += 1
        return {"stored": len(events), "received": len(events)}

    def pull(self, since):
        pending = [ev for ev in self.events if ev["seq"] > since]
        cursor = pending[-1]["seq"] if pending else since
        return {"events": pending, "cursor": cursor}


@pytest.fixture
def relay():
    return InMemoryRelay()


# ── Install B: a bare temp SQLite DB, no second Flask app ──────────────────

@pytest.fixture
def install_b(tmp_path):
    """Identical technique to retail_money_sync_test.py's own fixture -- see
    that file's module docstring for the full reasoning. Repeated here
    rather than imported, matching this project's own "each test file
    survives the others being deleted" convention (already used between
    retail_money_sync_test.py and retail_changed_field_delta_test.py)."""
    b_root = tmp_path / "install_b"
    b_subsys = b_root / "subsystems"
    b_subsys.mkdir(parents=True, exist_ok=True)

    original_base_dir, original_subsys_dir = schema.BASE_DIR, schema.SUBSYS_DIR
    schema.BASE_DIR = str(b_root)
    schema.SUBSYS_DIR = str(b_subsys)
    try:
        schema.init_retail()
    finally:
        schema.BASE_DIR, schema.SUBSYS_DIR = original_base_dir, original_subsys_dir

    db_path = b_subsys / "retail.db"

    def _get_conn():
        c = sqlite3.connect(str(db_path), timeout=30)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA busy_timeout=30000")
        c.execute("PRAGMA foreign_keys=ON")
        return c

    # Stands in for the `local_ensure_schema` hook a real install wires into
    # its SyncService -- see retail_money_sync_test.py's own `install_b`
    # fixture for the full reasoning (its own `_CREDIT_SCHEMA_READY`
    # process-global-flag paragraph applies verbatim here). Needed because
    # this file's sale/sale_item tests apply a pulled sale into a database
    # that never served a single HTTP route before its first pull.
    import api.retail_api as _retail_api_module
    _previous_flag = _retail_api_module._CREDIT_SCHEMA_READY
    _retail_api_module._CREDIT_SCHEMA_READY = False
    try:
        _ensure_credit_schema(_get_conn())
    finally:
        _retail_api_module._CREDIT_SCHEMA_READY = _previous_flag

    return _get_conn


def _service_b(relay, install_b):
    return SyncService(client_factory=lambda: relay, get_conn=install_b,
                        local_company_id_provider=lambda: "install-b-company")


@pytest.fixture(autouse=True)
def _clean_install_a_outbox():
    """Install A is booted ONCE at module import time and shared by every
    test below -- there is no per-test database the way `install_b` gives
    each test its own temp directory. `sync_outbox` is device-wide (see
    sync_service.py's `_pending_outbox_count` docstring), so without this a
    later test's `_single_outbox_event` would silently read an EARLIER
    test's row. Matches retail_money_sync_test.py's identical fixture."""
    conn = get_retail_conn()
    conn.execute("DELETE FROM sync_outbox")
    conn.commit()
    conn.close()
    yield
    conn = get_retail_conn()
    conn.execute("DELETE FROM sync_outbox")
    conn.commit()
    conn.close()


# ─────────────────────────────────────────────────────────────────────────────
# 1. products.avg_cost -- INSERT path (B has never seen this product)
# ─────────────────────────────────────────────────────────────────────────────

def test_product_avg_cost_converges_for_a_device_that_has_never_seen_the_row(install_b, relay):
    admin, cid = _new_admin()
    supplier = _create_supplier(admin)
    pid = _create_product(admin, cost_price=1.0)
    _po_receipt(admin, supplier, pid, qty=10, unit_cost=7.5)

    # Deliberately NOT service_a.push_once() -- see the module docstring's
    # own paragraph on this test. Pushing only the `update` event means B's
    # very first-ever contact with this product id already carries a
    # nonzero avg_cost, so the plain INSERT half of `_apply_event`'s product
    # branch (ON CONFLICT never fires -- there is no local row to conflict
    # with) is what has to carry it.
    event = _single_outbox_event("product", "update")
    assert event["payload"]["avg_cost"] == 7.5
    assert event["payload"]["_changed_fields"] == ["avg_cost"]
    relay.push([event])

    service_b = _service_b(relay, install_b)
    service_b.pull_once()

    b_conn = install_b()
    try:
        b_row = b_conn.execute("SELECT avg_cost FROM products WHERE id=?", (pid,)).fetchone()
    finally:
        b_conn.close()
    assert b_row is not None, (
        "the update event's INSERT half never created the row on a device that had never seen this id")
    assert b_row["avg_cost"] == 7.5


# ─────────────────────────────────────────────────────────────────────────────
# 2. products.avg_cost -- UPDATE/delta path (B already has the row)
# ─────────────────────────────────────────────────────────────────────────────

def test_product_avg_cost_converges_via_delta_update_to_a_device_that_already_has_the_row(install_b, relay):
    admin, cid = _new_admin()
    supplier = _create_supplier(admin)
    pid = _create_product(admin, cost_price=1.0)

    service_a = SyncService(client_factory=lambda: relay, get_conn=get_retail_conn)
    service_b = _service_b(relay, install_b)

    # B learns the row via an ordinary create event first -- avg_cost=0,
    # same as A's own row at this point.
    service_a.push_once()
    service_b.pull_once()

    b_conn = install_b()
    try:
        seed = b_conn.execute("SELECT avg_cost FROM products WHERE id=?", (pid,)).fetchone()
    finally:
        b_conn.close()
    assert seed["avg_cost"] == 0

    # A receives stock -> avg_cost moves to 7.5, queuing a real `product`/
    # `update` event with `_changed_fields: ['avg_cost']`.
    _po_receipt(admin, supplier, pid, qty=10, unit_cost=7.5)
    service_a.push_once()
    service_b.pull_once()

    b_conn = install_b()
    try:
        b_row = b_conn.execute("SELECT avg_cost FROM products WHERE id=?", (pid,)).fetchone()
    finally:
        b_conn.close()
    assert b_row["avg_cost"] == 7.5


# ─────────────────────────────────────────────────────────────────────────────
# 3. A delta event that does NOT name avg_cost leaves B's value alone
# ─────────────────────────────────────────────────────────────────────────────

def test_product_delta_event_naming_a_different_column_leaves_avg_cost_alone(install_b, relay):
    admin, cid = _new_admin()
    supplier = _create_supplier(admin)
    pid = _create_product(admin, cost_price=1.0)
    _po_receipt(admin, supplier, pid, qty=10, unit_cost=9.0)

    service_a = SyncService(client_factory=lambda: relay, get_conn=get_retail_conn)
    service_b = _service_b(relay, install_b)
    service_a.push_once()
    service_b.pull_once()

    b_conn = install_b()
    try:
        seed = b_conn.execute("SELECT avg_cost, cost_price FROM products WHERE id=?", (pid,)).fetchone()
    finally:
        b_conn.close()
    assert seed["avg_cost"] == 9.0
    assert seed["cost_price"] == 1.0

    # An ordinary edit that never touches avg_cost -- update_product's own
    # `_changed_fields` names only `cost_price` here (see retail_changed_
    # field_delta_test.py's identical product-branch proof for this
    # mechanism). Widening `_delta_set_clause`'s column list to include
    # `avg_cost` must not make it a column that gets clobbered by an
    # unrelated PATCH.
    patch = admin.patch(f'{API}/products/{pid}', json={'cost_price': 3.5})
    assert patch.status_code == 200, patch.get_json()
    service_a.push_once()
    service_b.pull_once()

    b_conn = install_b()
    try:
        b_row = b_conn.execute("SELECT avg_cost, cost_price FROM products WHERE id=?", (pid,)).fetchone()
    finally:
        b_conn.close()
    assert b_row["cost_price"] == 3.5   # the real edit landed
    assert b_row["avg_cost"] == 9.0     # avg_cost, untouched by this event, survived


# ─────────────────────────────────────────────────────────────────────────────
# 4. sale_items.unit_cost -- INSERT path (sale_item is create-only, no delta
#    half exists at all -- see sync_service.py's own comment on that branch)
# ─────────────────────────────────────────────────────────────────────────────

def test_sale_item_unit_cost_converges_for_a_device_that_has_never_seen_the_sale(install_b, relay):
    admin, cid = _new_admin()
    # No PO receipt needed: create_sale's own cost_basis fallback is
    # `avg_cost or cost_price` (retail_api.py), so a fresh product's
    # nonzero cost_price alone is enough to snapshot a nonzero unit_cost.
    # initial_stock=10 (overriding this file's own 0 default) -- there has
    # to be something on hand to actually sell.
    pid = _create_product(admin, cost_price=4.25, initial_stock=10)
    sale = _sell(admin, pid, qty=3)

    service_a = SyncService(client_factory=lambda: relay, get_conn=get_retail_conn)
    service_b = _service_b(relay, install_b)
    service_a.push_once()
    service_b.pull_once()

    b_conn = install_b()
    try:
        b_items = [dict(r) for r in b_conn.execute("SELECT * FROM sale_items").fetchall()]
    finally:
        b_conn.close()
    assert len(b_items) == 1
    assert b_items[0]['unit_cost'] == 4.25
    assert b_items[0]['line_total'] == sale['total']


# ─────────────────────────────────────────────────────────────────────────────
# 5. sales.points_redeemed_amount -- INSERT path (sale is create-only too)
# ─────────────────────────────────────────────────────────────────────────────

def test_sale_points_redeemed_amount_converges_for_a_device_that_has_never_seen_the_sale(install_b, relay):
    admin, cid = _new_admin()
    pid = _create_product(admin, cost_price=1.0, sell_price=100.0, initial_stock=10)
    cust = _create_customer(admin)
    _set_loyalty_point_value(admin, 0.5)
    _grant_ledger_points(cid, cust, 20)

    sale = _sell(admin, pid, qty=1, customer_id=cust, points_redeemed=10)
    assert sale['total'] == 100.0

    service_a = SyncService(client_factory=lambda: relay, get_conn=get_retail_conn)
    service_b = _service_b(relay, install_b)
    service_a.push_once()
    service_b.pull_once()

    b_conn = install_b()
    try:
        b_sales = [dict(r) for r in b_conn.execute("SELECT * FROM sales").fetchall()]
    finally:
        b_conn.close()
    assert len(b_sales) == 1
    # 10 points * 0.5 currency/point = 5.0 -- see create_sale's own
    # points_applied/points_redeemed_amount comment for the exact formula.
    assert b_sales[0]['points_redeemed_amount'] == 5.0
    assert b_sales[0]['amount_paid'] == 95.0  # total(100) - points_redeemed_amount(5)
