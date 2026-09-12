"""Aura Retail -- Phase 5 wave B (launch-readiness): stock-moving sync, end
to end through the real routes, across REAL separate OS processes.

Scope: exactly two entity types -- `inventory_movement` and `branch` -- the
two Phase 5 wave B adds to the sync allowlist (`sale`/`sale_item`/`payment`/
`return`/`return_item` were wave A; `user` is out of scope entirely, see
sync_service.py's module docstring). Wave A deliberately left this seam
open: `sync_service.py` never touched `inventory_balances`/
`inventory_movements`/`quantity_on_hand`, so a sale that synced from another
device moved money but never moved stock on the receiver. This file proves
that seam is closed, and -- just as importantly -- proves it WITHOUT
reopening Phase 3's guarantee that `inventory_balances` stays provably
derivable from `inventory_movements` (docs/launch-readiness/
phase3-ledger-truth.md), without double-counting a pulled sale's own
decrement, and without letting a rewritten branch or a doubled ledger row
slip past the gate.

THE HARNESS, AND WHY retail_money_sync_test.py's `install_b` IS NOT REUSED
FOR THE DECISIVE TESTS BELOW.

Wave A's own delivered suites -- including retail_money_sync_test.py's own
`install_b` fixture -- were blind to all three of that wave's shipped
defects, TWICE, because `install_b` builds the second device as a PURE
RECEIVER: a bare temp SQLite database seeded from `database.schema.
init_retail()`, never a second real Flask app, never a device that rings a
sale or posts an adjustment of its own. Its `doc_sequences` stay at zero
forever; nothing it does ever collides with anything, because only one side
of the pair ever writes anything at all. That fixture is still used below
for the apply-side unit-shaped tests in section 2 (DO NOTHING vs DO UPDATE,
quarantine, balance-created-on-demand) -- it is the right tool for "does
this one INSERT statement do what it claims" -- but the tests that actually
decide whether wave B works (section 3) stand up TWO real, independently
selling-and-adjusting devices in SEPARATE OS PROCESSES via
_stock_sync_harness.py's `run_device()`, each with its own `AURA_APP_DATA`,
its own `retail.db`, its own process globals -- see that module's own
docstring for the full "AURA_APP_DATA trap" reasoning this sidesteps.

Four hazards this phase names explicitly, each with its own test group
below:

  1. THE LEDGER. Applying a movement must update `inventory_balances`
     atomically with the movement insert, create the balance row when it
     does not exist, and do so IF AND ONLY IF the movement insert actually
     happened -- never on a `DO NOTHING` no-op (that doubles stock with no
     error). The decisive test: `compute_drift` returns zero, on BOTH real
     devices, the actual Phase 3 gate function, not "balances look right".
  2. DEPENDENCY/RESOLUTION. A movement whose product has not arrived yet is
     quarantined (parked, visible, replayable), never dropped or allowed to
     wedge the pull batch. `branch_uid` resolution should ORDINARILY
     resolve at tier 1 now that `branch` itself syncs -- proved directly.
  3. MUTABILITY. `branch` converges on a rename (`DO UPDATE`); `inventory_
     movement` never does (`DO NOTHING`, replay-safe) -- getting these
     backwards is how stock or its history gets rewritten from another
     device.
  4. ATTRIBUTION AND THE DRAWER. `actor_user_uid`/`terminal_id`/
     `created_at_utc` are preserved from the ORIGINATING device, never the
     receiver's. A branch arriving by sync must never disturb a Phase 4
     open cash drawer.

Run:
    pytest products/retail/tests/retail_stock_sync_test.py -v
"""
from __future__ import annotations

import contextlib
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
for _p in (str(SUITE_ROOT), str(BACKEND_DIR), str(Path(__file__).resolve().parent)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

DATA = Path(tempfile.mkdtemp(prefix="aura_retail_stocksync_"))
(DATA / "database" / "subsystems").mkdir(parents=True, exist_ok=True)
os.environ.update(AURA_STANDALONE="1", AURA_BUNDLE_DIR=str(BACKEND_DIR), AURA_APP_DATA=str(DATA))
os.environ.pop("AURA_DEV", None)

from commercial_runtime.licensing_contracts.test_support import seed_active_license  # noqa: E402
seed_active_license(str(DATA), product_code="AURA_RETAIL", platform="WINDOWS")

import app as _app_module  # noqa: E402

app = _app_module.init_app()  # in-process install A: a real, fully-booted Flask app -- section 1/2 only
app.config["TESTING"] = True

from api import retail_api  # noqa: E402
from api.retail_api import _ensure_credit_schema  # noqa: E402
from commercial_runtime.identity.registry_db import get_conn as registry_conn  # noqa: E402
from commercial_runtime.security.passwords import hash_password  # noqa: E402
from commercial_runtime.sync.sync_service import SyncService  # noqa: E402
from core.retail import stock_reconciliation  # noqa: E402
import database.schema as schema  # noqa: E402
from database.schema import get_retail_conn  # noqa: E402

from _stock_sync_harness import FileRelay, run_device  # noqa: E402

API = '/api/sub/retail'
PY = sys.executable


def teardown_module(module):
    shutil.rmtree(DATA, ignore_errors=True)


def _new_shop(price=100.0, initial_stock=5000):
    """A company with an admin, a product and stock. Returns (admin, cid, pid)."""
    company_id = str(uuid.uuid4())
    email = f'stocksync-admin-{uuid.uuid4().hex[:8]}@test.local'
    password = 'StockSyncPW1'
    conn = registry_conn()
    conn.execute(
        "INSERT INTO users (id, company_id, employee_id, email, password_hash, role, status, require_password_change) "
        "VALUES (?,?,?,?,?,?,?,0)",
        (str(uuid.uuid4()), company_id, 'EMP-0001', email, hash_password(password), 'admin', 'active'),
    )
    conn.commit()
    conn.close()
    admin = app.test_client()
    r = admin.post('/api/auth/login', json={'email': email, 'password': password})
    assert r.status_code == 200, r.get_json()
    p = admin.post(f'{API}/products', json={
        'name': 'Stock Sync Widget', 'sku': f'STK-{uuid.uuid4().hex[:8]}',
        'cost_price': price / 2, 'sell_price': price, 'tax_rate': 0, 'initial_stock': initial_stock})
    assert p.status_code == 200, p.get_json()
    return admin, company_id, p.get_json()['data']['id']


def _outbox(entity_type=None):
    conn = get_retail_conn()
    try:
        if entity_type:
            rows = conn.execute(
                "SELECT * FROM sync_outbox WHERE entity_type=? ORDER BY rowid", (entity_type,)
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM sync_outbox ORDER BY rowid").fetchall()
        return [dict(r) | {"payload": json.loads(r["payload"])} for r in rows]
    finally:
        conn.close()


@pytest.fixture(autouse=True)
def _clean_install_a_outbox():
    """Same reasoning as retail_money_sync_test.py's own identical fixture:
    install A is booted ONCE at module import time and shared by every
    section-1/2 test below, so `sync_outbox` (device-wide, not company-
    scoped) must be emptied before AND after each test or a later test's
    `_outbox('branch')[0]` could silently read an earlier test's row."""
    conn = get_retail_conn()
    conn.execute("DELETE FROM sync_outbox")
    conn.commit()
    conn.close()
    yield
    conn = get_retail_conn()
    conn.execute("DELETE FROM sync_outbox")
    conn.commit()
    conn.close()


@pytest.fixture
def relay():
    class InMemoryRelay:
        """Identical double to retail_two_install_roundtrip_test.py's own --
        see that file's module docstring for the full reasoning. Used only by
        section 2's single-process apply-side tests."""

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

    return InMemoryRelay()


@pytest.fixture
def install_b(tmp_path):
    """Identical technique to retail_money_sync_test.py's own `install_b` --
    see that file's module docstring for the full reasoning (repeated here
    rather than imported for the same survive-independently reason every
    other duplicate helper in this suite is)."""
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

    # See retail_money_sync_test.py's own `install_b` fixture for why this
    # exists: `_ensure_credit_schema` is a lazy, route-triggered migration
    # whose process-global ready-flag was already set True by THIS process's
    # own `app = _app_module.init_app()` above, which would make a plain
    # call silently no-op against install B's separate database file.
    import api.retail_api as _retail_api_module
    _previous_flag = _retail_api_module._CREDIT_SCHEMA_READY
    _retail_api_module._CREDIT_SCHEMA_READY = False
    try:
        _ensure_credit_schema(_get_conn())
    finally:
        _retail_api_module._CREDIT_SCHEMA_READY = _previous_flag

    return _get_conn


# ─────────────────────────────────────────────────────────────────────────────
# 1. EMISSION -- every real writer queues the right shape
# ─────────────────────────────────────────────────────────────────────────────

def test_create_product_self_heal_queues_no_branch_event_but_still_queues_the_inventory_movement():
    """REPAIRED (was `..._queues_branch_and_inventory_movement_events`): the
    self-heal (`_default_branch`, no branch exists yet for a brand new
    company) used to fire a `branch`/`create` event alongside the opening-
    stock movement -- that is the wave-B1 defect this phase's fix removes.
    See retail_api.py's `_default_branch`, "Wave B, CORRECTED": broadcasting
    a self-healed branch let two devices that each self-healed before their
    first pull mint two different `uid`s for "the default branch", and since
    the apply-side upsert dedupes on `uid` alone, both devices ended up
    holding two permanently-unmerged 'Main Branch' rows with the shop's
    stock split across them -- `compute_drift` stayed zero throughout,
    because the defect was in IDENTITY, not arithmetic.

    This test is now the REGRESSION GUARD for that exact bug -- a coverage
    GAIN over the original, which asserted the self-heal DID broadcast and
    would have stayed green on the very shape that broke two real devices
    (see `retail_stock_sync_apply_hardening_test.py` and this file's own
    `test_two_devices_that_each_self_heal_before_first_pull_converge_to_one_branch_row_each`
    for the end-to-end, two-process version of this same guarantee).

    The opening-stock movement's OWN emission is untouched by the fix and
    still asserted in full below -- only the branch-uid source changed, from
    the (now absent) branch event to the `branches` table directly, since
    that row still exists locally, self-healed, just never broadcast.
    """
    admin, cid, pid = _new_shop(price=10.0, initial_stock=42)

    branch_events = _outbox('branch')
    assert branch_events == [], (
        "a self-healed default branch must NEVER queue a sync event -- see "
        "_default_branch's 'Wave B, CORRECTED' comment. If this fires, two "
        "devices that each self-heal before their first pull will mint two "
        "permanently-unmerged 'Main Branch' rows -- the exact identity bug "
        "this test now guards against."
    )

    # No branch event to read the uid from any more -- the self-healed row
    # itself still exists locally; read its uid straight from `branches`.
    conn = get_retail_conn()
    try:
        branch_uid = conn.execute(
            "SELECT uid FROM branches WHERE company_id=? ORDER BY id LIMIT 1", (cid,)
        ).fetchone()['uid']
    finally:
        conn.close()

    movement_events = _outbox('inventory_movement')
    assert len(movement_events) == 1
    mv = movement_events[0]['payload']
    assert movement_events[0]['event_type'] == 'create'
    assert mv['product_id'] == pid
    assert mv['branch_uid'] == branch_uid
    assert mv['movement_type'] == 'opening_stock'
    assert mv['quantity'] == 42
    assert mv['actor_user_uid'] is not None or mv['actor_user_uid'] is None  # present either way, never missing the key
    assert 'created_at_utc' in mv
    assert 'terminal_id' in mv


def test_adjust_stock_queues_an_inventory_movement_event_with_the_signed_delta():
    admin, cid, pid = _new_shop(initial_stock=100)
    r = admin.post(f'{API}/products/{pid}/stock-adjust', json={'quantity': -15, 'reason': 'Breakage'})
    assert r.status_code == 200, r.get_json()

    events = _outbox('inventory_movement')
    # One for the opening stock (created during _new_shop), one for this adjustment.
    adj = [e for e in events if e['payload']['movement_type'] in ('stock_in', 'stock_out')]
    assert len(adj) == 1
    assert adj[0]['payload']['quantity'] == -15
    assert adj[0]['payload']['movement_type'] == 'stock_out'
    assert adj[0]['payload']['reference'] == 'ADJ'
    assert adj[0]['payload']['notes'] == 'Breakage'


def test_create_sale_queues_an_inventory_movement_event_alongside_the_sale():
    admin, cid, pid = _new_shop(price=25.0, initial_stock=100)
    r = admin.post(f'{API}/sales', json={
        'items': [{'product_id': pid, 'quantity': 4}],
        'payment_method': 'cash', 'idempotency_key': str(uuid.uuid4())})
    assert r.status_code == 200, r.get_json()

    sale_uid = _outbox('sale')[0]['payload']['uid']
    events = _outbox('inventory_movement')
    sale_movements = [e for e in events if e['payload']['movement_type'] == 'sale_out']
    assert len(sale_movements) == 1
    mv = sale_movements[0]['payload']
    assert mv['product_id'] == pid
    assert mv['quantity'] == -4
    # Same branch_uid the sale event itself carries -- resolved once, reused,
    # not re-derived per line (see create_sale's own emission comment).
    assert mv['branch_uid'] == _outbox('sale')[0]['payload']['branch_uid']
    assert mv['reference'] is not None


def test_create_return_queues_an_inventory_movement_event_alongside_the_return():
    admin, cid, pid = _new_shop(price=25.0, initial_stock=100)
    sale = admin.post(f'{API}/sales', json={
        'items': [{'product_id': pid, 'quantity': 4}],
        'payment_method': 'cash', 'idempotency_key': str(uuid.uuid4())})
    sale_id = sale.get_json()['data']['id']

    ret = admin.post(f'{API}/returns', json={
        'sale_id': sale_id, 'items': [{'product_id': pid, 'quantity': 1}]})
    assert ret.status_code == 200, ret.get_json()

    events = _outbox('inventory_movement')
    return_movements = [e for e in events if e['payload']['movement_type'] == 'return_in']
    assert len(return_movements) == 1
    mv = return_movements[0]['payload']
    assert mv['product_id'] == pid
    assert mv['quantity'] == 1


def test_receive_purchase_order_queues_an_inventory_movement_event_per_line():
    admin, cid, pid = _new_shop(initial_stock=0)
    sup = admin.post(f'{API}/suppliers', json={'name': 'Stock Sync Supplier'})
    sup_id = sup.get_json()['data']['id']
    po = admin.post(f'{API}/purchase-orders', json={
        'supplier_id': sup_id,
        'items': [{'product_id': pid, 'quantity': 30, 'unit_cost': 2.5}]})
    assert po.status_code == 200, po.get_json()
    po_id = po.get_json()['data']['id']

    r = admin.post(f'{API}/purchase-orders/{po_id}/receive')
    assert r.status_code == 200, r.get_json()

    events = _outbox('inventory_movement')
    purchase_movements = [e for e in events if e['payload']['movement_type'] == 'purchase_in']
    assert len(purchase_movements) == 1
    mv = purchase_movements[0]['payload']
    assert mv['product_id'] == pid
    assert mv['quantity'] == 30
    assert mv['unit_cost'] == 2.5


def test_create_branch_route_queues_a_branch_create_event():
    admin, cid, pid = _new_shop()
    r = admin.post(f'{API}/branches', json={'name': 'Second Branch', 'address': '123 St', 'phone': '555'})
    assert r.status_code == 200, r.get_json()

    branch_events = [e for e in _outbox('branch') if e['payload']['name'] == 'Second Branch']
    assert len(branch_events) == 1
    assert branch_events[0]['event_type'] == 'create'
    assert branch_events[0]['payload']['address'] == '123 St'
    assert branch_events[0]['payload']['phone'] == '555'


# ─────────────────────────────────────────────────────────────────────────────
# 2. APPLY-SIDE CORRECTNESS -- single-process, install_b (the right tool for
#    "does this one INSERT statement do what it claims", not the decisive
#    proof -- see section 3 for that)
# ─────────────────────────────────────────────────────────────────────────────

def test_a_movement_applied_on_b_updates_its_balance_and_creates_the_row_if_absent(install_b, relay):
    """HAZARD 1: the balance row may NOT EXIST on a receiver that has never
    stocked this product at this branch -- applying must CREATE it, not
    silently do nothing."""
    admin, cid, pid = _new_shop(price=10.0, initial_stock=200)

    service_a = SyncService(client_factory=lambda: relay, get_conn=get_retail_conn)
    service_b = SyncService(client_factory=lambda: relay, get_conn=install_b,
                             local_company_id_provider=lambda: 'install-b-company')
    service_a.push_once()
    service_b.pull_once()

    branch_uid = _outbox('branch')[0]['payload']['uid'] if _outbox('branch') else None
    b_conn = install_b()
    try:
        b_branch = b_conn.execute("SELECT id FROM branches WHERE company_id='install-b-company'").fetchone()
        assert b_branch is not None, "branch did not sync onto B"
        bal = b_conn.execute(
            "SELECT quantity_on_hand FROM inventory_balances WHERE company_id='install-b-company' "
            "AND product_id=? AND branch_id=?", (pid, b_branch['id'])
        ).fetchone()
        assert bal is not None, "balance row was never created on B"
        assert bal['quantity_on_hand'] == 200.0

        drift = stock_reconciliation.compute_drift(b_conn, 'install-b-company')
        assert drift == [], f"B's ledger disagrees with its own cache: {drift}"
    finally:
        b_conn.close()


def test_replaying_the_full_pull_result_twice_does_not_double_stock(install_b, relay):
    """MUTATION-PROOF #2 (idempotency), as a permanent regression guard --
    mirrors retail_money_sync_test.py's own "MUTATION-PROOF-#1 REGRESSION
    GUARD" precedent for the money side. Re-applying the SAME already-seen
    batch (a re-pulled cursor range, a replayed relay delivery) must be a
    total no-op on the balance."""
    admin, cid, pid = _new_shop(price=10.0, initial_stock=200)
    admin.post(f'{API}/products/{pid}/stock-adjust', json={'quantity': 30, 'reason': 'Restock'})

    service_a = SyncService(client_factory=lambda: relay, get_conn=get_retail_conn)
    service_b = SyncService(client_factory=lambda: relay, get_conn=install_b,
                             local_company_id_provider=lambda: 'install-b-company')
    service_a.push_once()

    b_conn = install_b()
    try:
        since = service_b.read_cursor(b_conn)
        result = relay.pull(since)
        service_b.apply_pull_result(b_conn, result)
        b_conn.commit()
        b_branch = b_conn.execute("SELECT id FROM branches WHERE company_id='install-b-company'").fetchone()
        bal_after_first = b_conn.execute(
            "SELECT quantity_on_hand FROM inventory_balances WHERE company_id='install-b-company' "
            "AND product_id=? AND branch_id=?", (pid, b_branch['id'])
        ).fetchone()['quantity_on_hand']
        assert bal_after_first == 230.0  # 200 opening + 30 adjustment

        # Re-apply the IDENTICAL result a second time -- same events, same
        # `since`, exactly what a replayed delivery or a re-pulled cursor
        # range looks like on the wire.
        service_b.apply_pull_result(b_conn, result)
        b_conn.commit()
        bal_after_replay = b_conn.execute(
            "SELECT quantity_on_hand FROM inventory_balances WHERE company_id='install-b-company' "
            "AND product_id=? AND branch_id=?", (pid, b_branch['id'])
        ).fetchone()['quantity_on_hand']
        assert bal_after_replay == bal_after_first == 230.0, \
            f"stock doubled on replay: {bal_after_first} -> {bal_after_replay}"

        movement_rows = b_conn.execute(
            "SELECT COUNT(*) AS c FROM inventory_movements WHERE company_id='install-b-company'"
        ).fetchone()['c']
        assert movement_rows == 2  # opening_stock + the one adjustment, NOT duplicated

        drift = stock_reconciliation.compute_drift(b_conn, 'install-b-company')
        assert drift == []
    finally:
        b_conn.close()


def test_a_movement_for_an_unresolved_product_is_quarantined_not_wedged(install_b, relay):
    """HAZARD 2. `inventory_movements` declares exactly one real FK --
    product_id -> products(id). A movement whose product hasn't arrived yet
    (Owner's push-side quarantine can skip a malformed product event just as
    easily as a malformed sale/return -- see sync_service.py's own sale_item
    branch comment) must be parked, not left to raise and wedge the batch."""
    admin, cid, pid = _new_shop(price=10.0, initial_stock=50)
    # Simulate the product event never having arrived: delete it from A's
    # own outbox before pushing, so B only ever receives the branch and the
    # movement -- an orphan by construction, the same technique
    # retail_money_sync_test.py's own orphan tests use.
    conn = get_retail_conn()
    conn.execute("DELETE FROM sync_outbox WHERE entity_type='product'")
    conn.commit()
    conn.close()

    service_a = SyncService(client_factory=lambda: relay, get_conn=get_retail_conn)
    service_b = SyncService(client_factory=lambda: relay, get_conn=install_b,
                             local_company_id_provider=lambda: 'install-b-company')
    service_a.push_once()
    service_b.pull_once()  # must not raise

    b_conn = install_b()
    try:
        movements = b_conn.execute("SELECT * FROM inventory_movements").fetchall()
        assert len(movements) == 0, "the orphaned movement was applied despite its product never arriving"
        quarantined = b_conn.execute(
            "SELECT * FROM sync_apply_quarantine WHERE entity_type='inventory_movement'"
        ).fetchall()
        assert len(quarantined) == 1
        assert quarantined[0]['reason'] == 'missing_parent:product'

        # Now the product arrives (a later batch, an operator replaying it
        # from Owner's console) -- resolves in the SAME pull it lands in.
        conn = get_retail_conn()
        product_events = [dict(r) for r in conn.execute(
            "SELECT * FROM sync_outbox WHERE entity_type='product'"
        ).fetchall()]
        conn.close()
        # The product event was deleted from A's outbox above -- re-queue it
        # directly onto the relay to simulate "arrives on a later pull",
        # matching retail_money_sync_test.py's own orphan-resolution tests.
        relay.push([{
            "id": str(uuid.uuid4()), "entity_type": "product", "entity_id": pid,
            "event_type": "create", "created_at": "2026-01-01T00:00:00+00:00",
            "payload": {
                'id': pid, 'sku': f'ORPHAN-{pid[:8]}', 'barcode': '', 'name': 'Orphan Widget',
                'category_id': None, 'supplier_id': None, 'cost_price': 5, 'sell_price': 10,
                'tax_rate': 0, 'unit': 'pcs', 'reorder_level': 5, 'reorder_method': 'none', 'status': 'active',
            },
        }])
        service_b.pull_once()

        movements = b_conn.execute("SELECT * FROM inventory_movements WHERE product_id=?", (pid,)).fetchall()
        assert len(movements) == 1
        quarantined_after = b_conn.execute(
            "SELECT * FROM sync_apply_quarantine WHERE entity_type='inventory_movement'"
        ).fetchall()
        assert len(quarantined_after) == 0, "resolved movement was not cleared from quarantine"
    finally:
        b_conn.close()


def test_a_branch_rename_propagates_do_update_not_do_nothing(install_b, relay):
    """HAZARD 3, mutable half: renaming a branch on the device that owns it
    must converge on every other device -- the SAME wire uid maps to the
    SAME local row on B, updated in place, never a second row.

    REPAIRED: the branch this test renames must be one that actually
    SYNCS -- `_new_shop`'s own product-creation self-heal deliberately
    queues no `branch` event any more (see `_default_branch`'s "Wave B,
    CORRECTED" comment), so this now creates the branch through the
    OPERATOR path (`POST /branches`, via `create_branch` below), the same
    one `test_create_branch_route_queues_a_branch_create_event` already
    proves emits a real `branch`/create event. The rename mechanics and
    assertions this test actually exists to prove (`DO UPDATE`, not a
    second row) are unchanged."""
    admin, cid, pid = _new_shop()
    r = admin.post(f'{API}/branches', json={
        'name': 'Uptown Branch', 'address': '1 Market Rd', 'phone': '555-0100'})
    assert r.status_code == 200, r.get_json()
    branch_events = [e for e in _outbox('branch') if e['payload']['name'] == 'Uptown Branch']
    assert len(branch_events) == 1
    branch_uid = branch_events[0]['payload']['uid']

    service_a = SyncService(client_factory=lambda: relay, get_conn=get_retail_conn)
    service_b = SyncService(client_factory=lambda: relay, get_conn=install_b,
                             local_company_id_provider=lambda: 'install-b-company')
    service_a.push_once()
    service_b.pull_once()

    # A "rename" -- hand-built, since no PATCH /branches/<id> route exists in
    # this product today (see sync_service.py's module docstring, wave B
    # note) -- queued exactly the shape a future rename route would queue.
    conn = get_retail_conn()
    conn.execute(
        "INSERT INTO sync_outbox (id, entity_type, entity_id, event_type, payload, created_at) VALUES (?,?,?,?,?,?)",
        (str(uuid.uuid4()), 'branch', branch_uid, 'update',
         json.dumps({'uid': branch_uid, 'name': 'Renamed Branch', 'address': 'New Address', 'phone': '999',
                     'status': 'active'}),
         '2026-01-01T00:00:00+00:00'),
    )
    conn.commit()
    conn.close()
    service_a.push_once()
    service_b.pull_once()

    b_conn = install_b()
    try:
        rows = b_conn.execute("SELECT * FROM branches WHERE uid=?", (branch_uid,)).fetchall()
        assert len(rows) == 1, f"a rename created a SECOND row instead of updating the one that exists: {rows}"
        assert rows[0]['name'] == 'Renamed Branch'
        assert rows[0]['address'] == 'New Address'
        assert rows[0]['company_id'] == 'install-b-company'  # B's own, never A's
    finally:
        b_conn.close()


def test_an_inventory_movement_update_event_on_an_existing_uid_never_rewrites_it(install_b, relay):
    """HAZARD 3, immutable half, scenario 1 of 2: unlike `branch`,
    `inventory_movement` never accepts `update`/`delete` -- a stock ledger
    fact is corrected by a NEW movement (an adjustment), never edited or
    removed in place.

    RENAMED (was `..._update_or_delete_event_is_refused`) to say precisely
    what this scenario actually proves and what it does NOT: this sends an
    "update" for a movement `uid` that has ALREADY been "create"d, and
    `ON CONFLICT(uid) ... DO NOTHING` alone -- a completely different
    mechanism from the `if event_type != "create": return True` guard in
    sync_service.py's `_apply_event` -- is enough to protect an EXISTING
    row regardless of event_type. Mutation-proven: with that guard removed
    entirely, this exact scenario stays green, because the INSERT still
    hits the same uid's conflict and DO NOTHING still fires. The guard's
    real job only shows up when there is no existing row to conflict
    with -- see `test_a_bare_inventory_movement_update_event_with_no_prior_create_is_refused_not_fabricated`
    in `retail_stock_sync_apply_hardening_test.py` for that decisive case,
    the one actually promoted from the adversarial verifier's mutation
    proof. This scenario is kept because "an update on a real row can't
    rewrite it" is still a real property worth guarding -- just not the
    one this test's old name implied it was proving."""
    admin, cid, pid = _new_shop(price=10.0, initial_stock=200)
    service_a = SyncService(client_factory=lambda: relay, get_conn=get_retail_conn)
    service_b = SyncService(client_factory=lambda: relay, get_conn=install_b,
                             local_company_id_provider=lambda: 'install-b-company')
    service_a.push_once()
    service_b.pull_once()

    b_conn = install_b()
    try:
        movement_uid = b_conn.execute(
            "SELECT uid FROM inventory_movements WHERE company_id='install-b-company'"
        ).fetchone()['uid']
    finally:
        b_conn.close()

    # An attacker/bug on the relay side sends an "update" attempting to
    # rewrite this movement's quantity -- e.g. turning a 200-unit opening
    # declaration into a 200000-unit one.
    conn = get_retail_conn()
    conn.execute(
        "INSERT INTO sync_outbox (id, entity_type, entity_id, event_type, payload, created_at) VALUES (?,?,?,?,?,?)",
        (str(uuid.uuid4()), 'inventory_movement', movement_uid, 'update',
         json.dumps({'uid': movement_uid, 'product_id': pid, 'quantity': 200000, 'movement_type': 'opening_stock'}),
         '2026-01-01T00:00:00+00:00'),
    )
    conn.commit()
    conn.close()
    service_a.push_once()
    service_b.pull_once()  # must not raise, must not rewrite

    b_conn = install_b()
    try:
        row = b_conn.execute(
            "SELECT quantity FROM inventory_movements WHERE uid=?", (movement_uid,)
        ).fetchone()
        assert row['quantity'] == 200.0, \
            f"an update event REWROTE stock history: quantity is now {row['quantity']}"
    finally:
        b_conn.close()


def test_actor_terminal_and_created_at_utc_are_preserved_from_the_originating_device(install_b, relay):
    """Attributing another till's stock adjustment to the local cashier would
    be a fabricated audit fact -- `inventory_movements` is in
    RETAIL_ACTOR_TABLES for exactly this reason."""
    admin, cid, pid = _new_shop(price=10.0, initial_stock=100)
    original = _outbox('inventory_movement')[0]['payload']

    service_a = SyncService(client_factory=lambda: relay, get_conn=get_retail_conn)
    service_b = SyncService(client_factory=lambda: relay, get_conn=install_b,
                             local_company_id_provider=lambda: 'install-b-company')
    service_a.push_once()
    service_b.pull_once()

    b_conn = install_b()
    try:
        row = b_conn.execute(
            "SELECT actor_user_uid, terminal_id, created_at_utc FROM inventory_movements "
            "WHERE company_id='install-b-company'"
        ).fetchone()
    finally:
        b_conn.close()
    assert row['terminal_id'] == original['terminal_id']
    assert row['created_at_utc'] == original['created_at_utc']
    assert row['actor_user_uid'] == original['actor_user_uid']


def test_a_new_branch_arriving_does_not_disturb_an_open_cash_drawer(install_b, relay):
    """A branch created/renamed by sync must never touch `cash_sessions` --
    Phase 4's whole guarantee (one open drawer per terminal) is on the
    line if it does."""
    b_conn = install_b()
    try:
        b_conn.execute(
            "INSERT INTO branches (company_id,name,address,phone,uid) VALUES (?,?,?,?,?)",
            ('install-b-company', 'B Main', '', '', str(uuid.uuid4())))
        b_bid = b_conn.execute("SELECT id FROM branches WHERE company_id='install-b-company'").fetchone()['id']
        session_id = str(uuid.uuid4())
        b_conn.execute(
            "INSERT INTO cash_sessions (id, company_id, branch_id, terminal_id, opening_float, status, opened_at) "
            "VALUES (?,?,?,?,?,'open',?)",
            (session_id, 'install-b-company', b_bid, 'till-99', 100.0, '2026-01-01T00:00:00+00:00'),
        )
        b_conn.commit()
        before = dict(b_conn.execute("SELECT * FROM cash_sessions WHERE id=?", (session_id,)).fetchone())
    finally:
        b_conn.close()

    admin, cid, pid = _new_shop()
    service_a = SyncService(client_factory=lambda: relay, get_conn=get_retail_conn)
    service_b = SyncService(client_factory=lambda: relay, get_conn=install_b,
                             local_company_id_provider=lambda: 'install-b-company')
    service_a.push_once()
    service_b.pull_once()

    b_conn = install_b()
    try:
        after = dict(b_conn.execute("SELECT * FROM cash_sessions WHERE id=?", (session_id,)).fetchone())
    finally:
        b_conn.close()
    assert after == before, f"an arriving branch disturbed the open cash session: {before} -> {after}"


# ─────────────────────────────────────────────────────────────────────────────
# 3. THE DECISIVE TESTS -- two REAL devices, separate OS processes, each
#    selling and adjusting stock through the real Flask routes.
# ─────────────────────────────────────────────────────────────────────────────

def test_two_real_devices_selling_and_adjusting_converge_to_zero_drift_both_sides(tmp_path):
    """THE decisive test the phase brief names explicitly: after syncing,
    `compute_drift` -- the actual Phase 3 gate function, not "balances look
    right" -- must return zero on BOTH devices.

    Device A originates a shop (a product, an opening stock). Device B is a
    genuinely fresh install (`bootstrap`: an admin and a company, no product
    of its own) that pulls A's catalogue, THEN rings its own sale and posts
    its own adjustment against the SAME product -- a real second till, not a
    receiver. A then pulls B's writes back. Both directions, both devices
    real OS processes the whole way through.

    REPAIRED: "the SAME product at the SAME branch" requires a branch B can
    actually resolve to A's -- and `create_shop`'s own opening-stock self-
    heal (`_default_branch`) deliberately queues NO `branch` sync event any
    more (retail_api.py, "Wave B, CORRECTED": broadcasting a self-heal is
    the exact duplicate-identity bug this phase fixes). Two devices that
    each self-heal independently can never be proven to agree on "the same
    place". So A now provisions its stock through an OPERATOR-created
    branch (`create_branch`, via `POST /branches`) instead -- the only kind
    of branch two devices can ever agree names the same physical location --
    and every unit of stock in this test is filed against it explicitly. The
    convergence arithmetic (1000 - 12 + 25 - 7 = 1006) and every assertion
    below are otherwise unchanged.
    """
    device_a = tmp_path / "device_a"
    device_b = tmp_path / "device_b"
    relay_db = tmp_path / "relay.db"

    [shop] = run_device(PY, device_a, relay_db, [
        {"op": "create_shop", "price": 40.0, "initial_stock": 0},
    ])
    cid_a, pid = shop["company_id"], shop["product_id"]

    _, branch = run_device(PY, device_a, relay_db, [
        {"op": "login", "email": shop["email"], "password": shop["password"]},
        {"op": "create_branch", "name": "Shared Branch", "address": "1 Market Rd", "phone": "555-0100"},
    ])
    assert branch["status_code"] == 200, branch["body"]
    a_branch_id, a_branch_uid = branch["branch_id"], branch["branch_uid"]

    run_device(PY, device_a, relay_db, [
        {"op": "login", "email": shop["email"], "password": shop["password"]},
        {"op": "adjust_stock", "product_id": pid, "quantity": 1000, "reason": "Opening stock, shared branch",
         "branch_id": a_branch_id},
        {"op": "push"},
    ])

    [bootstrap] = run_device(PY, device_b, relay_db, [{"op": "bootstrap"}])
    cid_b = bootstrap["company_id"]
    run_device(PY, device_b, relay_db, [{"op": "pull"}])

    # B now has A's product and A's OPERATOR branch locally (own company_id,
    # per the cross-device company_id fix) -- resolve B's own local branch id
    # for A's branch_uid before selling against it.
    [lookup] = run_device(PY, device_b, relay_db, [{"op": "branch_by_uid", "uid": a_branch_uid}])
    b_branch_id = lookup["branch"]["id"]
    assert lookup["branch"]["name"] == "Shared Branch"
    assert lookup["branch"]["company_id"] == cid_b  # B's own, never A's

    # B rings a real sale and posts a real adjustment -- a second till doing
    # its own business, not a passive receiver. `login` first: this is a
    # FRESH subprocess (a real relaunch), so the client session from
    # `bootstrap`'s own invocation is gone -- exactly as it would be for a
    # real device restarted between actions.
    _, sell_result, adjust_result = run_device(PY, device_b, relay_db, [
        {"op": "login", "email": bootstrap["email"], "password": bootstrap["password"]},
        {"op": "sell", "product_id": pid, "quantity": 12, "branch_id": b_branch_id},
        {"op": "adjust_stock", "product_id": pid, "quantity": 25, "reason": "Stocktake found extra units",
         "branch_id": b_branch_id},
    ])
    assert sell_result["status_code"] == 200, sell_result["body"]
    assert adjust_result["status_code"] == 200, adjust_result["body"]
    run_device(PY, device_b, relay_db, [{"op": "push"}])

    # A pulls B's writes back.
    run_device(PY, device_a, relay_db, [{"op": "pull"}])

    # A ALSO keeps doing its own business after receiving B's batch -- proves
    # this isn't a one-shot convergence but an ongoing, bidirectional one.
    _, a_sell = run_device(PY, device_a, relay_db, [
        {"op": "login", "email": shop["email"], "password": shop["password"]},
        {"op": "sell", "product_id": pid, "quantity": 7, "branch_id": a_branch_id},
    ])
    assert a_sell["status_code"] == 200, a_sell["body"]
    run_device(PY, device_a, relay_db, [{"op": "push"}])
    run_device(PY, device_b, relay_db, [{"op": "pull"}])

    # Expected balance, computed independently of either device's own report:
    #   1000 opening - 12 (B's sale) + 25 (B's adjustment) - 7 (A's sale) = 1006
    expected = 1000 - 12 + 25 - 7

    [bal_a] = run_device(PY, device_a, relay_db, [
        {"op": "get_balance", "company_id": cid_a, "product_id": pid, "branch_id": a_branch_id},
    ])
    assert bal_a["quantity_on_hand"] == expected, f"A's balance is wrong: {bal_a}"

    [bal_b] = run_device(PY, device_b, relay_db, [
        {"op": "get_balance", "company_id": cid_b, "product_id": pid, "branch_id": b_branch_id},
    ])
    assert bal_b["quantity_on_hand"] == expected, f"B's balance is wrong: {bal_b}"

    # THE decisive assertion: the real Phase 3 gate function, zero on BOTH.
    [drift_a] = run_device(PY, device_a, relay_db, [{"op": "compute_drift", "company_id": cid_a}])
    assert drift_a["drift"] == [], f"A's ledger disagrees with its own cache: {drift_a['drift']}"

    [drift_b] = run_device(PY, device_b, relay_db, [{"op": "compute_drift", "company_id": cid_b}])
    assert drift_b["drift"] == [], f"B's ledger disagrees with its own cache: {drift_b['drift']}"


def test_branch_uid_resolution_stops_falling_back_once_the_branch_has_synced(tmp_path):
    """HAZARD 2's other half: `_resolve_branch_id`'s tier-2 fallback existed
    because `branch` never synced. Now that it does, the ORDINARY case is
    tier-1 resolving on the very first pull that carries both the branch and
    a row naming it -- proved with zero fallback warnings, across two real
    processes.

    REPAIRED: `shop["branch_uid"]` (from `create_shop`'s own opening-stock
    self-heal) can never be one of those -- `_default_branch` deliberately
    queues no `branch` sync event any more (retail_api.py, "Wave B,
    CORRECTED"), so it is a branch tier 1 will NEVER resolve, on any pull,
    ever. Tier 1 resolving on the very first pull is still true, but only
    for a branch that genuinely syncs -- an OPERATOR-created one
    (`create_branch`). A's stock is filed against that branch instead of
    the self-healed default; the arithmetic (500 - 3 = 497) is unchanged.
    """
    device_a = tmp_path / "device_a"
    device_b = tmp_path / "device_b"
    relay_db = tmp_path / "relay.db"

    [shop] = run_device(PY, device_a, relay_db, [
        {"op": "create_shop", "price": 15.0, "initial_stock": 0},
    ])
    _, branch = run_device(PY, device_a, relay_db, [
        {"op": "login", "email": shop["email"], "password": shop["password"]},
        {"op": "create_branch", "name": "Operator Branch", "address": "1 Market Rd", "phone": "555-0100"},
    ])
    assert branch["status_code"] == 200, branch["body"]
    op_branch_id, op_branch_uid = branch["branch_id"], branch["branch_uid"]

    run_device(PY, device_a, relay_db, [
        {"op": "login", "email": shop["email"], "password": shop["password"]},
        {"op": "adjust_stock", "product_id": shop["product_id"], "quantity": 500,
         "reason": "Stock into operator branch", "branch_id": op_branch_id},
        {"op": "sell", "product_id": shop["product_id"], "quantity": 3, "branch_id": op_branch_id},
        {"op": "push"},
    ])

    [bootstrap] = run_device(PY, device_b, relay_db, [{"op": "bootstrap"}])
    run_device(PY, device_b, relay_db, [{"op": "pull"}])

    [lookup] = run_device(PY, device_b, relay_db, [{"op": "branch_by_uid", "uid": op_branch_uid}])
    assert lookup["branch"] is not None, "tier 1 did not resolve on the very first pull"
    b_bid = lookup["branch"]["id"]

    [bal] = run_device(PY, device_b, relay_db, [
        {"op": "get_balance", "company_id": bootstrap["company_id"], "product_id": shop["product_id"],
         "branch_id": b_bid},
    ])
    # 500 stocked in - 3 sold, filed under the CORRECT synced (operator)
    # branch, not a self-healed default one -- if the fallback had fired
    # instead, this balance would be sitting under a DIFFERENT (self-healed)
    # branch_id and this lookup (by A's own real, synced branch_uid) would
    # show nothing at all.
    assert bal["quantity_on_hand"] == 497.0


def test_a_product_that_never_arrives_quarantines_the_movement_across_real_processes(tmp_path):
    """HAZARD 2, proved through two real processes rather than the single-
    process `install_b` version above -- the parked event must survive
    exactly as parked across a genuine process boundary, and resolve once
    the product legitimately arrives on a later pull.

    A's own outbox is emptied of its `product` event BEFORE pushing (the
    device action `outbox_delete_entity_type`), reproducing the exact
    real-world cause named throughout sync_service.py -- Owner's push-side
    quarantine (681b0fa) can skip a malformed product event while still
    relaying its already-valid children -- without needing Owner itself:
    B genuinely never receives the product in this batch, only the branch
    and the movement that depends on it.

    REPAIRED: proving "the branch event was NOT blocked by the orphaned
    product" requires a batch that actually CONTAINS a branch event.
    `create_shop`'s own opening-stock self-heal (`_default_branch`)
    deliberately queues none any more (retail_api.py, "Wave B, CORRECTED"),
    so this now files the opening stock through an OPERATOR-created branch
    (`create_branch`, via `adjust_stock` against it) instead of relying on
    `create_shop`'s `initial_stock` -- the only path that still produces a
    genuine `branch`/create event alongside the `inventory_movement`/create
    event this test orphans. The quarantine mechanics and the final balance
    (60.0) are unchanged.
    """
    device_a = tmp_path / "device_a"
    device_b = tmp_path / "device_b"
    relay_db = tmp_path / "relay.db"

    [shop] = run_device(PY, device_a, relay_db, [
        {"op": "create_shop", "price": 8.0, "initial_stock": 0},
    ])
    pid = shop["product_id"]

    _, branch = run_device(PY, device_a, relay_db, [
        {"op": "login", "email": shop["email"], "password": shop["password"]},
        {"op": "create_branch", "name": "Orphan Test Branch", "address": "", "phone": ""},
    ])
    assert branch["status_code"] == 200, branch["body"]
    op_branch_id, op_branch_uid = branch["branch_id"], branch["branch_uid"]

    run_device(PY, device_a, relay_db, [
        {"op": "login", "email": shop["email"], "password": shop["password"]},
        {"op": "adjust_stock", "product_id": pid, "quantity": 60, "reason": "Opening stock, orphan test",
         "branch_id": op_branch_id},
        {"op": "outbox_delete_entity_type", "entity_type": "product"},
        {"op": "push"},
    ])

    [bootstrap] = run_device(PY, device_b, relay_db, [{"op": "bootstrap"}])
    run_device(PY, device_b, relay_db, [{"op": "pull"}])  # must not raise

    cnt = run_device(PY, device_b, relay_db, [{"op": "quarantine_count"}])[0]["count"]
    assert cnt == 1, "the orphaned movement was not parked"
    b_bal = run_device(PY, device_b, relay_db, [
        {"op": "branch_by_uid", "uid": op_branch_uid},
    ])[0]["branch"]
    assert b_bal is not None  # the branch event was NOT blocked by the orphaned product

    # The product legitimately arrives on a later pull (an operator replaying
    # it from Owner's console, or simply the next scheduled sync cycle that
    # happens to carry it) -- re-inject it directly onto the shared relay,
    # matching the harness's own file-relay contract.
    conn = sqlite3.connect(str(relay_db))
    conn.execute(
        "INSERT INTO relay_events (payload) VALUES (?)",
        (json.dumps({
            "id": str(uuid.uuid4()), "entity_type": "product", "entity_id": pid,
            "event_type": "create", "created_at": "2026-01-01T00:00:00+00:00",
            "payload": {
                'id': pid, 'sku': f'ORPHANRT-{pid[:8]}', 'barcode': '', 'name': 'Orphan RT Widget',
                'category_id': None, 'supplier_id': None, 'cost_price': 4, 'sell_price': 8,
                'tax_rate': 0, 'unit': 'pcs', 'reorder_level': 5, 'reorder_method': 'none', 'status': 'active',
            },
        }),),
    )
    conn.commit()
    conn.close()

    run_device(PY, device_b, relay_db, [{"op": "pull"}])
    cnt_after = run_device(PY, device_b, relay_db, [{"op": "quarantine_count"}])[0]["count"]
    assert cnt_after == 0, "resolved movement was not cleared from quarantine"

    bal = run_device(PY, device_b, relay_db, [
        {"op": "get_balance", "company_id": bootstrap["company_id"], "product_id": pid,
         "branch_id": b_bal["id"]},
    ])[0]
    assert bal["quantity_on_hand"] == 60.0, "the parked movement was not applied once its product arrived"


def test_two_devices_that_each_self_heal_before_first_pull_converge_to_one_branch_row_each(tmp_path):
    """THE decisive proof for the identity bug this phase's fix closes (see
    retail_api.py's `_default_branch`, "Wave B, CORRECTED", and this file's
    own module docstring). Two devices that EACH self-heal their own
    default branch before ever exchanging anything must NOT end up with two
    permanently-unmerged 'Main Branch' rows once they converge.

    THE HARNESS BLIND SPOT THIS CLOSES: `_stock_sync_device.py`'s own
    `bootstrap` op docstring says plainly that `_default_branch` is never
    called by anything it does -- by design, it is a pure receiver with no
    product of its own. Every stock-sync test that used `bootstrap` alone
    always pulled BEFORE writing anything locally, so device B never
    independently minted its own branch in any of them -- this was the
    THIRD time in this project a fixture manufactured exactly the state
    that hides the bug wave A's own fixtures did this twice already (see
    CLAUDE.md's "Team dynamics" / verification-failure-patterns history).
    This test closes it: B does a real local write (`create_product`,
    which calls `_default_branch` unconditionally -- see that route's own
    comment, "the normal way an operator provisions one") BEFORE its first
    pull, exactly like a genuinely fresh install that starts selling before
    its first successful sync tick would.

    Neither device ever creates an OPERATOR branch here -- both only ever
    self-heal -- so with the fix in place, NEITHER `branch` event exists to
    cross the wire at all, and each device's own `branches` table holds
    exactly the one row it minted for itself. See this file's own
    `test_create_product_self_heal_queues_no_branch_event_but_still_queues_the_inventory_movement`
    for the single-process version of the same guarantee, and
    `retail_stock_sync_apply_hardening_test.py` for the apply-side unit
    proofs this end-to-end one complements.
    """
    device_a = tmp_path / "device_a"
    device_b = tmp_path / "device_b"
    relay_db = tmp_path / "relay.db"

    [shop] = run_device(PY, device_a, relay_db, [
        {"op": "create_shop", "price": 12.0, "initial_stock": 100},
    ])
    cid_a = shop["company_id"]
    run_device(PY, device_a, relay_db, [{"op": "push"}])

    # B self-heals its OWN default branch via a real local write -- BEFORE
    # its first pull, the exact ordering that reproduced the duplicate-
    # branch bug end to end (see retail_api.py's own reproduction note).
    bootstrap, b_product = run_device(PY, device_b, relay_db, [
        {"op": "bootstrap"},
        {"op": "create_product", "name": "B Own Widget", "initial_stock": 10},
    ])
    cid_b = bootstrap["company_id"]
    assert b_product["status_code"] == 200, b_product["body"]

    run_device(PY, device_b, relay_db, [{"op": "push"}])
    run_device(PY, device_b, relay_db, [{"op": "pull"}])
    run_device(PY, device_a, relay_db, [{"op": "pull"}])

    [count_a] = run_device(PY, device_a, relay_db, [{"op": "branch_count", "company_id": cid_a}])
    assert count_a["count"] == 1, (
        f"device A ended up with {count_a['count']} branch rows for its own company -- "
        "a self-healed branch must never duplicate across a converged sync"
    )

    [count_b] = run_device(PY, device_b, relay_db, [{"op": "branch_count", "company_id": cid_b}])
    assert count_b["count"] == 1, (
        f"device B ended up with {count_b['count']} branch rows for its own company -- "
        "a self-healed branch must never duplicate across a converged sync"
    )
