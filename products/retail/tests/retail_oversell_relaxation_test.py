"""
Aura Retail -- launch-readiness Phase 7 stage 7d-iii: trust the cashier's
eyes over a stale cache, and record it. See docs/launch-readiness/
phase7-offline-ux.md "Correction to Decision 1", ROADMAP.md's 2026-08-29
"Correction to the v20 claim" entry, api/retail_api.py's `create_sale`
(the `qty > on_hand` relaxation and its post-write recheck), and
database/schema.py's `record_or_refresh_stock_exception` (the ONE canonical
upsert both `create_sale` and commercial_runtime/sync/sync_service.py's
`inventory_movement` apply branch now share).

TWO HALVES THAT MUST LAND TOGETHER, tested in that order:

  HALF 1 (allow/safety FIRST, per this stage's own instructions): relaxing
  `create_sale`'s `Insufficient stock` refusal creates a NEW way for a
  balance to go negative. Sections 1-4 below prove the refusal stays fully
  intact on every install where relaxing it would be wrong -- sync
  switched off (the MAJORITY install), never synced, recently synced, and
  a behind device whose stock genuinely covers the sale. Section 1 is THE
  most important test in this file: a bug here would let every
  single-device shop that never turns sync on start overselling.

  HALF 2 (the relaxation and its recording): sections 5-10 prove the
  relaxation actually fires when the device is behind, that it is never
  silent (the response says so), and that it is recorded in
  `stock_exceptions` -- through the SAME canonical function the sync apply
  branch already used, not a second copy of the SQL.

── HOW THIS FILE AVOIDS TESTING ITSELF (ENGINEERING.md's three failure
   shapes), following retail_offline_sales_stop_test.py's own precedent
   exactly ──────────────────────────────────────────────────────────────
1. ASSERTS THE CHECK RAN, not just the outcome: every refusal test reads
   stock and the sales count BEFORE and AFTER, and asserts both are
   UNCHANGED -- a refusal that still wrote the sale would satisfy a bare
   status-code assertion while doing exactly the damage stage 7c/7d-iii's
   safety half exists to prevent.
2. NO FIXTURE MANUFACTURES THE STATE THAT HIDES THE BUG: "behind" is built
   by writing a REAL old timestamp through `record_sync_freshness` -- the
   SAME function `SyncService._record_sync_success` calls in production --
   so a freshly constructed SyncService loads it back exactly as a
   restarted process would, matching `_mark_behind` in
   retail_offline_sales_stop_test.py verbatim.
3. THE PASS CONDITION IS NOT THE BUG SIGNATURE: `test_a_device_that_has_
   never_synced_still_refuses_to_oversell` is deliberately followed by an
   ADVERSARIAL probe (`test_the_never_synced_guard_is_independently_load_
   bearing_for_oversell`) that hand-crafts a health snapshot a real
   SyncService can never produce -- because a real "never synced" snapshot
   always pairs with `seconds_since_last_success=None`, which fails the
   downstream `isinstance` check on its own. Proven by actually running the
   mutation (see mutation proof 2 below): the NATURAL version of that test
   stays green even with the `never_synced` guard deleted, for the exact
   reason retail_offline_sales_stop_test.py's own section 1b documents for
   its sibling guard. Only the adversarial probe isolates it.

Self-contained bootstrap, matching retail_offline_sales_stop_test.py (no
shared conftest.py exists here): SYNC_RELAY_BASE_URL deliberately left
UNSET so app.py's own module-scope SyncService is never constructed, and
every test controls `_active_service` explicitly via the autouse fixture
below -- exactly test_a_shop_with_sync_switched_off_still_refuses_to_
oversell's real-world shape.

Run:
    pytest products/retail/tests/retail_oversell_relaxation_test.py -v
"""
import os
import sys
import shutil
import tempfile
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

PRODUCT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = PRODUCT_DIR / 'backend'
SUITE_ROOT = PRODUCT_DIR.parent.parent
for _p in (str(SUITE_ROOT), str(BACKEND_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

DATA = Path(tempfile.mkdtemp(prefix='aura_retail_oversell_relax_'))
(DATA / 'database' / 'subsystems').mkdir(parents=True, exist_ok=True)
os.environ.update(AURA_STANDALONE='1', AURA_BUNDLE_DIR=str(BACKEND_DIR), AURA_APP_DATA=str(DATA))
os.environ.pop('AURA_DEV', None)
# Deliberately NOT set, same reasoning as retail_offline_sales_stop_test.py's
# own module docstring: leaving this unset is what makes the module-scope
# app.init_app() below register NO SyncService of its own, so every test
# here controls `_active_service` explicitly and starts from "sync
# switched off" -- the majority-install shape section 1 below tests.
os.environ.pop('SYNC_RELAY_BASE_URL', None)

from commercial_runtime.licensing_contracts.test_support import seed_active_license  # noqa: E402
seed_active_license(str(DATA), product_code='AURA_RETAIL', platform='WINDOWS')

import app as _app_module  # noqa: E402

app = _app_module.init_app()
app.config['TESTING'] = True

import database.schema as sch  # noqa: E402
from api import retail_api  # noqa: E402
from commercial_runtime.identity import user_accounts  # noqa: E402
from commercial_runtime.identity.registry_db import get_conn as registry_conn  # noqa: E402
from commercial_runtime.security.passwords import hash_password  # noqa: E402
from commercial_runtime.sync.sync_service import (  # noqa: E402
    SyncFreshnessStore, SyncService,
    register_active_service, unregister_active_service,
    SYNC_STALE_THRESHOLD_SECONDS, SYNC_SALES_STOP_THRESHOLD_SECONDS,
)

API = '/api/sub/retail'

assert SYNC_STALE_THRESHOLD_SECONDS == 30 * 60, (
    'this file marks devices "behind" using hour-scale offsets chosen to clear this '
    'threshold while staying well under the 72h sales-stop one -- update the offsets '
    'deliberately if this constant moves')
assert SYNC_SALES_STOP_THRESHOLD_SECONDS == 72 * 60 * 60, (
    'this file marks devices "behind" by 1-2 hours specifically so stage 7c-ii\'s '
    '72-hour hard stop on NEW SALES never fires and confounds these tests -- update '
    'the offsets deliberately if this constant moves')


def teardown_module(module):
    shutil.rmtree(DATA, ignore_errors=True)


# ── Fixtures / helpers (deliberately duplicated per-file convention -- see
#    retail_offline_sales_stop_test.py's own module docstring for why
#    nothing here is shared via a conftest.py) ─────────────────────────────

def _reset_sync_freshness():
    """Mirrors retail_offline_sales_stop_test.py's own helper of the same
    name verbatim: `sync_freshness` is a SINGLE-ROW, per-DEVICE table, and
    every test in this file shares ONE physical retail.db, so a stale
    timestamp left behind by an earlier test would leak into the next
    one's "behind" state."""
    conn = sch.get_retail_conn()
    try:
        conn.execute(
            'UPDATE sync_freshness SET last_push_success_at=NULL, '
            'last_pull_success_at=NULL, offline_override_at=NULL WHERE id=1')
        conn.commit()
    finally:
        conn.close()


@pytest.fixture(autouse=True)
def _clear_active_service():
    """No test may inherit a service or a stale freshness timestamp another
    test left behind -- identical fixture to retail_offline_sales_stop_
    test.py's own."""
    unregister_active_service()
    _reset_sync_freshness()
    yield
    unregister_active_service()


def _make_user(role, company_id, email_prefix='oversellrelax'):
    email = f'{email_prefix}-{role}-{uuid.uuid4().hex[:10]}@test.local'
    password = 'OversellRelaxPW1'
    user_id = str(uuid.uuid4())
    conn = registry_conn()
    try:
        conn.execute(
            'INSERT INTO users (id, uid, company_id, employee_id, email, password_hash, role, status, '
            'require_password_change) VALUES (?,?,?,?,?,?,?,?,0)',
            (user_id, str(uuid.uuid4()), company_id, f'EMP-{uuid.uuid4().hex[:6].upper()}', email,
             hash_password(password), role, 'active'))
        conn.execute('INSERT INTO user_permissions (id, user_id, subsystem, access_level) VALUES (?,?,?,?)',
                     (str(uuid.uuid4()), user_id, 'retail', 'full'))
        user_accounts.seed_capabilities_for_user(conn, user_id, role)
        conn.commit()
    finally:
        conn.close()
    client = app.test_client()
    r = client.post('/api/auth/login', json={'email': email, 'password': password})
    assert r.status_code == 200, r.get_data(as_text=True)
    return client, user_id


def _new_shop():
    """A company with an admin and no products yet -- each test creates its
    own product(s) with whatever initial_stock the scenario needs."""
    company_id = str(uuid.uuid4())
    admin, _uid = _make_user('admin', company_id)
    return admin, company_id


def _create_product(client, initial_stock=0):
    sku = f'OSR-{uuid.uuid4().hex[:8]}'
    r = client.post(f'{API}/products', json={
        'name': f'Oversell Relaxation Widget {sku}', 'sku': sku, 'sell_price': 10.0,
        'cost_price': 5.0, 'tax_rate': 0, 'initial_stock': initial_stock,
    })
    assert r.status_code == 200, r.get_json()
    return r.get_json()['data']['id']


def _sale_payload(items, amount_paid=None):
    total_qty = sum(i['quantity'] for i in items)
    return {
        'items': items,
        'amount_paid': amount_paid if amount_paid is not None else total_qty * 1000.0,
        'payment_method': 'cash',
        'idempotency_key': str(uuid.uuid4()),
    }


def _sell(client, pid, qty=1):
    return client.post(f'{API}/sales', json=_sale_payload([{'product_id': pid, 'quantity': qty}]))


def _sell_multi(client, items):
    return client.post(f'{API}/sales', json=_sale_payload(items))


def _mark_behind(hours):
    """Persists BOTH push and pull's last_success_at as `hours` ago, through
    `record_sync_freshness` -- the SAME function `_record_sync_success`
    calls in production -- so a FRESHLY CONSTRUCTED SyncService loads this
    exact state on its own, exactly as a genuinely-behind device's restart
    would. Verbatim copy of retail_offline_sales_stop_test.py's own helper."""
    ts = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    conn = sch.get_retail_conn()
    try:
        sch.record_sync_freshness(conn, 'push', ts)
        sch.record_sync_freshness(conn, 'pull', ts)
        conn.commit()
    finally:
        conn.close()


def _real_sync_service():
    """A SyncService wired with a REAL SyncFreshnessStore against THIS
    test's own retail.db -- the exact collaborators app.py itself wires
    when SYNC_RELAY_BASE_URL is configured, INCLUDING `stock_exception_
    recorder` (stage 7d-iii's own addition), so tests in this file exercise
    the real production wiring, not a hand-rolled substitute. Matches
    retail_offline_sales_stop_test.py's own `_real_sync_service` plus the
    one new collaborator this stage adds."""
    store = SyncFreshnessStore(
        load=sch.load_sync_freshness, record=sch.record_sync_freshness,
        record_override=sch.record_offline_override)
    return SyncService(None, sch.get_retail_conn, None, local_freshness_store=store,
                        stock_exception_recorder=sch.record_or_refresh_stock_exception)


def _balance(cid, pid, bid=None):
    conn = sch.get_retail_conn()
    try:
        if bid is None:
            bid = retail_api._default_branch(conn, cid)
        row = conn.execute(
            'SELECT quantity_on_hand FROM inventory_balances WHERE company_id=? AND product_id=? AND branch_id=?',
            (cid, pid, bid)).fetchone()
        conn.commit()
        return row['quantity_on_hand'] if row else None
    finally:
        conn.close()


def _branch_info(cid):
    conn = sch.get_retail_conn()
    try:
        bid = retail_api._default_branch(conn, cid)
        buid = retail_api._branch_uid(conn, bid)
        conn.commit()
        return bid, buid
    finally:
        conn.close()


def _exceptions_for(cid, *, product_id=None):
    conn = sch.get_retail_conn()
    try:
        sql = 'SELECT * FROM stock_exceptions WHERE company_id=?'
        params = [cid]
        if product_id is not None:
            sql += ' AND product_id=?'
            params.append(product_id)
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def _sales_count(cid):
    conn = sch.get_retail_conn()
    try:
        return conn.execute('SELECT COUNT(*) AS n FROM sales WHERE company_id=?', (cid,)).fetchone()['n']
    finally:
        conn.close()


def _apply_incoming_movement(cid, product_id, branch_uid, quantity, movement_type='sale'):
    """A SECOND device's own sale merging in through the REAL sync apply
    path -- SyncService._apply_event -- reusing the exact technique
    retail_oversell_exception_test.py's own `_apply_incoming_movement`
    established for the ORIGINAL 7d-i writer. Used by section 10 below to
    prove the local path (this file's own subject) and the merge path
    (7d-i's own subject) really do share ONE recorder, not two."""
    conn = sch.get_retail_conn()
    try:
        service = SyncService(client_factory=lambda: None, get_conn=sch.get_retail_conn,
                               local_company_id_provider=lambda: cid,
                               stock_exception_recorder=sch.record_or_refresh_stock_exception)
        service._apply_event(conn, {
            'entity_type': 'inventory_movement', 'event_type': 'create',
            'payload': {
                'uid': str(uuid.uuid4()), 'product_id': product_id, 'branch_uid': branch_uid,
                'movement_type': movement_type, 'quantity': quantity, 'unit_cost': 0,
                'reference': 'OTHER-DEVICE', 'notes': None, 'created_by': 'System',
                'actor_user_uid': None, 'terminal_id': None, 'created_at_utc': None,
            },
        }, local_company_id=cid)
        conn.commit()
    finally:
        conn.close()


# ═════════════════════════════════════════════════════════════════════════
# HALF 1 -- THE ALLOW/SAFETY HALF, proved first (this stage relaxes a real
# refusal, so the cases where it must NOT relax matter more than the case
# where it does).
# ═════════════════════════════════════════════════════════════════════════

def test_a_shop_with_sync_switched_off_still_refuses_to_oversell():
    """THE single most important test in this file. No SyncService is ever
    registered here -- `_sync_get_active_health()` therefore reports
    `{"configured": False}`, exactly the majority-install shape (sync never
    turned on). This device's balance is the ONLY ledger there is, so the
    hard refusal must be provably intact -- not merely a status code, but
    stock and the sales row genuinely untouched."""
    admin, cid = _new_shop()
    pid = _create_product(admin, initial_stock=1)
    before_stock = _balance(cid, pid)
    before_sales = _sales_count(cid)

    r = _sell(admin, pid, qty=2)
    assert r.status_code == 400, r.get_json()
    assert 'Insufficient stock' in r.get_json()['message'], r.get_json()

    assert _balance(cid, pid) == before_stock, 'a refused oversell moved stock anyway'
    assert _sales_count(cid) == before_sales, 'a refused oversell wrote a sales row anyway'
    assert _exceptions_for(cid, product_id=pid) == [], (
        'a refused sale on an unconfigured install recorded a stock exception -- '
        'nothing was ever relaxed here, so nothing should ever be written')


def test_a_device_that_has_never_synced_still_refuses_to_oversell():
    """A SyncService IS registered (configured=True) but has never recorded
    a success -- `never_synced=True`. Must never be read as "behind"."""
    admin, cid = _new_shop()
    pid = _create_product(admin, initial_stock=1)
    service = _real_sync_service()
    register_active_service(service)

    health = service.get_health()
    assert health['configured'] is True
    assert health['never_synced'] is True

    before_stock = _balance(cid, pid)
    r = _sell(admin, pid, qty=2)
    assert r.status_code == 400, r.get_json()
    assert _balance(cid, pid) == before_stock
    assert _exceptions_for(cid, product_id=pid) == []


def test_the_never_synced_guard_is_independently_load_bearing_for_oversell(monkeypatch):
    """ADVERSARIAL PROBE, matching retail_offline_sales_stop_test.py's own
    section 1b technique exactly. A real `get_active_health()` snapshot
    NEVER pairs `never_synced=True` with a populated `seconds_since_last_
    success` -- `_elapsed_since_last_success` always sets that field to
    None when never_synced is True -- so the natural test above stays green
    even with the `never_synced` guard deleted from `_is_device_behind_on_
    sync`, because the downstream `isinstance(secs, (int, float))` check
    independently fails on the same missing field (proven by actually
    running that mutation -- see this stage's own mutation proof 2). This
    probe hand-crafts the combination a real SyncService can never produce,
    specifically to isolate the `never_synced` guard on its own."""
    admin, cid = _new_shop()
    pid = _create_product(admin, initial_stock=1)
    monkeypatch.setattr(retail_api, '_sync_get_active_health', lambda: {
        'configured': True, 'never_synced': True,
        'seconds_since_last_success': SYNC_STALE_THRESHOLD_SECONDS + 1,
        'offline_override_valid': False, 'pending_count': 0,
    })
    before_stock = _balance(cid, pid)
    r = _sell(admin, pid, qty=2)
    assert r.status_code == 400, r.get_json()
    assert _balance(cid, pid) == before_stock
    assert _exceptions_for(cid, product_id=pid) == []


def test_a_recently_synced_device_still_refuses_to_oversell():
    """Being CONFIGURED is not being BEHIND. A real success, recorded
    through the actual production method, well inside the 30-minute
    staleness threshold."""
    admin, cid = _new_shop()
    pid = _create_product(admin, initial_stock=1)
    service = _real_sync_service()
    register_active_service(service)
    service._record_sync_success('push')
    service._record_sync_success('pull')

    health = service.get_health()
    assert health['never_synced'] is False
    assert health['seconds_since_last_success'] < 60

    before_stock = _balance(cid, pid)
    r = _sell(admin, pid, qty=2)
    assert r.status_code == 400, r.get_json()
    assert _balance(cid, pid) == before_stock
    assert _exceptions_for(cid, product_id=pid) == []


def test_a_behind_device_still_sells_normally_when_stock_covers_the_sale():
    """The relaxation must not disturb the ordinary path: a behind device
    whose balance genuinely covers the sale sells exactly as it always
    has, and the response says nothing was sold past the recorded figure."""
    admin, cid = _new_shop()
    pid = _create_product(admin, initial_stock=5)
    _mark_behind(2)
    service = _real_sync_service()
    register_active_service(service)
    assert service.get_health()['seconds_since_last_success'] > SYNC_STALE_THRESHOLD_SECONDS
    assert service.get_health()['seconds_since_last_success'] < SYNC_SALES_STOP_THRESHOLD_SECONDS

    r = _sell(admin, pid, qty=3)
    assert r.status_code == 200, r.get_json()
    assert r.get_json()['data']['oversold_past_recorded_stock'] is False

    assert _balance(cid, pid) == 2
    assert _exceptions_for(cid, product_id=pid) == []


# ═════════════════════════════════════════════════════════════════════════
# HALF 2 -- the relaxation itself, and its recording.
# ═════════════════════════════════════════════════════════════════════════

def test_a_behind_device_can_sell_past_a_stale_zero():
    """THE case this stage exists for: this device's own cache says zero,
    the cashier is holding the item, and the device knows it might be
    behind another till's delivery or return."""
    admin, cid = _new_shop()
    pid = _create_product(admin, initial_stock=0)
    _mark_behind(2)
    register_active_service(_real_sync_service())

    before_sales = _sales_count(cid)
    r = _sell(admin, pid, qty=1)
    assert r.status_code == 200, r.get_json()

    assert _sales_count(cid) == before_sales + 1, 'the relaxed sale did not actually write a sales row'
    assert _balance(cid, pid) == -1, 'the relaxed sale did not actually move stock'


def test_that_sale_records_a_stock_exception():
    """Names the right product, branch and negative quantity."""
    admin, cid = _new_shop()
    pid = _create_product(admin, initial_stock=0)
    bid, _buid = _branch_info(cid)
    _mark_behind(2)
    register_active_service(_real_sync_service())

    r = _sell(admin, pid, qty=1)
    assert r.status_code == 200, r.get_json()
    assert _balance(cid, pid, bid) == -1

    rows = _exceptions_for(cid, product_id=pid)
    assert len(rows) == 1, rows
    row = rows[0]
    assert row['company_id'] == cid
    assert row['product_id'] == pid
    assert row['branch_id'] == bid
    assert row['observed_quantity_on_hand'] == -1
    assert row['resolved_at_utc'] is None
    assert row['detected_at_utc']


def test_the_response_says_the_sale_went_past_the_recorded_figure():
    admin, cid = _new_shop()
    pid = _create_product(admin, initial_stock=0)
    _mark_behind(2)
    register_active_service(_real_sync_service())

    r = _sell(admin, pid, qty=1)
    assert r.status_code == 200, r.get_json()
    assert r.get_json()['data']['oversold_past_recorded_stock'] is True, (
        'a sale allowed past its recorded on-hand figure must say so in the '
        'response -- silently allowing it would contradict this stage\'s own '
        '"the sale must not be silent" requirement')


def test_a_behind_sale_that_lands_exactly_on_zero_records_no_exception():
    """Zero is not negative. Built as a TWO-line sale on purpose: product A
    is genuinely oversold (relaxed), which is what makes the write loop's
    SALE-level recheck fire at all; product B is sold perfectly ordinarily
    in the SAME sale, landing its own balance on exactly zero. A relaxed
    line can only ever land its OWN balance strictly negative (qty already
    exceeded on_hand to trigger the relaxation in the first place), so the
    only way to observe the "exactly zero" boundary of the `< 0` filter is
    a second, non-relaxed line sharing the sale with a relaxed one -- see
    create_sale's own write-loop comment for the same reasoning."""
    admin, cid = _new_shop()
    pid_a = _create_product(admin, initial_stock=1)   # will be oversold: qty 2 > on_hand 1
    pid_b = _create_product(admin, initial_stock=3)   # will be sold exactly to zero: qty 3 == on_hand 3
    _mark_behind(2)
    register_active_service(_real_sync_service())

    r = _sell_multi(admin, [
        {'product_id': pid_a, 'quantity': 2},
        {'product_id': pid_b, 'quantity': 3},
    ])
    assert r.status_code == 200, r.get_json()
    assert r.get_json()['data']['oversold_past_recorded_stock'] is True

    assert _balance(cid, pid_a) == -1
    assert _balance(cid, pid_b) == 0, 'the fixture did not actually land product B exactly on zero'

    assert _exceptions_for(cid, product_id=pid_a) != [], (
        'the genuinely oversold product in this sale recorded no exception -- '
        'the fixture is not driving the scenario this test claims to be driving')
    assert _exceptions_for(cid, product_id=pid_b) == [], (
        'a perfectly ordinary line that merely landed on zero, sharing a sale with '
        'a genuinely relaxed line, recorded an exception -- zero is not an oversell')


def test_a_second_such_sale_refreshes_the_open_exception_instead_of_adding_one():
    """7d-i's one-open-row rule, now reached from the LOCAL path."""
    admin, cid = _new_shop()
    pid = _create_product(admin, initial_stock=0)
    _mark_behind(2)
    register_active_service(_real_sync_service())

    assert _sell(admin, pid, qty=1).status_code == 200
    first = _exceptions_for(cid, product_id=pid)
    assert len(first) == 1, first
    first_id = first[0]['id']

    assert _sell(admin, pid, qty=1).status_code == 200
    assert _balance(cid, pid) == -2

    second = _exceptions_for(cid, product_id=pid)
    assert len(second) == 1, (
        f'a second oversold sale against the same product/branch appended a SECOND '
        f'exception instead of refreshing the open one: {second}')
    assert second[0]['id'] == first_id, 'the refresh created a new row instead of updating the existing one'
    assert second[0]['observed_quantity_on_hand'] == -2


def test_the_recorded_exception_is_the_same_shape_the_sync_path_records():
    """Proves the local path (this stage) and the merge path (7d-i) really
    do share ONE implementation, not two copies of the same upsert.

    Strong proof first: the ACTIVE SyncService's own `stock_exception_
    recorder` collaborator IS, by object identity, `database/schema.py`'s
    `record_or_refresh_stock_exception` -- the exact function `create_sale`
    also imports and calls directly. Then a behavioural proof: one
    exception produced by each path has an identical column shape and both
    obey the SAME "one open row" contract."""
    admin, cid = _new_shop()
    pid_local = _create_product(admin, initial_stock=0)
    pid_merge = _create_product(admin, initial_stock=1)
    bid, buid = _branch_info(cid)
    _mark_behind(2)
    service = _real_sync_service()
    register_active_service(service)

    assert service._stock_exception_recorder is sch.record_or_refresh_stock_exception, (
        'the active SyncService is not wired to the SAME canonical recorder '
        'create_sale calls directly -- the two callers have drifted apart')

    # Local path (this stage): a behind device's own oversold sale.
    assert _sell(admin, pid_local, qty=1).status_code == 200
    assert _balance(cid, pid_local, bid) == -1

    # Merge path (7d-i, unchanged): another device's sale of the SAME
    # product's last unit, merging in and pushing this device's own,
    # perfectly ordinary, ALREADY-ZERO balance negative. Uses a service
    # NOT marked behind for the merge call itself (`_apply_incoming_
    # movement` builds its own fresh SyncService instance) -- the merge
    # writer's behaviour was never conditioned on staleness in the first
    # place, and stays that way.
    assert _sell(admin, pid_merge, qty=1).status_code == 200
    assert _balance(cid, pid_merge, bid) == 0
    _apply_incoming_movement(cid, pid_merge, buid, quantity=-1)
    assert _balance(cid, pid_merge, bid) == -1

    local_rows = _exceptions_for(cid, product_id=pid_local)
    merge_rows = _exceptions_for(cid, product_id=pid_merge)
    assert len(local_rows) == 1, local_rows
    assert len(merge_rows) == 1, merge_rows

    local_row, merge_row = local_rows[0], merge_rows[0]
    assert set(local_row.keys()) == set(merge_row.keys()), (
        'the local-path row and the merge-path row have different columns -- '
        'the two callers are not really sharing one table shape')
    for key in ('company_id', 'branch_id', 'observed_quantity_on_hand'):
        assert local_row[key] == merge_row[key], (key, local_row, merge_row)
    assert local_row['resolved_at_utc'] is None
    assert merge_row['resolved_at_utc'] is None
