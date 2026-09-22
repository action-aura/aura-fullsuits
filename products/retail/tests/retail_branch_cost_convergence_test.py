"""Aura Retail -- schema v40 (per-branch cost basis) SYNC-RECEIVE-side
convergence proof for `commercial_runtime/sync/sync_service.py`'s
`_apply_event` `inventory_movement` branch.

Root cause this file exists to close, per database/schema.py's own v40
migration docstring ("NOT MAINTAINED BY THIS MIGRATION"): `inventory_
balances` is not itself a synced entity -- each device DERIVES it locally by
REPLAYING `inventory_movement` events (sync_service.py's own "HAZARD 1"
comment). That apply branch maintained `quantity_on_hand` from
`p.get("quantity")` but had NO equivalent maintenance of `avg_cost` from
`p.get("unit_cost")` -- so a device that only ever learns a branch's stock
THROUGH SYNC, never through a local PO receipt or a local transfer-receive at
that branch, never converged its own `inventory_balances.avg_cost` to match
the device that actually did the work. This file proves the fix: a figure
COMPUTED on device A and ARRIVING, correctly, at device B -- never merely
that a column got written.

retail_sync_cost_basis_convergence_test.py already proves the SAME kind of
gap for `products.avg_cost` (company-wide, schema v39, a REAL synced entity
with its own `product`/`update` event). This file is the per-BRANCH sibling:
`inventory_balances.avg_cost` is never itself put on the wire -- it has to be
independently RE-DERIVED on every device from the same `inventory_movement`
events, which makes the replay-side formula (not just the payload) the thing
under test.

Harness: the exact two-install shape retail_money_sync_test.py, retail_
changed_field_delta_test.py and retail_sync_cost_basis_convergence_test.py
already established (see any of their own module docstrings for the full
"AURA_APP_DATA trap" reasoning, not repeated here) -- install A is a real,
fully-booted Flask app; install B is a bare temp SQLite database built
straight from `database.schema.init_retail()`, never a second Flask app;
`InMemoryRelay` stands in for Owner's real Postgres-backed relay.

`branch` is itself a synced entity type (wave B) but is NOT reconciled by
default the way `product`/`customer` are -- every branch-scoped test below
pushes+pulls once right after creating branches so `_resolve_branch_id`'s
tier-1 lookup (uid -> local id, scoped to `local_company_id`) resolves each
`inventory_movement` event to the CORRECT branch on B, rather than every
event silently merging onto B's tier-2 fallback (its own self-healed default
branch) -- see that method's own docstring for the two-tier design.

Two tests (`test_zero_cost_movement_does_not_drag_average_toward_zero` and
`test_replaying_the_same_movement_event_twice_does_not_move_the_average_
twice`) hand-build an `inventory_movement` event and push it straight into
`InMemoryRelay`, matching retail_sync_cost_basis_convergence_test.py's own
`_single_outbox_event`-based test's technique -- the ONLY way to put a
`unit_cost: 0` `purchase_in`/`transfer_in` event on the wire deliberately (no
live route ever emits one: a PO receipt always carries whatever price the
shopkeeper typed, and this scenario is meant to model a pre-v40 replayed
event whose sender never recorded a cost, per schema.py's own v40 migration
docstring) and the only reliable way to force a genuine second application of
the SAME already-applied event (resetting B's own `sync_cursor` back to 0 and
pulling again, rather than hoping for a natural replay).

Every weighted-average figure asserted below is a "nice" number -- every
quantity/price combination is chosen so the TRUE weighted average terminates
within 1-2 decimal places. This is deliberate, not an oversight: api/
retail_api.py's own local writers round through `_money(x, currency)`, which
quantizes to THIS COMPANY's own currency precision (JOD's default is 3dp);
sync_service.py has no way to look that up without importing a retail-
specific currency lookup into shared code (see this file's own change for
the reasoning), so it quantizes to a fixed, currency-agnostic 4dp instead.
For a "nice" number that already terminates within both precisions, the two
roundings agree exactly -- proving CONVERGENCE, the thing under test. A true
non-terminating weighted average (e.g. 125/15) would still converge in
VALUE but could show extra insignificant digits under B's coarser-than-A or
finer-than-A rounding; that is a display-precision nuance, not a defect this
file's scope covers (the task's own framing names the denominator/negative-
result guards to match, never a currency-exact rounding replication).

CRITICAL: exactly ONE pytest process per file (AUDIT-010) -- these test
files pollute each other in a shared pytest process; see products/
run_all_tests.py.

Run:
    pytest products/retail/tests/retail_branch_cost_convergence_test.py -v
"""
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

DATA = Path(tempfile.mkdtemp(prefix="aura_retail_branchcostsync_"))
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

#: `_service_b`'s own fixed `local_company_id_provider` value -- install B's
#: single-tenant identity is independent of whatever real company uuid `cid`
#: install A uses (see `_apply_event`: every write uses `local_company_id`,
#: never the payload's own `company_id`).
B_COMPANY_ID = "install-b-company"


def teardown_module(module):
    shutil.rmtree(DATA, ignore_errors=True)


# ── Install A: the real Flask app + its own real database ──────────────────

def _new_admin():
    email = f'branchcost-admin-{uuid.uuid4().hex[:8]}@test.local'
    password = 'BranchCostSyncPW1'
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


def _create_branch(c, name):
    r = c.post(f'{API}/branches', json={'name': name})
    assert r.status_code == 200, r.get_json()
    return r.get_json()['data']['id']


def _branch_uid(cid, branch_id):
    conn = get_retail_conn()
    try:
        row = conn.execute(
            "SELECT uid FROM branches WHERE company_id=? AND id=?", (cid, branch_id)
        ).fetchone()
        assert row is not None
        return row['uid']
    finally:
        conn.close()


def _create_product(c, **kw):
    # initial_stock defaults to 0 -- every avg_cost assertion below
    # hand-computes its own expected weighted average from a known starting
    # qty_on_hand at a SPECIFIC branch, matching retail_v40_branch_cost_
    # test.py's own `_seed_product`'s identical reasoning.
    payload = {'name': 'Branch Cost Sync Widget', 'sku': f'BCSY-{uuid.uuid4().hex[:8]}',
               'cost_price': 1.0, 'sell_price': 50.0, 'tax_rate': 0, 'initial_stock': 0}
    payload.update(kw)
    r = c.post(f'{API}/products', json=payload)
    assert r.status_code == 200, r.get_json()
    return r.get_json()['data']['id']


def _create_supplier(c):
    r = c.post(f'{API}/suppliers', json={'name': f'BCSY Supplier {uuid.uuid4().hex[:6]}'})
    assert r.status_code == 200, r.get_json()
    return r.get_json()['data']['id']


def _po_receipt(c, supplier_id, pid, branch_id, qty, unit_cost):
    """Create + receive in one call, into a SPECIFIC branch -- matches
    retail_v40_branch_cost_test.py's own helper of the same name."""
    r = c.post(f'{API}/purchase-orders', json={
        'supplier_id': supplier_id, 'branch_id': branch_id,
        'items': [{'product_id': pid, 'quantity': qty, 'unit_cost': unit_cost}]})
    assert r.status_code == 200, r.get_json()
    po_id = r.get_json()['data']['id']
    r = c.post(f'{API}/purchase-orders/{po_id}/receive')
    assert r.status_code == 200, r.get_json()
    return po_id


def _create_transfer(c, source_bid, dest_bid, pid, qty):
    r = c.post(f'{API}/stock-transfers', json={
        'source_branch_id': source_bid, 'destination_branch_id': dest_bid,
        'items': [{'product_id': pid, 'quantity': qty}]})
    assert r.status_code == 200, r.get_json()
    return r.get_json()['data']['id']


def _send_transfer(c, transfer_id):
    r = c.post(f'{API}/stock-transfers/{transfer_id}/send')
    assert r.status_code == 200, r.get_json()


def _first_item_id(c, transfer_id):
    r = c.get(f'{API}/stock-transfers/{transfer_id}')
    assert r.status_code == 200, r.get_json()
    return r.get_json()['data']['items'][0]['id']


def _receive_transfer(c, transfer_id, item_id, qty_received):
    r = c.post(f'{API}/stock-transfers/{transfer_id}/receive', json={
        'items': [{'id': item_id, 'quantity_received': qty_received}]})
    assert r.status_code == 200, r.get_json()


def _sell(c, pid, branch_id, qty):
    r = c.post(f'{API}/sales', json={
        'branch_id': branch_id,
        'items': [{'product_id': pid, 'quantity': qty}],
        'payment_method': 'cash', 'amount_paid': 1_000_000.0,
        'idempotency_key': str(uuid.uuid4())})
    assert r.status_code == 200, r.get_json()
    return r.get_json()['data']['id']


# ── Read helpers -- install A's own real database (raw SQL, same posture as
#    retail_v40_branch_cost_test.py's own `_branch_avg_cost`/`_branch_qty`,
#    reading state a route does not itself expose through a JSON response) ──

def _a_branch_avg_cost(cid, pid, bid):
    conn = get_retail_conn()
    try:
        row = conn.execute(
            "SELECT avg_cost FROM inventory_balances WHERE company_id=? AND product_id=? AND branch_id=?",
            (cid, pid, bid)
        ).fetchone()
        return float(row['avg_cost']) if row and row['avg_cost'] is not None else 0.0
    finally:
        conn.close()


# ── The relay double (identical to retail_sync_cost_basis_convergence_
#    test.py's own) ──────────────────────────────────────────────────────

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
    """Identical technique to retail_sync_cost_basis_convergence_test.py's
    own fixture of the same name -- see that file's module docstring for
    the full reasoning."""
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

    import api.retail_api as _retail_api_module
    _previous_flag = _retail_api_module._CREDIT_SCHEMA_READY
    _retail_api_module._CREDIT_SCHEMA_READY = False
    try:
        _ensure_credit_schema(_get_conn())
    finally:
        _retail_api_module._CREDIT_SCHEMA_READY = _previous_flag

    return _get_conn


def _service_a(relay):
    return SyncService(client_factory=lambda: relay, get_conn=get_retail_conn)


def _service_b(relay, install_b):
    return SyncService(client_factory=lambda: relay, get_conn=install_b,
                        local_company_id_provider=lambda: B_COMPANY_ID)


@pytest.fixture(autouse=True)
def _clean_install_a_outbox():
    """Install A is booted ONCE at module import time and shared by every
    test below. Matches retail_sync_cost_basis_convergence_test.py's
    identical fixture."""
    conn = get_retail_conn()
    conn.execute("DELETE FROM sync_outbox")
    conn.commit()
    conn.close()
    yield
    conn = get_retail_conn()
    conn.execute("DELETE FROM sync_outbox")
    conn.commit()
    conn.close()


# ── Read helpers -- install B's own real database ───────────────────────────

def _b_branch_id(install_b, branch_uid):
    conn = install_b()
    try:
        row = conn.execute(
            "SELECT id FROM branches WHERE company_id=? AND uid=?", (B_COMPANY_ID, branch_uid)
        ).fetchone()
        assert row is not None, f"branch {branch_uid!r} never synced to install B"
        return row['id']
    finally:
        conn.close()


def _b_balance(install_b, pid, bid_on_b):
    conn = install_b()
    try:
        row = conn.execute(
            "SELECT quantity_on_hand, avg_cost FROM inventory_balances "
            "WHERE company_id=? AND product_id=? AND branch_id=?",
            (B_COMPANY_ID, pid, bid_on_b)
        ).fetchone()
        if row is None:
            return None, None
        return float(row['quantity_on_hand']), float(row['avg_cost'] or 0)
    finally:
        conn.close()


def _b_reset_cursor(install_b):
    """Rewinds install B's own pull cursor to the beginning -- the same
    `sync_cursor.last_seq` row `pull_once()` itself reads (sync_service.py
    ~line 958). Re-running `pull_once()` after this refetches EVERY event
    the relay has ever held, exercising the real `ON CONFLICT(uid) ... DO
    NOTHING` idempotency path this file's replay test proves, rather than a
    synthetic double-call to `_apply_event` that would not prove the actual
    machinery."""
    conn = install_b()
    try:
        conn.execute("UPDATE sync_cursor SET last_seq=0 WHERE id=1")
        conn.commit()
    finally:
        conn.close()


def _movement_event(uid, pid, branch_uid, movement_type, quantity, unit_cost):
    """Hand-built `inventory_movement`/`create` event, matching the exact
    payload shape api/retail_api.py's receive_purchase_order/receive_stock_
    transfer emit (see either route's own `_queue_sync_event` call) -- used
    only where a real route cannot produce the scenario on demand (a
    unit_cost of exactly 0 on an 'in' movement, or a second delivery of an
    event already applied)."""
    return {
        "id": str(uuid.uuid4()),
        "entity_type": "inventory_movement", "entity_id": uid, "event_type": "create",
        "payload": {
            "uid": uid, "product_id": pid, "branch_id": None, "branch_uid": branch_uid,
            "movement_type": movement_type, "quantity": quantity, "unit_cost": unit_cost,
            "reference": "TEST-HAND-BUILT", "notes": None, "created_by": "test-fixture",
            "actor_user_uid": None, "terminal_id": None, "created_at_utc": "2026-09-22T00:00:00Z",
        },
        "created_at": "2026-09-22T00:00:00Z",
    }


# ═════════════════════════════════════════════════════════════════════════
# 1. purchase_in -- first receipt into an empty branch converges exactly.
# ═════════════════════════════════════════════════════════════════════════

def test_first_receipt_into_empty_branch_converges(install_b, relay):
    admin, cid = _new_admin()
    bid_a = _create_branch(admin, 'Convergence Branch A')
    supplier = _create_supplier(admin)
    pid = _create_product(admin)

    _po_receipt(admin, supplier, pid, bid_a, qty=10, unit_cost=7.50)
    assert _a_branch_avg_cost(cid, pid, bid_a) == 7.50

    service_a = _service_a(relay)
    service_b = _service_b(relay, install_b)
    service_a.push_once()
    service_b.pull_once()

    bid_a_on_b = _b_branch_id(install_b, _branch_uid(cid, bid_a))
    b_qty, b_avg = _b_balance(install_b, pid, bid_a_on_b)
    assert b_qty == 10.0
    assert b_avg == 7.50


# ═════════════════════════════════════════════════════════════════════════
# 2. A second receipt at a different price converges to the SAME weighted
#    average on both devices.
# ═════════════════════════════════════════════════════════════════════════

def test_second_receipt_at_different_price_converges_to_same_weighted_average(install_b, relay):
    admin, cid = _new_admin()
    bid_a = _create_branch(admin, 'Convergence Branch A2')
    supplier = _create_supplier(admin)
    pid = _create_product(admin)

    service_a = _service_a(relay)
    service_b = _service_b(relay, install_b)

    _po_receipt(admin, supplier, pid, bid_a, qty=10, unit_cost=7.50)
    service_a.push_once()
    service_b.pull_once()
    bid_a_on_b = _b_branch_id(install_b, _branch_uid(cid, bid_a))
    _, b_avg = _b_balance(install_b, pid, bid_a_on_b)
    assert b_avg == 7.50

    # (10*7.50 + 10*8.50) / 20 = 160/20 = 8.00 exactly.
    _po_receipt(admin, supplier, pid, bid_a, qty=10, unit_cost=8.50)
    assert _a_branch_avg_cost(cid, pid, bid_a) == 8.00

    service_a.push_once()
    service_b.pull_once()

    b_qty, b_avg = _b_balance(install_b, pid, bid_a_on_b)
    assert b_qty == 20.0
    assert b_avg == 8.00


# ═════════════════════════════════════════════════════════════════════════
# 3. A transfer between branches converges on B, with the destination
#    blended correctly against ITS OWN pre-existing average.
# ═════════════════════════════════════════════════════════════════════════

def test_transfer_between_branches_converges_with_destination_blended_correctly(install_b, relay):
    admin, cid = _new_admin()
    bid_a = _create_branch(admin, 'Transfer Source')
    bid_b = _create_branch(admin, 'Transfer Dest')
    supplier = _create_supplier(admin)
    pid = _create_product(admin)

    # Source: 10 @ 4.00 -> avg 4.00. Destination: 6 @ 10.00 -> avg 10.00.
    _po_receipt(admin, supplier, pid, bid_a, qty=10, unit_cost=4.00)
    _po_receipt(admin, supplier, pid, bid_b, qty=6, unit_cost=10.00)

    transfer_id = _create_transfer(admin, bid_a, bid_b, pid, qty=4)
    _send_transfer(admin, transfer_id)
    item_id = _first_item_id(admin, transfer_id)
    _receive_transfer(admin, transfer_id, item_id, qty_received=4)

    # Source unaffected by its own transfer_out (removing stock does not
    # change what the remaining stock cost).
    assert _a_branch_avg_cost(cid, pid, bid_a) == 4.00
    # Destination: (6*10.00 + 4*4.00) / 10 = 76/10 = 7.60 exactly.
    assert _a_branch_avg_cost(cid, pid, bid_b) == 7.60

    service_a = _service_a(relay)
    service_b = _service_b(relay, install_b)
    service_a.push_once()
    service_b.pull_once()

    bid_a_on_b = _b_branch_id(install_b, _branch_uid(cid, bid_a))
    bid_b_on_b = _b_branch_id(install_b, _branch_uid(cid, bid_b))

    a_qty, a_avg = _b_balance(install_b, pid, bid_a_on_b)
    assert a_qty == 6.0    # 10 sent 4 away
    assert a_avg == 4.00

    d_qty, d_avg = _b_balance(install_b, pid, bid_b_on_b)
    assert d_qty == 10.0   # 6 + 4 received
    assert d_avg == 7.60


# ═════════════════════════════════════════════════════════════════════════
# 4. sale_out does NOT change the converged average on B.
# ═════════════════════════════════════════════════════════════════════════

def test_sale_out_does_not_change_convergent_average(install_b, relay):
    """Two parts, deliberately.

    Part 1 rings a REAL sale through create_sale and proves the ordinary,
    honest case: today's `sale_out` emission hardcodes `unit_cost: 0` on the
    wire (api/retail_api.py, the `inventory_movements` INSERT a few lines
    above its own `_queue_sync_event` call never even lists a `unit_cost`
    column) -- so in practice this scenario is ALREADY caught by the
    zero-cost guard alone, and is not, by itself, capable of mutation-
    proving that `sale_out` is excluded from the movement_type allowlist
    specifically. Proven directly: adding 'sale_out' to the allowlist
    tuple WITHOUT also touching the unit_cost guard left this exact
    assertion green (`incoming_unit_cost` is 0 for every real sale_out
    event today, so the OTHER guard masks the allowlist one).

    Part 2 isolates the allowlist guard on its own by hand-building a
    `sale_out` event with a deliberately NONZERO unit_cost that also
    differs from the branch's already-converged average (2.00 against an
    established 5.00) -- a shape no live route emits today, but exactly
    what a future emitter (or an adversarial/corrupted relay payload)
    could carry. B is already at qty=7/avg=5.00 from part 1 when this
    lands; if `sale_out` were ever allowed to weight in, (7*5.00 +
    (-2)*2.00)/5 = 31/5 = 6.2 would land on B (reproduced live while
    writing this test -- mutating the allowlist to include 'sale_out'
    landed exactly 6.2). This proves it does not, independently of
    whatever unit_cost sale_out happens to carry today."""
    admin, cid = _new_admin()
    bid_a = _create_branch(admin, 'Sale Branch')
    supplier = _create_supplier(admin)
    pid = _create_product(admin, sell_price=99.0)

    _po_receipt(admin, supplier, pid, bid_a, qty=10, unit_cost=5.00)
    _sell(admin, pid, bid_a, qty=3)

    assert _a_branch_avg_cost(cid, pid, bid_a) == 5.00  # sale never touches it locally either

    service_a = _service_a(relay)
    service_b = _service_b(relay, install_b)
    service_a.push_once()
    service_b.pull_once()

    bid_a_on_b = _b_branch_id(install_b, _branch_uid(cid, bid_a))
    b_qty, b_avg = _b_balance(install_b, pid, bid_a_on_b)
    assert b_qty == 7.0    # 10 - 3 sold
    assert b_avg == 5.00, "sale_out must never move the branch average"

    # Part 2: isolate the movement_type-allowlist guard from the unit_cost
    # guard by giving a hand-built sale_out event a real, nonzero cost that
    # differs from the converged average.
    branch_uid = _branch_uid(cid, bid_a)
    skewing_event = _movement_event(
        uid=str(uuid.uuid4()), pid=pid, branch_uid=branch_uid,
        movement_type='sale_out', quantity=-2, unit_cost=2.00,
    )
    relay.push([skewing_event])
    service_b.pull_once()

    b_qty, b_avg = _b_balance(install_b, pid, bid_a_on_b)
    assert b_qty == 5.0, "quantity must still move for a sale_out, allowlisted or not"
    assert b_avg == 5.00, "a sale_out carrying a real, nonzero unit_cost must still not move the average"


# ═════════════════════════════════════════════════════════════════════════
# 5. THE HEADLINE GUARD: a movement carrying unit_cost 0 must not drag a
#    real average toward zero.
# ═════════════════════════════════════════════════════════════════════════

def test_zero_cost_movement_does_not_drag_average_toward_zero(install_b, relay):
    admin, cid = _new_admin()
    bid_a = _create_branch(admin, 'Zero Cost Branch')
    supplier = _create_supplier(admin)
    pid = _create_product(admin)

    _po_receipt(admin, supplier, pid, bid_a, qty=10, unit_cost=6.00)

    service_a = _service_a(relay)
    service_b = _service_b(relay, install_b)
    service_a.push_once()
    service_b.pull_once()

    bid_a_on_b = _b_branch_id(install_b, _branch_uid(cid, bid_a))
    b_qty, b_avg = _b_balance(install_b, pid, bid_a_on_b)
    assert b_qty == 10.0
    assert b_avg == 6.00

    # No live route ever emits a purchase_in/transfer_in with unit_cost 0 --
    # this models a pre-v40 replayed event, per schema.py's own v40
    # migration docstring. Hand-built and pushed directly, matching retail_
    # sync_cost_basis_convergence_test.py's own `_single_outbox_event`
    # technique.
    zero_cost_event = _movement_event(
        uid=str(uuid.uuid4()), pid=pid, branch_uid=_branch_uid(cid, bid_a),
        movement_type='purchase_in', quantity=5, unit_cost=0,
    )
    relay.push([zero_cost_event])
    service_b.pull_once()

    b_qty, b_avg = _b_balance(install_b, pid, bid_a_on_b)
    assert b_qty == 15.0, "quantity must still move even when the average update is skipped"
    assert b_avg == 6.00, "a unit_cost=0 movement must not drag a real average toward zero"


# ═════════════════════════════════════════════════════════════════════════
# 6. Replaying the same movement event twice leaves the average (and the
#    quantity) exactly where they were.
# ═════════════════════════════════════════════════════════════════════════

def test_replaying_the_same_movement_event_twice_does_not_move_the_average_twice(install_b, relay):
    admin, cid = _new_admin()
    bid_a = _create_branch(admin, 'Replay Branch')
    supplier = _create_supplier(admin)
    pid = _create_product(admin)

    _po_receipt(admin, supplier, pid, bid_a, qty=10, unit_cost=7.50)

    service_a = _service_a(relay)
    service_b = _service_b(relay, install_b)
    service_a.push_once()
    service_b.pull_once()

    bid_a_on_b = _b_branch_id(install_b, _branch_uid(cid, bid_a))
    b_qty, b_avg = _b_balance(install_b, pid, bid_a_on_b)
    assert b_qty == 10.0
    assert b_avg == 7.50

    # Rewind B's own cursor and pull again -- refetches EVERY event the
    # relay has ever held (branch/product/inventory_movement creates
    # included), re-running them through `_apply_event` a second time. The
    # movement's own `ON CONFLICT(uid) ... DO NOTHING` (HAZARD 1) makes
    # `cur.rowcount` 0 on this second pass, which is the SAME guard this
    # file's own avg_cost update is nested inside -- so the average must be
    # untouched exactly like the quantity is.
    _b_reset_cursor(install_b)
    service_b.pull_once()

    b_qty, b_avg = _b_balance(install_b, pid, bid_a_on_b)
    assert b_qty == 10.0, "a replayed movement must not double the quantity"
    assert b_avg == 7.50, "a replayed movement must not move the average a second time"


# ═════════════════════════════════════════════════════════════════════════
# 7. The negative-quantity (behind_on_sync oversell) path never produces a
#    negative average on B either.
# ═════════════════════════════════════════════════════════════════════════

def test_negative_quantity_path_never_produces_negative_average_on_b(install_b, relay):
    """Reproduces, on the SYNC-APPLY side, the exact scenario retail_v40_
    branch_cost_test.py's own `test_branch_average_never_goes_negative_on_
    a_receipt_after_oversell` proves for the LOCAL write path: old_avg=10,
    qty_on_hand=-5 (left behind on THIS device by its own behind_on_sync
    oversell -- see create_sale's own comment block), receive 6 @ 5.00.
    Unguarded: ((-5)*10 + 6*5)/1 = -20.00. Guarded: falls back to the
    observed incoming cost, 5.00."""
    admin, cid = _new_admin()
    bid_a = _create_branch(admin, 'Negative Branch')
    supplier = _create_supplier(admin)
    pid = _create_product(admin)

    service_a = _service_a(relay)
    service_b = _service_b(relay, install_b)
    # Sync the branch + product to B FIRST (an empty receipt-free push/pull)
    # so the corruption below lands under the correct local branch id.
    service_a.push_once()
    service_b.pull_once()
    bid_a_on_b = _b_branch_id(install_b, _branch_uid(cid, bid_a))

    # Test-only direct write on B, matching retail_v40_branch_cost_test.py's
    # own `_set_negative_branch_balance` -- simulates the state B's own
    # behind_on_sync oversell path would have left, without reproducing the
    # entire multi-device oversell flow just to get there.
    conn = install_b()
    try:
        conn.execute(
            "INSERT INTO inventory_balances (company_id,product_id,branch_id,quantity_on_hand,avg_cost) "
            "VALUES (?,?,?,?,?) "
            "ON CONFLICT(company_id,product_id,branch_id) DO UPDATE SET "
            "quantity_on_hand=excluded.quantity_on_hand, avg_cost=excluded.avg_cost",
            (B_COMPANY_ID, pid, bid_a_on_b, -5, 10.0),
        )
        conn.commit()
    finally:
        conn.close()

    _po_receipt(admin, supplier, pid, bid_a, qty=6, unit_cost=5.00)
    service_a.push_once()
    service_b.pull_once()

    b_qty, b_avg = _b_balance(install_b, pid, bid_a_on_b)
    assert b_qty == 1.0  # -5 + 6
    assert b_avg >= 0.0, f"branch avg_cost must never go negative on B, got {b_avg!r}"
    assert b_avg == 5.00
