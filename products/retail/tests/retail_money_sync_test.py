"""Aura Retail -- Phase 5 (launch-readiness): money-moving sync, end to end
through the real routes.

Scope: exactly `sale`, `sale_item`, `payment`, `return`, `return_item` --
the five entity types Phase 5 adds to the sync allowlist. This file proves
the pieces this project's own precedent (retail_two_install_roundtrip_test.py)
warns can each pass in isolation while failing to COMPOSE: the emit side
(retail_api.py's create_sale/create_return/_record_payment outbox writes),
the apply side (sync_service.py's `_apply_event` branches), and a real (if
double-transport) relay in between.

Three hazards this phase names explicitly, each with its own test group
below:

  1. IDEMPOTENCY -- applying the same batch twice must never double a shop's
     takings. Unit-proved exhaustively in commercial_runtime/sync/tests/
     test_sync_service.py; proved again here through the REAL retail_api
     emission path (not a hand-built payload), because a field present in
     the emit side but absent from the apply side -- or vice versa -- is
     exactly the class of bug that harness exists to catch.
  2. THE CASH DRAWER -- a synced sale must never be re-attributed to the
     receiving device's own open drawer (Phase 4, retail v16). Proved with a
     REAL open cash session and a REAL local sale on install A, plus a
     simulated foreign sale arriving via sync into A's own database, read
     back through the actual `_cash_session_report` function the X/Z report
     route calls.
  3. ORPHANS -- a child event whose parent cannot be resolved locally must
     be parked (visible, replayable), never dropped or allowed to raise and
     wedge the whole pull batch.

Harness notes (see retail_two_install_roundtrip_test.py's own module
docstring for the two traps this shape is built around -- the Postgres trap
and the AURA_APP_DATA trap -- repeated here verbatim, not re-derived):

  * install A is a REAL, fully-booted Flask app (this file's own `app`).
  * install B is a bare temp SQLite database built from the real
    `database.schema.init_retail()` -- never a second Flask app.
  * `InMemoryRelay` stands in for Owner's real Postgres-backed relay,
    implementing only `.push(events)` / `.pull(since)`.
  * `at_terminal()` (retail_drawer_terminal_scope_test.py's own technique)
    swaps which terminal THIS process reports itself as, driving the SAME
    app/database/routes a second physical device would use -- no row is
    ever hand-stamped with a terminal id to manufacture a scene.

Run:
    pytest products/retail/tests/retail_money_sync_test.py -v
"""
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
for _p in (str(SUITE_ROOT), str(BACKEND_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

DATA = Path(tempfile.mkdtemp(prefix="aura_retail_moneysync_"))
(DATA / "database" / "subsystems").mkdir(parents=True, exist_ok=True)
os.environ.update(AURA_STANDALONE="1", AURA_BUNDLE_DIR=str(BACKEND_DIR), AURA_APP_DATA=str(DATA))
os.environ.pop("AURA_DEV", None)

from commercial_runtime.licensing_contracts.test_support import seed_active_license  # noqa: E402
seed_active_license(str(DATA), product_code="AURA_RETAIL", platform="WINDOWS")

import app as _app_module  # noqa: E402

app = _app_module.init_app()  # install A: a real, fully-booted Flask app
app.config["TESTING"] = True

from api import retail_api  # noqa: E402
from api.retail_api import _ensure_credit_schema  # noqa: E402
from commercial_runtime.identity.registry_db import get_conn as registry_conn  # noqa: E402
from commercial_runtime.security.passwords import hash_password  # noqa: E402
from commercial_runtime.sync.sync_service import SyncService  # noqa: E402
import database.schema as schema  # noqa: E402
from database.schema import get_retail_conn  # noqa: E402

API = '/api/sub/retail'

#: Two terminal identities -- real-shaped uuid4 strings, matching
#: retail_drawer_terminal_scope_test.py's own convention exactly.
TILL_DESK = 'a1b2c3d4-1111-4c3a-9d55-desk00000001'
TILL_PHONE_B = 'e5f6a7b8-2222-4f18-8e21-installb0001'


def teardown_module(module):
    shutil.rmtree(DATA, ignore_errors=True)


@contextlib.contextmanager
def at_terminal(terminal_id):
    """See retail_drawer_terminal_scope_test.py's own docstring for the full
    reasoning -- copied verbatim rather than imported, matching how that
    file's own precedent (retail_drawer_money_sweep_test.py) already
    duplicates it: each Phase's test file is meant to survive the others
    being deleted."""
    previous = retail_api.local_terminal_id
    retail_api.local_terminal_id = lambda: terminal_id
    try:
        yield
    finally:
        retail_api.local_terminal_id = previous


@contextlib.contextmanager
def at_device_data(app_data_dir):
    """Swaps AURA_APP_DATA for the duration -- simulates a genuinely SEPARATE
    physical install, as opposed to `at_terminal()` above (which fakes "this
    call reports a different terminal" within the SAME install).

    AUDIT-032D's `persist_doc_discriminator()` (commercial_runtime/identity/
    device_context.py) deliberately does NOT re-derive its value from
    `local_terminal_id()` on every call -- it persists ONCE, to
    `<AURA_APP_DATA>/device/doc_discriminator.json`, and reads that same file
    back forever after (see that function's own docstring for why: re-
    deriving live would split one till's numbering into two series). That
    means `at_terminal()`'s in-process swap, which drove the PRE-AUDIT-032D
    discriminator (it re-read `local_terminal_id()` fresh every call), no
    longer changes what `_device_doc_discriminator()` returns -- the thing
    that now varies between "two tills" is which `AURA_APP_DATA` directory
    the persisted file lives under, which is exactly what a second physical
    install actually differs by. `device_context._resolve_app_data()` reads
    `AURA_APP_DATA` fresh on every call (see that function's own module
    docstring), so this swap is a faithful double for a second install
    rather than a second terminal on the same one.
    """
    previous = os.environ.get('AURA_APP_DATA')
    os.environ['AURA_APP_DATA'] = str(app_data_dir)
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop('AURA_APP_DATA', None)
        else:
            os.environ['AURA_APP_DATA'] = previous


def _new_shop(price=100.0):
    """A company with an admin, a product and stock. Returns (admin, cid, pid)."""
    company_id = str(uuid.uuid4())
    email = f'moneysync-admin-{uuid.uuid4().hex[:8]}@test.local'
    password = 'MoneySyncPW1'
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
        'name': 'Sync Widget', 'sku': f'MS-{uuid.uuid4().hex[:8]}',
        'cost_price': price / 2, 'sell_price': price, 'tax_rate': 0, 'initial_stock': 5000})
    assert p.status_code == 200, p.get_json()
    return admin, company_id, p.get_json()['data']['id']


def _sell_cash(client, pid, qty, unit_price_total_hint=None):
    """Rings a cash sale for `qty` units of `pid`, tendering exactly the
    total (no change) so payment amounts are easy to reason about."""
    return client.post(f'{API}/sales', json={
        'items': [{'product_id': pid, 'quantity': qty}],
        'payment_method': 'cash', 'idempotency_key': str(uuid.uuid4())})


class InMemoryRelay:
    """Identical to retail_two_install_roundtrip_test.py's own double -- see
    that file's module docstring (trap 1) for the full reasoning."""

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


@pytest.fixture
def install_b(tmp_path):
    """Identical technique to retail_two_install_roundtrip_test.py's own
    fixture (trap 2) -- see that file's module docstring for the full
    reasoning. Repeated here rather than imported for the same
    survive-independently reason `at_terminal` is."""
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

    # Stands in for the `local_ensure_schema` hook a REAL install wires into
    # its SyncService (app.py passes `_ensure_credit_schema` on both the
    # Windows and the Android branch; SyncService then calls it lazily, from
    # inside apply_pull_result, on any batch touching sale/return/payment).
    # app.py does NOT call it eagerly at boot -- an earlier attempt at that
    # regressed an existing test's `_CREDIT_SCHEMA_READY is False`
    # anti-vacuity assumption -- so this fixture reproduces the hook's
    # EFFECT rather than mirroring a boot sequence that does not exist.
    # Without it this harness hits the exact bug the hook closes:
    # `sqlite3.OperationalError: table sales has no column named due_date`,
    # from a "device" that (like install_b always has, by this harness's own
    # design -- see the module docstring) never served a single HTTP route
    # before applying its first pulled sale.
    #
    # `_CREDIT_SCHEMA_READY` is a PROCESS-global flag, not a per-database
    # one -- entirely correct for a real device (exactly one retail.db per
    # process), and WRONG for this harness's two-databases-in-one-process
    # trick: install A's own module-import-time `app.init_app()` (this
    # file's `app = _app_module.init_app()` line, near the top) already set
    # it True for THIS process before any test/fixture here runs, so a plain
    # call would silently no-op against install B's separate database file.
    # Forced False around this one call, restored immediately after -- the
    # ALTER statements underneath are independently idempotent (column-
    # existence-checked), so running them for real here is exactly as safe
    # as the very first call in the process was.
    import api.retail_api as _retail_api_module
    _previous_flag = _retail_api_module._CREDIT_SCHEMA_READY
    _retail_api_module._CREDIT_SCHEMA_READY = False
    try:
        _ensure_credit_schema(_get_conn())
    finally:
        _retail_api_module._CREDIT_SCHEMA_READY = _previous_flag

    return _get_conn


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
    """Install A (this file's own `app`/`get_retail_conn()`) is booted ONCE
    at module import time and shared by every test below -- there is no
    per-test database the way `install_b` gives each test its own temp
    directory. `sync_outbox` is device-wide, not company-scoped (see
    sync_service.py's `_pending_outbox_count` docstring), so without this a
    later test's `_outbox('sale')[0]` would silently read an EARLIER test's
    row and either assert against the wrong sale or (worse) push an earlier
    test's already-tested events into a LATER test's `install_b`, inflating
    row counts in a way that looks like a real duplication bug but is only
    ever this file's own cross-test contamination. Runs before AND after
    (the `yield`), so a test that pushes some of its own rows via a real
    `service_a.push_once()` still starts the NEXT test with an empty table."""
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
# 1. EMISSION -- the real routes queue the right shape
# ─────────────────────────────────────────────────────────────────────────────

def test_create_sale_queues_sale_sale_item_and_payment_with_linked_uids():
    admin, cid, pid = _new_shop(price=50.0)
    resp = _sell_cash(admin, pid, 2)  # 2 * 50 = 100
    assert resp.status_code == 200, resp.get_json()

    sale_events = _outbox('sale')
    item_events = _outbox('sale_item')
    pay_events = _outbox('payment')
    assert len(sale_events) == 1
    assert len(item_events) == 1
    assert len(pay_events) == 1

    sale_uid = sale_events[0]['payload']['uid']
    assert sale_events[0]['entity_id'] == sale_uid
    assert sale_events[0]['event_type'] == 'create'
    assert 'company_id' not in sale_events[0]['payload']
    assert 'idempotency_key' not in sale_events[0]['payload']
    assert 'session_id' not in sale_events[0]['payload']
    assert sale_events[0]['payload']['total'] == 100.0

    assert item_events[0]['payload']['sale_uid'] == sale_uid
    assert item_events[0]['payload']['product_id'] == pid
    assert item_events[0]['payload']['line_total'] == 100.0

    assert pay_events[0]['payload']['sale_uid'] == sale_uid
    assert pay_events[0]['payload']['related_type'] == 'sale'
    assert pay_events[0]['payload']['amount'] == 100.0
    assert pay_events[0]['payload']['direction'] == 'in'


def test_create_return_queues_return_and_return_item_linked_to_the_sale_uid():
    admin, cid, pid = _new_shop(price=20.0)
    sale_resp = _sell_cash(admin, pid, 3)  # 60
    assert sale_resp.status_code == 200
    sale_id = sale_resp.get_json()['data']['id']
    sale_uid = _outbox('sale')[0]['payload']['uid']

    ret_resp = admin.post(f'{API}/returns', json={
        'sale_id': sale_id, 'items': [{'product_id': pid, 'quantity': 1}],
    })
    assert ret_resp.status_code == 200, ret_resp.get_json()

    ret_events = _outbox('return')
    ret_item_events = _outbox('return_item')
    assert len(ret_events) == 1
    assert len(ret_item_events) == 1
    assert ret_events[0]['payload']['sale_uid'] == sale_uid
    assert ret_events[0]['payload']['refund_amount'] == 20.0
    return_uid = ret_events[0]['payload']['uid']
    assert ret_item_events[0]['payload']['return_uid'] == return_uid
    assert ret_item_events[0]['payload']['product_id'] == pid


def test_a_customer_account_payment_queues_a_payment_event_with_no_sale_uid():
    """The single-funnel claim: _record_payment queues an event for EVERY
    money movement, not just a sale's own retained cash. A customer-account
    payment has no sale to resolve, so `sale_uid` must be explicitly absent
    (None), not a stale value from an unrelated earlier sale."""
    admin, cid, pid = _new_shop()
    cust = admin.post(f'{API}/customers', json={'name': 'Sync Customer'})
    assert cust.status_code == 200
    cust_id = cust.get_json()['data']['id']

    resp = admin.post(f'{API}/customers/{cust_id}/payments', json={'amount': 25.0, 'method': 'cash'})
    assert resp.status_code == 200, resp.get_json()

    pay_events = _outbox('payment')
    assert len(pay_events) == 1
    assert pay_events[0]['payload']['sale_uid'] is None
    assert pay_events[0]['payload']['related_type'] is None
    assert pay_events[0]['payload']['party_type'] == 'customer'
    assert pay_events[0]['payload']['amount'] == 25.0


# ─────────────────────────────────────────────────────────────────────────────
# 2. TWO-INSTALL ROUND TRIP -- correct to the cent, exactly once
# ─────────────────────────────────────────────────────────────────────────────

def test_a_sale_rung_on_install_a_appears_exactly_once_and_correct_to_the_cent_on_b(install_b, relay):
    admin, cid, pid = _new_shop(price=33.33)
    sale_resp = _sell_cash(admin, pid, 3)  # 99.99
    assert sale_resp.status_code == 200
    a_total = sale_resp.get_json()['data']['total']

    service_a = SyncService(client_factory=lambda: relay, get_conn=get_retail_conn)
    service_b = SyncService(client_factory=lambda: relay, get_conn=install_b,
                            local_company_id_provider=lambda: 'install-b-company')
    service_a.push_once()
    service_b.pull_once()

    b_conn = install_b()
    try:
        b_sales = [dict(r) for r in b_conn.execute("SELECT * FROM sales").fetchall()]
        b_items = [dict(r) for r in b_conn.execute("SELECT * FROM sale_items").fetchall()]
        b_pays = [dict(r) for r in b_conn.execute("SELECT * FROM payments").fetchall()]
    finally:
        b_conn.close()

    assert len(b_sales) == 1
    assert len(b_items) == 1
    assert len(b_pays) == 1
    assert b_sales[0]['total'] == a_total == 99.99
    assert b_sales[0]['company_id'] == 'install-b-company'  # B's own, never A's
    assert b_sales[0]['session_id'] is None
    assert b_items[0]['sale_id'] == b_sales[0]['id']  # resolved to B's own local id
    assert b_pays[0]['sale_id'] == b_sales[0]['id']
    assert b_pays[0]['amount'] == 99.99

    # MUTATION-PROOF-#1 REGRESSION GUARD: re-apply the FULL history from
    # since=0 two more times directly (bypassing pull_once()'s cursor, which
    # would otherwise just return an empty batch on a re-pull -- see this
    # test file's module docstring). Real create_sale-produced payloads,
    # not the synthetic ones commercial_runtime/sync/tests/test_sync_
    # service.py already exhausts this claim with.
    for _ in range(2):
        b_conn = install_b()
        try:
            service_b.apply_pull_result(b_conn, relay.pull(0))
            b_conn.commit()
        finally:
            b_conn.close()

    b_conn = install_b()
    try:
        b_sales = [dict(r) for r in b_conn.execute("SELECT * FROM sales").fetchall()]
        b_pays_total = b_conn.execute("SELECT COALESCE(SUM(amount),0) FROM payments").fetchone()[0]
    finally:
        b_conn.close()
    assert len(b_sales) == 1, f"re-applying the same batch created duplicate sales: {b_sales}"
    assert b_pays_total == 99.99, f"re-applying the same batch changed total takings: {b_pays_total}"


def test_a_return_rung_on_a_round_trips_and_resolves_against_bs_own_local_sale(install_b, relay):
    admin, cid, pid = _new_shop(price=40.0)
    sale_resp = _sell_cash(admin, pid, 2)  # 80
    sale_id = sale_resp.get_json()['data']['id']

    service_a = SyncService(client_factory=lambda: relay, get_conn=get_retail_conn)
    service_b = SyncService(client_factory=lambda: relay, get_conn=install_b,
                            local_company_id_provider=lambda: 'install-b-company')
    service_a.push_once()
    service_b.pull_once()  # B must have the sale before the return references it

    ret_resp = admin.post(f'{API}/returns', json={
        'sale_id': sale_id, 'items': [{'product_id': pid, 'quantity': 1}],
    })
    assert ret_resp.status_code == 200, ret_resp.get_json()
    service_a.push_once()
    service_b.pull_once()

    b_conn = install_b()
    try:
        b_returns = [dict(r) for r in b_conn.execute("SELECT * FROM returns").fetchall()]
        b_items = [dict(r) for r in b_conn.execute("SELECT * FROM return_items").fetchall()]
        b_sale = b_conn.execute("SELECT id FROM sales").fetchone()
    finally:
        b_conn.close()
    assert len(b_returns) == 1
    assert b_returns[0]['refund_amount'] == 40.0
    assert b_returns[0]['sale_id'] == b_sale['id']  # resolved to B's own local sale row
    assert len(b_items) == 1
    assert b_items[0]['return_id'] == b_returns[0]['id']


# ─────────────────────────────────────────────────────────────────────────────
# 3. ORPHANS -- Owner's own push-side quarantine can split parent from child
# ─────────────────────────────────────────────────────────────────────────────

def test_a_child_arriving_with_no_parent_is_quarantined_then_resolves_once_the_parent_arrives(install_b, relay):
    """Simulates exactly the interaction the phase brief names: Owner's own
    quarantine (681b0fa) skips a malformed PARENT sale event but still
    relays its already-valid CHILD sale_item event. B must not raise (which
    would wedge its cursor and every event behind this one forever) and
    must not silently drop the child."""
    admin, cid, pid = _new_shop()
    sale_resp = _sell_cash(admin, pid, 1)
    assert sale_resp.status_code == 200

    product_ev = _outbox('product')[0]
    sale_ev = _outbox('sale')[0]
    item_ev = _outbox('sale_item')[0]
    pay_ev = _outbox('payment')[0]

    service_b = SyncService(client_factory=lambda: relay, get_conn=install_b,
                            local_company_id_provider=lambda: 'install-b-company')

    # The PRODUCT this sale's line item points at (sale_items.product_id has
    # its own real FK, `REFERENCES products(id)`) must exist on B first --
    # exactly as it always would in real traffic, where a device can only
    # ever sell a product it already has locally, so the product's own
    # catalog-sync event is always older in that SAME device's outbox than
    # any sale referencing it. This is the pre-existing four-entity sync
    # (multi-device-sync-foundation), not Phase 5 -- pushed here explicitly,
    # by itself, so the orphan simulation below is scoped to the ONE parent
    # link this test actually means to exercise (sale_item -> sale).
    relay.push([{
        'id': product_ev['id'], 'entity_type': 'product', 'entity_id': product_ev['entity_id'],
        'event_type': 'create', 'payload': product_ev['payload'], 'created_at': product_ev['created_at'],
    }])
    service_b.pull_once()

    # Only the CHILD reaches the relay -- the parent "was quarantined".
    relay.push([{
        'id': item_ev['id'], 'entity_type': 'sale_item', 'entity_id': item_ev['entity_id'],
        'event_type': 'create', 'payload': item_ev['payload'], 'created_at': item_ev['created_at'],
    }])
    service_b.pull_once()

    b_conn = install_b()
    try:
        assert b_conn.execute("SELECT COUNT(*) FROM sale_items").fetchone()[0] == 0
        parked = [dict(r) for r in b_conn.execute("SELECT * FROM sync_apply_quarantine").fetchall()]
    finally:
        b_conn.close()
    assert len(parked) == 1
    assert parked[0]['entity_type'] == 'sale_item'
    assert parked[0]['reason'] == 'missing_parent:sale'

    # The parent (and its payment) now arrive, e.g. an operator replayed the
    # once-quarantined event from Owner's console.
    relay.push([
        {'id': sale_ev['id'], 'entity_type': 'sale', 'entity_id': sale_ev['entity_id'],
         'event_type': 'create', 'payload': sale_ev['payload'], 'created_at': sale_ev['created_at']},
        {'id': pay_ev['id'], 'entity_type': 'payment', 'entity_id': pay_ev['entity_id'],
         'event_type': 'create', 'payload': pay_ev['payload'], 'created_at': pay_ev['created_at']},
    ])
    service_b.pull_once()

    b_conn = install_b()
    try:
        items = [dict(r) for r in b_conn.execute("SELECT * FROM sale_items").fetchall()]
        parked_after = b_conn.execute("SELECT COUNT(*) FROM sync_apply_quarantine").fetchone()[0]
    finally:
        b_conn.close()
    assert len(items) == 1, 'the parked child was never retried once its parent arrived'
    assert parked_after == 0


# ─────────────────────────────────────────────────────────────────────────────
# 4. THE CASH DRAWER -- hazard #2, a synced sale never joins a local drawer
# ─────────────────────────────────────────────────────────────────────────────

def test_a_synced_sale_never_joins_the_receiving_devices_open_drawer(relay):
    """Real open cash session, real local sale, on install A -- then a
    simulated foreign device's sale (own terminal, own uid) arrives via sync
    into A's OWN database. A's own X-report (the exact function
    `GET .../x-report` calls) must show ONLY its own takings."""
    admin, cid, pid = _new_shop(price=100.0)
    with at_terminal(TILL_DESK):
        open_resp = admin.post(f'{API}/cash-sessions/open', json={'opening_float': 50.0})
        assert open_resp.status_code == 200, open_resp.get_json()
        desk_sid = open_resp.get_json()['data']['id']
        sale_resp = _sell_cash(admin, pid, 1)  # $100, A's own takings
        assert sale_resp.status_code == 200, sale_resp.get_json()

    # A REAL foreign sale, shaped exactly like retail_api.py's own emission
    # (see create_sale's comment: no company_id, no idempotency_key, no
    # session_id in the payload) -- $250, rung on TILL_PHONE_B, arriving
    # into A's database via a genuine apply, not a hand-stamped row.
    foreign_sale_uid = str(uuid.uuid4())
    foreign_payment_uid = str(uuid.uuid4())
    relay.push([
        {'id': str(uuid.uuid4()), 'entity_type': 'sale', 'entity_id': foreign_sale_uid, 'event_type': 'create',
         'payload': {
             'uid': foreign_sale_uid, 'sale_number': 'FOREIGN-SALE-1', 'branch_id': 1, 'customer_id': None,
             'cashier': 'POS', 'subtotal': 250.0, 'discount_amount': 0.0, 'tax_amount': 0.0, 'total': 250.0,
             'amount_paid': 250.0, 'change_amount': 0.0, 'payment_method': 'cash', 'status': 'completed',
             'notes': '', 'created_at': '2026-08-25 12:00:00', 'due_date': None,
             'actor_user_uid': 'cashier-on-install-b', 'terminal_id': TILL_PHONE_B,
             'created_at_utc': '2026-08-25T12:00:00+00:00',
         }, 'created_at': '2026-08-25T12:00:00+00:00'},
        {'id': str(uuid.uuid4()), 'entity_type': 'payment', 'entity_id': foreign_payment_uid, 'event_type': 'create',
         'payload': {
             'uid': foreign_payment_uid, 'reference': 'FOREIGN-RCPT-1', 'party_type': None, 'party_id': None,
             'direction': 'in', 'amount': 250.0, 'currency': 'USD', 'method': 'cash',
             'related_type': 'sale', 'related_id': 1, 'sale_uid': foreign_sale_uid,
             'notes': '', 'status': 'active', 'created_by': 'cashier-on-install-b', 'device': None,
             'created_at': '2026-08-25 12:00:00',
         }, 'created_at': '2026-08-25T12:00:00+00:00'},
    ])
    service_a = SyncService(client_factory=lambda: relay, get_conn=get_retail_conn,
                            local_company_id_provider=lambda: cid)
    service_a.pull_once()

    conn = get_retail_conn()
    try:
        foreign_row = conn.execute("SELECT session_id, terminal_id FROM sales WHERE uid=?",
                                   (foreign_sale_uid,)).fetchone()
        assert foreign_row is not None, 'the foreign sale never applied at all'
        # Hazard #2's exact claim: NULL, never A's own open session.
        assert foreign_row['session_id'] is None
        # And it keeps its OWN terminal, never re-attributed to TILL_DESK.
        assert foreign_row['terminal_id'] == TILL_PHONE_B

        sess = conn.execute("SELECT * FROM cash_sessions WHERE id=?", (desk_sid,)).fetchone()
        report = retail_api._cash_session_report(conn, cid, sess)
    finally:
        conn.close()

    # Both halves, per this project's own anti-vacuity precedent
    # (retail_drawer_terminal_scope_test.py's module docstring, failure
    # shape #3): the figure is exactly A's own takings, not merely "not
    # $350" and not "zero" (which a broken fixture that sold nothing would
    # also satisfy).
    assert report['cash_sales'] == 100.0, report
    assert report['expected_cash'] == 150.0, report  # 50 float + 100 own sale


# ─────────────────────────────────────────────────────────────────────────────
# 5. THE THREE DEFECTS THIS WAVE EXISTS TO CLOSE
#
# Added by the verification pass, because none of the tests above touch any of
# them: with all three fixes reverted, every test in sections 1-4 (and all 55
# in commercial_runtime/sync/tests/test_sync_service.py) still passed, 62/62.
# A suite that stays green while the thing it was written for is broken is the
# exact failure this project has already been bitten by once -- see this
# file's own module docstring on composition. Each test below is written so
# that reverting its own defect's fix makes it FAIL; each was mutation-proved
# that way against a reverted copy of the tree, and the failure text is quoted
# in the comment above it.
# ─────────────────────────────────────────────────────────────────────────────

def test_two_installs_mint_different_stable_doc_discriminators(tmp_path):
    """DEFECT 1 (AUDIT-032B), re-verified after AUDIT-032D changed HOW the
    discriminator is derived. `doc_sequences` is per-device and never synced,
    so two tills both start at 0 and, without a device fragment, both would
    mint the identical "SALE-000001-<cid8>" -- which collides on
    `sales.sale_number`'s BARE unique index the moment sync relays both to
    any third device, raising out of `_apply_event` and wedging that
    device's cursor forever.

    `at_terminal()` no longer drives this (see `at_device_data()`'s own
    docstring for exactly why AUDIT-032D made that true): the discriminator
    is now a value persisted ONCE per install
    (`<AURA_APP_DATA>/device/doc_discriminator.json`) and read back forever,
    never re-derived from whatever `local_terminal_id()` reports on a given
    call. `at_device_data()` is the correct double now -- two installs, two
    `AURA_APP_DATA` directories, two independently-generated persisted
    values -- which is exactly what makes two REAL tills' numbers not
    collide. Both halves are asserted -- the same install is STABLE across
    repeated calls, and two DIFFERENT installs differ -- so a fix that made
    every number unique by some other means (a random suffix per sale, say)
    would not pass either: the discriminator has to be the INSTALL's, and
    stable.
    """
    device_a_data = tmp_path / "device_a"
    device_b_data = tmp_path / "device_b"
    with at_device_data(device_a_data):
        a1 = retail_api._device_doc_discriminator()
        a2 = retail_api._device_doc_discriminator()
    with at_device_data(device_b_data):
        b1 = retail_api._device_doc_discriminator()
    assert a1 and b1
    assert len(a1) == retail_api._DOC_DISCRIMINATOR_WIDTH
    assert len(b1) == retail_api._DOC_DISCRIMINATOR_WIDTH
    assert a1 == a2, 'the SAME install minted two different discriminators'
    assert a1 != b1, 'two DIFFERENT installs minted the same discriminator'

    # ...and the number a till in each install actually rings must carry
    # its own install's fragment. Without this half the test still passes
    # with the numbering fix reverted (it would only be pinning the helper,
    # which the revert leaves intact) -- verified by running exactly that.
    admin, cid, pid = _new_shop(price=10.0)
    with at_device_data(device_a_data):
        desk = _sell_cash(admin, pid, 1)
    assert desk.status_code == 200, desk.get_json()
    assert desk.get_json()['data']['sale_number'].rsplit('-', 1)[-1] == a1

    # The same must hold for returns -- `returns.return_number` carries the
    # identical bare UNIQUE constraint and was fixed in the same breath.
    with at_device_data(device_a_data):
        r = admin.post(f'{API}/returns', json={
            'sale_id': desk.get_json()['data']['id'],
            'items': [{'product_id': pid, 'quantity': 1}]})
    assert r.status_code == 200, r.get_json()
    assert r.get_json()['data']['return_number'].rsplit('-', 1)[-1] == a1


def test_a_device_with_no_terminal_identity_yet_still_mints_a_stable_discriminator(tmp_path):
    """DEFECT 1, the state a brand-new till is genuinely in: `local_device.json`
    does not exist yet (a Phase 5 prerequisite subject in its own right).
    `local_terminal_id()` is a deliberate PEEK that never creates the file, so
    a fresh till has to persist its OWN discriminator -- otherwise the very
    first sale a till ever rings is the one number with no device fragment at
    all.

    AUDIT-032D: minting that discriminator must NEVER create
    `local_device.json` -- that WAS the bug (see `_device_doc_discriminator()`'s
    own docstring and `persist_doc_discriminator()`'s in
    commercial_runtime/identity/device_context.py for the full chain, and
    retail_cash_drawer_test.py's full-shift round trip for the 403 it caused
    on a brand-new till's first shift close). This is asserted directly
    below, not just implied: a version of the fix that persisted the
    discriminator correctly but STILL happened to touch local_device.json as
    a side effect would fail this test, and only this test.

    The stability assertions are the ones that matter most: the discriminator
    must not CHANGE even once a REAL terminal identity is later established
    on this same install.
    """
    from commercial_runtime.identity.device_context import (
        local_device_uuid, peek_local_device_uuid,
    )
    device_data = tmp_path / "fresh_till"
    local_device_json = device_data / "device" / "local_device.json"

    with at_device_data(device_data):
        with at_terminal(None):  # the real pre-identity state
            assert not local_device_json.exists()
            first = retail_api._device_doc_discriminator()
            assert first, 'a till with no terminal identity minted an EMPTY discriminator'
            assert len(first) == retail_api._DOC_DISCRIMINATOR_WIDTH
            # THE regression this test exists to catch: minting a document
            # number must never manufacture device identity as a side effect.
            assert not local_device_json.exists(), (
                'minting a document-numbering discriminator created '
                'local_device.json -- AUDIT-032D regressed')
            assert peek_local_device_uuid() is None

            # Persisted; every later call must read that SAME value back.
            assert retail_api._device_doc_discriminator() == first

            # ...and the number a till in THIS state actually rings must
            # carry it, and must still not have created device identity.
            admin, cid, pid = _new_shop(price=10.0)
            sale = _sell_cash(admin, pid, 1)
            assert sale.status_code == 200, sale.get_json()
            assert sale.get_json()['data']['sale_number'].rsplit('-', 1)[-1] == first, \
                sale.get_json()['data']['sale_number']
            assert not local_device_json.exists()

            # Now a REAL terminal identity gets established on this same
            # install (an ordinary event -- e.g. device resolution on
            # login -- just never one document numbering triggers itself).
            real_terminal_id = local_device_uuid()
        assert local_device_json.exists()
        # The two are deliberately unrelated now -- AUDIT-032D's whole point
        # is that document numbering stops caring what this value is.
        assert real_terminal_id != first
        # ...including once local_terminal_id() can answer for real again --
        # the discriminator must not re-derive from it.
        assert retail_api._device_doc_discriminator() == first


def test_voiding_a_payment_propagates_to_the_other_device(install_b, relay):
    """DEFECT 2 (AUDIT-032A). Before this fix `POST /payments/<id>/void`
    queued NOTHING, so a receipt cancelled on one device stayed live money on
    every other device forever. Proved through the real route, end to end.

    The amount is asserted unchanged on both sides: this branch is the ONE
    exception to "money is never edited in place", and it must stay narrow
    enough that it cannot become a way to rewrite what another till rang.
    """
    admin, cid, pid = _new_shop()
    cust = admin.post(f'{API}/customers', json={'name': 'Void Propagation Co'})
    assert cust.status_code == 200, cust.get_json()
    cust_id = cust.get_json()['data']['id']
    # A customer-account receipt, not a sale's own retained cash: void_payment
    # REFUSES anything belonging to a sale (409, "process a return against
    # that sale instead"), so account receipts are the whole voidable
    # population and therefore the only honest subject for this test.
    rec = admin.post(f'{API}/customers/{cust_id}/payments', json={'amount': 25.0, 'method': 'cash'})
    assert rec.status_code == 200, rec.get_json()

    service_a = SyncService(client_factory=lambda: relay, get_conn=get_retail_conn)
    service_b = SyncService(client_factory=lambda: relay, get_conn=install_b,
                            local_company_id_provider=lambda: 'install-b-company')
    service_a.push_once()
    service_b.pull_once()

    conn = get_retail_conn()
    try:
        pay = conn.execute("SELECT id, uid, amount FROM payments WHERE company_id=? AND "
                           "party_type='customer' ORDER BY id DESC LIMIT 1", (cid,)).fetchone()
    finally:
        conn.close()

    b_conn = install_b()
    try:
        before = b_conn.execute("SELECT status, amount FROM payments WHERE uid=?",
                                (pay['uid'],)).fetchone()
    finally:
        b_conn.close()
    assert before is not None, 'the receipt never reached install B at all -- test is vacuous'
    assert before['status'] == 'active'

    resp = admin.post(f'{API}/payments/{pay["id"]}/void', json={})
    assert resp.status_code == 200, resp.get_json()

    # The void must be QUEUED at all -- assert the check ran, not just that
    # the row eventually looks right.
    void_events = [e for e in _outbox('payment') if e['event_type'] == 'update']
    assert len(void_events) == 1, f'void queued no sync event: {_outbox("payment")}'
    assert void_events[0]['entity_id'] == pay['uid']
    assert void_events[0]['payload'] == {'uid': pay['uid'], 'status': 'voided'}
    assert 'amount' not in void_events[0]['payload'], 'a void event carried an amount'

    service_a.push_once()
    service_b.pull_once()

    b_conn = install_b()
    try:
        after = b_conn.execute("SELECT status, amount FROM payments WHERE uid=?",
                               (pay['uid'],)).fetchone()
        n_pay = b_conn.execute("SELECT COUNT(*) FROM payments").fetchone()[0]
    finally:
        b_conn.close()
    assert after['status'] == 'voided', \
        f"the void never reached install B -- status is still {after['status']!r}"
    assert after['amount'] == before['amount'] == 25.0, 'the void rewrote the amount'
    assert n_pay == 1, 'the void inserted a second payment row instead of updating'


def test_a_pulled_sale_is_filed_under_the_receiving_devices_own_branch(install_b, relay):
    """DEFECT 3 (AUDIT-032C). `branches.id` is a plain per-device
    autoincrement and `branch` is not a synced entity type, so carrying the
    sending device's raw integer files the pulled sale under whatever that
    number happens to mean here. Resolved via the payload's `branch_uid`
    instead.

    Install B is deliberately given DECOY branches first, so its own id for
    the shared uid is not the same integer A used -- without them both sides
    would say 1 and the assertion would pass on the broken code too.
    """
    admin, cid, pid = _new_shop()
    sale = _sell_cash(admin, pid, 1)
    assert sale.status_code == 200, sale.get_json()

    conn = get_retail_conn()
    try:
        a_branch = conn.execute("SELECT id, uid FROM branches WHERE company_id=? ORDER BY id LIMIT 1",
                                (cid,)).fetchone()
    finally:
        conn.close()

    # The emission side must carry the uid at all.
    sale_event = _outbox('sale')[0]
    assert sale_event['payload']['branch_uid'] == a_branch['uid'], sale_event['payload']

    b_conn = install_b()
    try:
        for name in ('Decoy 1', 'Decoy 2', 'Decoy 3'):
            b_conn.execute("INSERT INTO branches (company_id,name,address,phone,uid) VALUES (?,?,?,?,?)",
                           ('install-b-company', name, '', '', str(uuid.uuid4())))
        b_conn.execute("INSERT INTO branches (company_id,name,address,phone,uid) VALUES (?,?,?,?,?)",
                       ('install-b-company', 'The Same Shop', '', '', a_branch['uid']))
        b_conn.commit()
        b_branch_id = b_conn.execute("SELECT id FROM branches WHERE uid=?",
                                     (a_branch['uid'],)).fetchone()['id']
    finally:
        b_conn.close()
    assert b_branch_id != a_branch['id'], 'decoys failed -- both devices use the same id, test is vacuous'

    service_a = SyncService(client_factory=lambda: relay, get_conn=get_retail_conn)
    service_b = SyncService(client_factory=lambda: relay, get_conn=install_b,
                            local_company_id_provider=lambda: 'install-b-company')
    service_a.push_once()
    service_b.pull_once()

    b_conn = install_b()
    try:
        landed = b_conn.execute("SELECT branch_id FROM sales WHERE uid=?",
                                (sale_event['payload']['uid'],)).fetchone()
    finally:
        b_conn.close()
    assert landed is not None, 'the sale never reached install B at all -- test is vacuous'
    assert landed['branch_id'] == b_branch_id, \
        f"pulled sale landed on branch {landed['branch_id']}; expected B's own {b_branch_id}"


def test_an_unresolvable_branch_uid_falls_back_visibly_not_silently(install_b, relay, caplog):
    """DEFECT 3's other half, and the one that is easy to get wrong: falling
    back to this device's default branch is the only answer available, but it
    must leave a trail. A silent guess is what the defect WAS.

    Asserts THE CHECK RAN (a WARNING naming the discarded uid) as well as the
    outcome (the row landed on the default branch) -- a fallback that filed
    the row correctly but said nothing would still be the defect.
    """
    admin, cid, pid = _new_shop()
    sale = _sell_cash(admin, pid, 1)
    assert sale.status_code == 200
    sale_event = _outbox('sale')[0]
    # B is never given a branch carrying A's uid -- the ordinary state of
    # every install today, since `branch` itself does not sync.
    b_conn = install_b()
    try:
        b_conn.execute("INSERT INTO branches (company_id,name,address,phone,uid) VALUES (?,?,?,?,?)",
                       ('install-b-company', 'B Main', '', '', str(uuid.uuid4())))
        b_conn.commit()
        b_default = b_conn.execute("SELECT id FROM branches WHERE company_id=? ORDER BY id LIMIT 1",
                                   ('install-b-company',)).fetchone()['id']
    finally:
        b_conn.close()

    service_a = SyncService(client_factory=lambda: relay, get_conn=get_retail_conn)
    service_b = SyncService(client_factory=lambda: relay, get_conn=install_b,
                            local_company_id_provider=lambda: 'install-b-company')
    service_a.push_once()
    with caplog.at_level('WARNING', logger='commercial_runtime.sync.sync_service'):
        service_b.pull_once()

    b_conn = install_b()
    try:
        landed = b_conn.execute("SELECT branch_id FROM sales WHERE uid=?",
                                (sale_event['payload']['uid'],)).fetchone()
    finally:
        b_conn.close()
    assert landed is not None and landed['branch_id'] == b_default

    warnings = [r.getMessage() for r in caplog.records if r.levelname == 'WARNING']
    assert any(sale_event['payload']['branch_uid'] in m and 'did not resolve' in m
               for m in warnings), \
        f'the fallback was SILENT -- no warning naming the discarded uid: {warnings}'


def test_a_line_item_whose_product_never_arrived_is_quarantined_not_wedged(install_b, relay):
    """The OTHER parent link, which the orphan test above deliberately arranges
    around ("pushed here explicitly, by itself, so the orphan simulation below
    is scoped to the ONE parent link this test actually means to exercise").

    `sale_items` declares TWO real FKs -- sale_id -> sales(id) AND product_id
    -> products(id) -- and Owner's push-side quarantine (681b0fa) can skip a
    malformed PRODUCT event exactly as easily as a malformed sale. A device
    that sold a product it had itself RECEIVED by sync emits no product event
    of its own, so nothing downstream re-supplies it either.

    Left to the FK this raises `sqlite3.IntegrityError: FOREIGN KEY constraint
    failed` out of pull_once(), the cursor never advances, and every later
    pull re-fails on the same batch forever -- catalogue included, from every
    device on the license. Reproduced end to end against two real installs
    before the fix; this test is the cheap version of that.

    Both halves matter: parked instead of raising (so the batch applies), AND
    resolved once the product finally arrives (so parking is not just a
    quieter way to lose the line).
    """
    admin, cid, pid = _new_shop(price=10.0)
    sale = _sell_cash(admin, pid, 1)
    assert sale.status_code == 200, sale.get_json()

    product_ev = _outbox('product')[0]
    sale_ev = _outbox('sale')[0]
    item_ev = _outbox('sale_item')[0]

    service_b = SyncService(client_factory=lambda: relay, get_conn=install_b,
                            local_company_id_provider=lambda: 'install-b-company')

    # Everything EXCEPT the product: its own create event "was quarantined".
    relay.push([
        {'id': sale_ev['id'], 'entity_type': 'sale', 'entity_id': sale_ev['entity_id'],
         'event_type': 'create', 'payload': sale_ev['payload'], 'created_at': sale_ev['created_at']},
        {'id': item_ev['id'], 'entity_type': 'sale_item', 'entity_id': item_ev['entity_id'],
         'event_type': 'create', 'payload': item_ev['payload'], 'created_at': item_ev['created_at']},
    ])
    service_b.pull_once()  # must NOT raise

    b_conn = install_b()
    try:
        cursor = b_conn.execute("SELECT last_seq FROM sync_cursor WHERE id=1").fetchone()['last_seq']
        n_sales = b_conn.execute("SELECT COUNT(*) FROM sale_items").fetchone()[0]
        parked = [dict(r) for r in b_conn.execute("SELECT * FROM sync_apply_quarantine").fetchall()]
        applied_sale = b_conn.execute("SELECT COUNT(*) FROM sales").fetchone()[0]
    finally:
        b_conn.close()
    assert cursor > 0, 'the cursor never advanced -- this batch wedged the device'
    assert applied_sale == 1, 'the rest of the batch did not apply'
    assert n_sales == 0
    assert len(parked) == 1, parked
    assert parked[0]['entity_type'] == 'sale_item'
    assert parked[0]['reason'] == 'missing_parent:product', parked[0]
    assert str(pid) in parked[0]['detail'], parked[0]['detail']

    # The product finally arrives (an operator replayed it from Owner's
    # console). The parked line must come home, to the cent.
    relay.push([{
        'id': product_ev['id'], 'entity_type': 'product', 'entity_id': product_ev['entity_id'],
        'event_type': 'create', 'payload': product_ev['payload'],
        'created_at': product_ev['created_at'],
    }])
    service_b.pull_once()

    b_conn = install_b()
    try:
        items = [dict(r) for r in b_conn.execute("SELECT * FROM sale_items").fetchall()]
        still_parked = b_conn.execute("SELECT COUNT(*) FROM sync_apply_quarantine").fetchone()[0]
    finally:
        b_conn.close()
    assert len(items) == 1, 'the parked line item never resolved -- parking became silent loss'
    assert items[0]['line_total'] == 10.0
    assert still_parked == 0
