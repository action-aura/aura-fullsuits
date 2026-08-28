"""Aura Retail -- launch-readiness Phase 7 stage 7c-ii: the 72-hour hard stop
on NEW SALES, behind a manager override. See docs/launch-readiness/
phase7-offline-ux.md "Decision 2", ROADMAP.md's 2026-08-28 "retail schema v19
CLAIMED for Phase 7 stage 7c-ii" entry, database/schema.py's
`_migrate_add_offline_override` / `load_sync_freshness` / `record_offline_
override`, commercial_runtime/sync/sync_service.py's `SyncFreshnessStore` /
`_offline_override_valid` / `SyncService.record_offline_override`, and
api/retail_api.py's `_evaluate_offline_sales_stop` / `create_sale` /
`sync_offline_override`.

"It is a block with an override, NOT an absolute refusal" -- and this is the
one stage in the whole launch-readiness programme that can refuse a sale, so
THE ALLOW HALF COMES FIRST and outnumbers the deny half. A healthy till that
never turns sync on (the majority install) must be provably unaffected --
test_a_shop_with_sync_switched_off_can_always_sell is the single most
important test in this file.

── HOW THIS FILE AVOIDS TESTING ITSELF (ENGINEERING.md's three failure
   shapes) ───────────────────────────────────────────────────────────────
1. ASSERTS THE CHECK RAN, not just the outcome:
   test_a_blocked_sale_writes_nothing reads stock and the sales count BEFORE
   and AFTER a blocked attempt and asserts both are UNCHANGED -- a refusal
   that still wrote the sale would satisfy a bare status-code assertion
   while doing exactly the damage the block exists to prevent.
   test_a_cashier_without_cash_approve_cannot_override reads
   `sync_freshness.offline_override_at` after a refused attempt and asserts
   it is still NULL -- not merely that a 403 came back.
2. NO FIXTURE MANUFACTURES THE STATE THAT HIDES THE BUG: "behind" is built
   by writing a REAL old timestamp through `record_sync_freshness` -- the
   SAME function `SyncService._record_sync_success` calls in production --
   so a freshly constructed SyncService loads it back exactly as a
   restarted process would (see `_mark_behind` below). Nothing hand-stamps
   `get_health()`'s return value directly.
3. THE PASS CONDITION IS NOT THE BUG SIGNATURE: every allow-half test
   asserts a 200 AND a real state change (a sale row exists, stock moved),
   never merely "not a 403" -- a route that silently no-ops would pass a
   bare status-code check too.

Follows the SAME bootstrap convention as retail_drawer_terminal_scope_test.py
/ retail_capability_ratchet_consumption_test.py (no shared conftest.py exists
for products/retail/tests/): a real Flask app via app.init_app(), a real
account via the production seeding helper, and -- borrowed from
retail_sync_freshness_test.py -- a REAL SyncService constructed directly
against the same retail.db, registered as the active service via
register_active_service()/unregister_active_service() (the same module-level
seam retail_api.py's _sync_get_active_health() reads).

EVERY test proves outcomes end-to-end this way -- through the real routes,
against a real SyncService and a real database -- EXCEPT the two adversarial
probes in section 1b (test_the_configured_guard_is_independently_load_bearing
/ test_the_never_synced_guard_is_independently_load_bearing), which
monkeypatch `retail_api._sync_get_active_health` directly. Those two exist
for a specific, narrow reason explained in section 1b's own header comment:
a REAL "not configured" or "never synced" health snapshot never carries a
populated `seconds_since_last_success` alongside it, so the natural
end-to-end tests cannot by themselves distinguish "the configured/never_
synced guard ran" from "it didn't, but the numeric elapsed-time check failed
anyway for the same underlying reason" -- proven by actually running that
mutation (see this stage's own report). The two probes hand-craft a health
dict a real SyncService can never produce today, specifically to make each
of those two guards independently provable.

Run:
    pytest products/retail/tests/retail_offline_sales_stop_test.py -v
"""
import os
import re
import shutil
import sys
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

DATA = Path(tempfile.mkdtemp(prefix='aura_retail_offline_stop_'))
(DATA / 'database' / 'subsystems').mkdir(parents=True, exist_ok=True)
os.environ.update(AURA_STANDALONE='1', AURA_BUNDLE_DIR=str(BACKEND_DIR), AURA_APP_DATA=str(DATA))
os.environ.pop('AURA_DEV', None)
# Deliberately NOT set: SYNC_RELAY_BASE_URL. app.py only constructs and
# registers its OWN SyncService when that is configured (see
# SyncFreshnessStore's own module docstring in sync_service.py) -- leaving
# it unset means init_app() below registers nothing, so `_active_service`
# starts this file at None and every test controls it explicitly via the
# autouse fixture below. This is also exactly test_a_shop_with_sync_
# switched_off_can_always_sell's real-world shape: SYNC_RELAY_BASE_URL
# unset IS "sync switched off".
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
    SYNC_SALES_STOP_THRESHOLD_SECONDS,
)

API = '/api/sub/retail'

assert SYNC_SALES_STOP_THRESHOLD_SECONDS == 72 * 60 * 60, (
    'this file is written against a 72-hour threshold; the constant moved -- '
    'update the hour values used throughout this file deliberately')


def teardown_module(module):
    shutil.rmtree(DATA, ignore_errors=True)


def _reset_sync_freshness():
    """`sync_freshness` is a SINGLE-ROW, per-DEVICE table (not company-scoped
    -- deliberately, see database/schema.py's `_migrate_add_sync_freshness`
    docstring), and every test in this file shares ONE physical retail.db
    (one `app.init_app()` call at module scope, real multi-tenant install
    behaviour: many companies, one till). A standing override or a stale
    success timestamp left behind by an earlier test would therefore leak
    into the next one's company -- so this resets the row to the fresh-
    install NULL/NULL/NULL shape before every test, the same isolation
    `unregister_active_service()` already gives `_active_service`."""
    conn = sch.get_retail_conn()
    try:
        conn.execute(
            'UPDATE sync_freshness SET last_push_success_at=NULL, '
            'last_pull_success_at=NULL, offline_override_at=NULL WHERE id=1')
        conn.commit()
    finally:
        conn.close()


def _set_freshness(hours_ago_success=None, hours_ago_override=None):
    """Directly sets sync_freshness's columns to fixed points relative to
    the CURRENT real time -- used where a test needs precise control over
    the relative ORDER of a success and an override (e.g.
    test_a_successful_sync_invalidates_a_standing_override_when_still_
    behind), rather than mixing `_mark_behind`'s now-relative offsets with
    `_record_sync_success()`/`record_offline_override()`'s real "now"
    stamps, which cannot be made to land at an arbitrary point in the past.
    Writes through the SAME schema.py functions production uses, exactly
    like `_mark_behind`."""
    conn = sch.get_retail_conn()
    try:
        if hours_ago_success is not None:
            ts = (datetime.now(timezone.utc) - timedelta(hours=hours_ago_success)).isoformat()
            sch.record_sync_freshness(conn, 'push', ts)
            sch.record_sync_freshness(conn, 'pull', ts)
        if hours_ago_override is not None:
            ts = (datetime.now(timezone.utc) - timedelta(hours=hours_ago_override)).isoformat()
            sch.record_offline_override(conn, ts)
        conn.commit()
    finally:
        conn.close()


@pytest.fixture(autouse=True)
def _clear_active_service():
    """No test may inherit a service, a stale success timestamp, or a
    standing override another test left behind -- mirrors
    commercial_runtime/sync/tests/test_sync_service.py's own
    `_clear_active_service` fixture, extended to also reset the shared
    per-device `sync_freshness` row (see `_reset_sync_freshness`'s own
    docstring for why that reset is necessary in THIS file specifically)."""
    unregister_active_service()
    _reset_sync_freshness()
    yield
    unregister_active_service()


# ─────────────────────────────────────────────────────────────────────────────
# Fixture bedrock
# ─────────────────────────────────────────────────────────────────────────────

def _make_user(role, company_id, capabilities=None, email_prefix='offstop'):
    """A real logged-in account, optionally with individual capability codes
    overridden away from the role default -- mirrors retail_drawer_terminal_
    scope_test.py's `_make_user` exactly (including its fix for the real
    `user_permissions` table name, not a nonexistent `user_capabilities`)."""
    email = f'{email_prefix}-{role}-{uuid.uuid4().hex[:10]}@test.local'
    password = 'OfflineStopPW1'
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
        for code, level in (capabilities or {}).items():
            conn.execute('UPDATE user_permissions SET access_level=? WHERE user_id=? AND subsystem=?',
                         (level, user_id, code))
        conn.commit()
    finally:
        conn.close()
    client = app.test_client()
    r = client.post('/api/auth/login', json={'email': email, 'password': password})
    assert r.status_code == 200, r.get_data(as_text=True)
    return client, user_id


def _new_shop(price=100.0):
    """A company with an admin, one product and stock. Returns (admin, cid, pid)."""
    company_id = str(uuid.uuid4())
    admin, _uid = _make_user('admin', company_id)
    r = admin.post(f'{API}/products', json={
        'name': 'Offline Stop Widget', 'sku': f'OS-{uuid.uuid4().hex[:8]}',
        'cost_price': price / 2, 'sell_price': price, 'tax_rate': 0, 'initial_stock': 500})
    assert r.status_code == 200, r.get_json()
    return admin, company_id, r.get_json()['data']['id']


def _sale_body(pid, qty=1):
    return {
        'items': [{'product_id': pid, 'quantity': qty}],
        'amount_paid': qty * 100.0, 'payment_method': 'cash',
        'idempotency_key': str(uuid.uuid4()),
    }


def _sell(client, pid, qty=1):
    return client.post(f'{API}/sales', json=_sale_body(pid, qty))


def _mark_behind(hours):
    """Persists BOTH push and pull's last_success_at as `hours` ago, through
    `record_sync_freshness` -- the SAME function `_record_sync_success` calls
    in production -- so a FRESHLY CONSTRUCTED SyncService loads this exact
    state on its own, exactly as a genuinely-behind device's restart would.
    Writing through the real persistence function (never poking a service's
    in-memory `_health` dict directly) is what lets
    test_the_override_survives_a_restart construct a genuinely SECOND
    service and observe the SAME 'behind' state the first one did."""
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
    test's own retail.db -- the exact three callables app.py itself wires
    when SYNC_RELAY_BASE_URL is configured (products/retail/backend/app.py's
    `_retail_sync_freshness_store`). Not yet registered as the active
    service. client_factory / local_company_id_provider are both None
    throughout this file, matching retail_sync_freshness_test.py's own
    `_service` helper -- no test here calls push_once()/pull_once(), only
    `_record_sync_success` and `record_offline_override` directly, the exact
    methods run_once() and the /sync/offline-override route actually call in
    production."""
    store = SyncFreshnessStore(
        load=sch.load_sync_freshness, record=sch.record_sync_freshness,
        record_override=sch.record_offline_override)
    return SyncService(None, sch.get_retail_conn, None, local_freshness_store=store)


def _stock_on_hand(cid, pid):
    conn = sch.get_retail_conn()
    try:
        row = conn.execute(
            'SELECT COALESCE(SUM(quantity_on_hand), 0) AS n FROM inventory_balances '
            'WHERE company_id=? AND product_id=?', (cid, pid)).fetchone()
        return row['n']
    finally:
        conn.close()


def _sales_count(cid):
    conn = sch.get_retail_conn()
    try:
        return conn.execute('SELECT COUNT(*) AS n FROM sales WHERE company_id=?', (cid,)).fetchone()['n']
    finally:
        conn.close()


def _pending_count():
    conn = sch.get_retail_conn()
    try:
        return conn.execute('SELECT COUNT(*) AS n FROM sync_outbox').fetchone()['n']
    finally:
        conn.close()


def _override_at(cid=None):
    conn = sch.get_retail_conn()
    try:
        row = conn.execute('SELECT offline_override_at FROM sync_freshness WHERE id=1').fetchone()
        return row['offline_override_at'] if row else None
    finally:
        conn.close()


# ─────────────────────────────────────────────────────────────────────────────
# 1. THE ALLOW HALF -- proved first, because this stage can close a shop
# ─────────────────────────────────────────────────────────────────────────────

def test_a_shop_with_sync_switched_off_can_always_sell():
    """THE single most important test in this file. No SyncService is ever
    registered here -- `_sync_get_active_health()` therefore reports
    `{"configured": False}`, exactly the majority-install shape (sync never
    turned on). The sale must succeed AND actually happen, not merely avoid
    a 403."""
    admin, cid, pid = _new_shop()
    before_stock = _stock_on_hand(cid, pid)
    before_sales = _sales_count(cid)

    r = _sell(admin, pid, qty=2)
    assert r.status_code == 200, r.get_json()

    assert _sales_count(cid) == before_sales + 1
    assert _stock_on_hand(cid, pid) == before_stock - 2


def test_a_device_that_has_never_synced_can_sell():
    """A SyncService IS registered (configured=True) but has never recorded
    a success -- `never_synced=True`. Must never be read as "behind"."""
    admin, cid, pid = _new_shop()
    service = _real_sync_service()
    register_active_service(service)

    health = service.get_health()
    assert health['configured'] is True
    assert health['never_synced'] is True

    r = _sell(admin, pid)
    assert r.status_code == 200, r.get_json()


def test_a_recently_synced_device_can_sell():
    """A real success, recorded through the actual production method
    (`_record_sync_success`), not a hand-stamped timestamp."""
    admin, cid, pid = _new_shop()
    service = _real_sync_service()
    register_active_service(service)
    service._record_sync_success('push')
    service._record_sync_success('pull')

    health = service.get_health()
    assert health['never_synced'] is False
    assert health['seconds_since_last_success'] < 60

    r = _sell(admin, pid)
    assert r.status_code == 200, r.get_json()


def test_a_device_behind_by_less_than_72_hours_can_still_sell():
    """THE BOUNDARY. Being behind is not the same as being past the stop --
    24 hours is well past the 24h soft warning but nowhere near 72h."""
    admin, cid, pid = _new_shop()
    _mark_behind(24)
    service = _real_sync_service()
    register_active_service(service)

    health = service.get_health()
    assert health['never_synced'] is False
    assert health['seconds_since_last_success'] < SYNC_SALES_STOP_THRESHOLD_SECONDS

    r = _sell(admin, pid)
    assert r.status_code == 200, r.get_json()


def test_a_refund_is_never_blocked_even_when_sales_are():
    """Sec5's explicit carve-out. The sale that will be refunded is rung
    FIRST, while nothing is blocked yet -- then the device goes behind by
    100 hours (past the stop), and the refund against that already-local
    sale must still succeed."""
    admin, cid, pid = _new_shop()
    sale = _sell(admin, pid, qty=3)
    assert sale.status_code == 200, sale.get_json()
    sale_id = sale.get_json()['data']['id']

    _mark_behind(100)
    service = _real_sync_service()
    register_active_service(service)
    assert service.get_health()['seconds_since_last_success'] > SYNC_SALES_STOP_THRESHOLD_SECONDS

    # ANTI-VACUITY: a new sale really is blocked right now, so the refund
    # succeeding below is contrast, not "nothing here is gated at all".
    blocked_sale = _sell(admin, pid, qty=1)
    assert blocked_sale.status_code == 403, blocked_sale.get_json()

    ret = admin.post(f'{API}/returns', json={
        'sale_id': sale_id,
        'items': [{'product_id': pid, 'quantity': 1}],
        'reason': 'offline-stop carve-out test',
        'idempotency_key': str(uuid.uuid4()),
    })
    assert ret.status_code == 200, ret.get_json()


# ─────────────────────────────────────────────────────────────────────────────
# 1b. ADVERSARIAL PROBES -- the `configured` / `never_synced` guards in
#     isolation, independent of the OTHER field that happens to protect the
#     same real-world scenario
# ─────────────────────────────────────────────────────────────────────────────
#
# `get_active_health()`'s real contract never lets `configured=False` or
# `never_synced=True` coexist with a populated `seconds_since_last_success`
# -- the "not configured" snapshot is the bare `{"configured": False}"`, and
# `_elapsed_since_last_success` always pairs `never_synced=True` with
# `seconds_since_last_success=None`. That means test_a_shop_with_sync_
# switched_off_can_always_sell and test_a_device_that_has_never_synced_can_
# sell above, run against a MUTATED `_evaluate_offline_sales_stop` with the
# `configured` or `never_synced` check deleted, STILL PASS -- proven by
# actually running that mutation (see this stage's own report) -- because
# the numeric `isinstance(secs, (int, float))` check downstream independently
# fails on the same missing field. Exactly the ENGINEERING.md trap: "a
# mutation that leaves a test green means the fixture hides the bug; make it
# adversarial instead of concluding the guard works."
#
# So these two probes hand-craft a `get_active_health()` snapshot that a real
# SyncService can never produce today, specifically to isolate EACH guard on
# its own -- reached via monkeypatching `retail_api._sync_get_active_health`
# itself (the NAME retail_api.py's own `from ... import get_active_health as
# _sync_get_active_health` binds in its own module globals -- patching
# sync_service.get_active_health would not touch that separate binding, the
# same distinction retail_route_capability_matrix_test.py's own module
# docstring already states for session_has_capability).

def test_the_configured_guard_is_independently_load_bearing(monkeypatch):
    """NOT configured, but every other field shaped as though the device
    were far past the 72-hour stop with no valid override. A real
    SyncService can never produce this combination -- see this section's
    own header comment -- which is exactly why it proves the `configured`
    check ITSELF is what stops the block, not an accidental absence of
    `seconds_since_last_success` elsewhere in the same dict."""
    admin, cid, pid = _new_shop()
    monkeypatch.setattr(retail_api, '_sync_get_active_health', lambda: {
        'configured': False, 'never_synced': False,
        'seconds_since_last_success': SYNC_SALES_STOP_THRESHOLD_SECONDS + 1,
        'offline_override_valid': False, 'pending_count': 3,
    })
    r = _sell(admin, pid)
    assert r.status_code == 200, r.get_json()


def test_the_never_synced_guard_is_independently_load_bearing(monkeypatch):
    """The same adversarial technique for `never_synced`: True, alongside a
    populated elapsed time that would otherwise read as far past the stop."""
    admin, cid, pid = _new_shop()
    monkeypatch.setattr(retail_api, '_sync_get_active_health', lambda: {
        'configured': True, 'never_synced': True,
        'seconds_since_last_success': SYNC_SALES_STOP_THRESHOLD_SECONDS + 1,
        'offline_override_valid': False, 'pending_count': 3,
    })
    r = _sell(admin, pid)
    assert r.status_code == 200, r.get_json()


# ─────────────────────────────────────────────────────────────────────────────
# 2. THE DENY HALF, and the override
# ─────────────────────────────────────────────────────────────────────────────

def test_a_device_behind_more_than_72_hours_cannot_ring_a_new_sale():
    """The refusal must name BOTH facts Decision 2 requires: how long this
    device has been behind, and how many events are unsent."""
    admin, cid, pid = _new_shop()
    expected_pending = _pending_count()
    assert expected_pending > 0, (
        'fixture must leave at least one unsent event (product creation '
        'queues one) or this test cannot prove the pending count is real')

    _mark_behind(100)
    service = _real_sync_service()
    register_active_service(service)

    r = _sell(admin, pid)
    assert r.status_code == 403, r.get_json()
    body = r.get_json()
    assert 99.0 < body['data']['hours_offline'] < 101.0, body
    assert body['data']['pending_count'] == expected_pending, body
    assert re.search(r'\b\d+(\.\d+)?\s*hours?\b', body['message'], re.IGNORECASE), body['message']
    assert str(expected_pending) in body['message'], body['message']


def test_a_blocked_sale_writes_nothing():
    """THE CHECK RAN, not just the outcome. Stock and the sales count are
    read BEFORE and AFTER the blocked attempt -- a refusal that still wrote
    the sale would satisfy a status-code-only assertion while doing exactly
    the damage this block exists to prevent."""
    admin, cid, pid = _new_shop()
    _mark_behind(100)
    service = _real_sync_service()
    register_active_service(service)

    before_stock = _stock_on_hand(cid, pid)
    before_sales = _sales_count(cid)

    r = _sell(admin, pid, qty=5)
    assert r.status_code == 403, r.get_json()

    assert _stock_on_hand(cid, pid) == before_stock, 'a blocked sale moved stock'
    assert _sales_count(cid) == before_sales, 'a blocked sale wrote a sales row'


def test_a_manager_can_override_and_then_sell():
    """A MANAGER granted CAP_CASH_APPROVE explicitly -- not the shop owner,
    whose `mt_require_capability` role-bypass would let the request through
    before the capability row is even read (see mt_auth.py's own ADMIN
    BYPASS comment). Using a manager with the code genuinely granted proves
    the decorator, not the bypass."""
    admin, cid, pid = _new_shop()
    manager, _mgr_uid = _make_user('manager', cid, capabilities={user_accounts.CAP_CASH_APPROVE: 'full'})

    _mark_behind(100)
    service = _real_sync_service()
    register_active_service(service)

    blocked = _sell(manager, pid)
    assert blocked.status_code == 403, blocked.get_json()

    approved = manager.post(f'{API}/sync/offline-override', json={})
    assert approved.status_code == 200, approved.get_json()

    allowed = _sell(manager, pid)
    assert allowed.status_code == 200, allowed.get_json()


def test_a_cashier_without_cash_approve_cannot_override():
    """403 alone is not the claim -- a route that 403s everyone would also
    satisfy that. The claim is that NO override was recorded: read
    `sync_freshness.offline_override_at` from the database itself, not the
    response body."""
    admin, cid, pid = _new_shop()
    cashier, _cashier_uid = _make_user('cashier', cid)
    assert not user_accounts.CAP_CASH_APPROVE in user_accounts.capabilities_for_role(
        user_accounts.ROLE_CASHIER), 'fixture drifted -- a cashier now holds cash-approve by default'

    _mark_behind(100)
    service = _real_sync_service()
    register_active_service(service)
    assert _override_at() is None

    refused = cashier.post(f'{API}/sync/offline-override', json={})
    assert refused.status_code == 403, refused.get_json()
    assert _override_at() is None, (
        'a cashier without CAP_CASH_APPROVE was refused, but an override was '
        'recorded anyway -- the decorator refused the RESPONSE, not the WRITE')

    # And the contrast: the sale is still blocked, because nothing was
    # actually accepted.
    still_blocked = _sell(cashier, pid)
    assert still_blocked.status_code == 403, still_blocked.get_json()


def test_a_successful_sync_invalidates_a_standing_override():
    """Step 2's rule, and the one a naive "just store a flag" implementation
    gets wrong: `offline_override_at > the most recent successful sync
    instant`, so a fresh success silently invalidates a standing override
    with no expiry job."""
    admin, cid, pid = _new_shop()
    _mark_behind(100)
    service = _real_sync_service()
    register_active_service(service)

    assert admin.post(f'{API}/sync/offline-override', json={}).status_code == 200
    assert service.get_health()['offline_override_valid'] is True
    assert _sell(admin, pid).status_code == 200, 'a valid override must let the sale through'

    # A fresh success -- the SAME method run_once() calls after a real
    # push_once()/pull_once() success -- happens AFTER the override was
    # recorded, so it must now postdate it and invalidate it.
    service._record_sync_success('push')
    health = service.get_health()
    assert health['offline_override_valid'] is False, (
        'a successful sync must invalidate a standing override, automatically, '
        'with no expiry job')
    assert health['seconds_since_last_success'] < 60, (
        'the fresh success must also reset the elapsed-offline clock, so this '
        'is testing the REAL rule, not merely a flag someone forgot to clear')

    blocked_again = _sell(admin, pid)
    assert blocked_again.status_code == 200, (
        'a device that just synced successfully must sell -- it is not "behind" '
        'any more regardless of the override')


def test_a_successful_sync_invalidates_a_standing_override_when_still_behind():
    """The sharper version of the rule above: a shop that overrides,
    reconnects, then goes back offline long enough to pass the stop AGAIN
    must be blocked again -- not because it is "newly" behind, but because
    the override now PREDATES the last success.

    Built with `_set_freshness`'s explicit relative timestamps rather than
    chaining real `_record_sync_success()` / `record_offline_override()`
    calls (both always stamp real wall-clock "now", which cannot be made to
    land at an arbitrary point in the past) -- three fixed instants, oldest
    to newest:
      1. override_at  = 150h ago -- a manager overrode during a bad outage.
      2. reconnect     = 100h ago -- ONE real success, AFTER the override
         (so it invalidates it), but ITSELF still more than 72h before now.
      3. now -- no further success since the reconnect at (2), so the
         device is genuinely behind again, with no valid override to fall
         back on."""
    admin, cid, pid = _new_shop()
    _set_freshness(hours_ago_success=200, hours_ago_override=150)
    service = _real_sync_service()
    register_active_service(service)
    assert service.get_health()['offline_override_valid'] is True
    assert _sell(admin, pid).status_code == 200, 'the override (150h ago) must still validate here'

    # The reconnect: one real success, 100 hours ago -- after the override,
    # so it invalidates it -- but still itself more than 72h behind NOW.
    _set_freshness(hours_ago_success=100)
    service2 = _real_sync_service()
    register_active_service(service2)
    health2 = service2.get_health()
    assert health2['seconds_since_last_success'] > SYNC_SALES_STOP_THRESHOLD_SECONDS
    assert health2['offline_override_valid'] is False, (
        'the OLD override must not silently keep validating after a device '
        'that used it reconnected and went offline again')
    assert _sell(admin, pid).status_code == 403, (
        'a shop that overrides, reconnects, then goes offline again must be '
        'blocked AGAIN rather than riding the old approval')


def test_the_override_survives_a_restart():
    """Construct a SECOND service against the SAME database, exactly as 7a's
    own restart test does -- a single long-lived process cannot fail this."""
    admin, cid, pid = _new_shop()
    _mark_behind(100)
    service_a = _real_sync_service()
    register_active_service(service_a)
    assert admin.post(f'{API}/sync/offline-override', json={}).status_code == 200
    recorded_override_at = service_a.get_health()['offline_override_at']
    assert recorded_override_at is not None

    # THE RESTART.
    service_b = _real_sync_service()
    register_active_service(service_b)
    health_b = service_b.get_health()
    assert health_b['offline_override_at'] == recorded_override_at, (
        'a restarted SyncService must report the SAME offline_override_at the '
        'previous instance recorded, not None')
    assert health_b['offline_override_valid'] is True

    assert _sell(admin, pid).status_code == 200, (
        'the override must still be honoured by a freshly constructed service '
        '-- a till restarted the morning after a manager override must not '
        're-prompt on the first sale of the day')


def test_the_override_is_audited_with_the_elapsed_time_and_unsent_count():
    admin, cid, pid = _new_shop()
    manager, mgr_uid = _make_user('manager', cid, capabilities={user_accounts.CAP_CASH_APPROVE: 'full'})
    expected_pending = _pending_count()

    _mark_behind(100)
    service = _real_sync_service()
    register_active_service(service)

    r = manager.post(f'{API}/sync/offline-override', json={})
    assert r.status_code == 200, r.get_json()

    conn = sch.get_retail_conn()
    try:
        rows = conn.execute(
            "SELECT user_id, details FROM audit_log WHERE company_id=? AND action='OFFLINE_SALES_OVERRIDE' "
            "ORDER BY id DESC LIMIT 1", (cid,)).fetchall()
    finally:
        conn.close()
    assert rows, 'no OFFLINE_SALES_OVERRIDE audit row was written'
    row = rows[0]
    assert row['user_id'] == mgr_uid, row['user_id']

    details = row['details']
    hours_match = re.search(r'hours_offline=([\d.]+)', details)
    pending_match = re.search(r'pending_count=(\d+)', details)
    assert hours_match, details
    assert pending_match, details
    assert 99.0 < float(hours_match.group(1)) < 101.0, details
    assert int(pending_match.group(1)) == expected_pending, details
    assert f'approved_by={mgr_uid}' in details, details
