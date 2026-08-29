"""
Aura Retail -- two lines for the SAME product in one sale must not jointly
exceed stock (launch-readiness Phase 7 stage 7d-iii follow-up, 2026-08-29).

THE BUG. `create_sale` (products/retail/backend/api/retail_api.py) validates
each line in a loop (`for item in items_in:`), re-reading `SELECT
quantity_on_hand FROM inventory_balances ...` and checking `if qty >
on_hand:` -- but the balance is not actually decremented until a SEPARATE
write loop that runs AFTER validation (`for line in resolved_lines:`,
further down). Every line in the validation loop therefore compares against
the SAME, still-untouched `on_hand` figure. Two lines of 3 units each
against 5 on hand each individually pass (3 <= 5) and jointly commit 6 --
one unit oversold that no single-line check can ever see.

Not reachable through the shipped web UI: `_addToCart` (subsystem-retail.js)
merges a repeat scan into the existing cart line rather than adding a
second one. It IS reachable by a direct API call, and by any other client
that does not merge -- the handler is the authoritative boundary in this
codebase (see retail_route_capability_matrix_test.py's own reasoning for
why a client-side behaviour is only ever a suggestion), so the fix belongs
in create_sale itself, not in trusting every client to merge.

THE FIX. Demand is now accumulated per (product_id, branch_id) -- the exact
key `inventory_balances` itself is scoped by, alongside `company_id` (which
is invariant for one request) -- across the validation loop, and the
RUNNING TOTAL is compared against `on_hand` instead of one line in
isolation. This composes with stage 7d-iii's `behind_on_sync` relaxation
unchanged: the accumulator only changes WHAT is compared to `on_hand`,
never WHEN the comparison refuses vs. relaxes (still gated purely on
`behind_on_sync`), and `sale_oversold_past_recorded_stock` is now set from
the SAME accumulated comparison, not a per-line one -- otherwise a relaxed
multi-line oversell would go unflagged and its resulting negative balance
would never reach `stock_exceptions`.

THIS ROUTE CANNOT CARRY LINES FOR MORE THAN ONE BRANCH. `create_sale`
resolves exactly one `bid` for the WHOLE sale, once, before the validation
loop (`bid = int(data.get('branch_id') or _default_branch(conn, cid))`) --
the per-item loop never reads `item.get('branch_id')` at all (only
`product_id`, `quantity`, `discount_pct`). Section 4 below pins this
directly: an item-level `branch_id` is proven to have NO effect on which
branch a line actually debits.

── HOW THIS FILE AVOIDS TESTING ITSELF (ENGINEERING.md's failure shapes),
   following retail_oversell_relaxation_test.py's own precedent ──────────
1. ASSERTS THE CHECK RAN, not just the outcome: the refusal test (section 1)
   reads stock and the sales count BEFORE and AFTER, and asserts BOTH are
   unchanged -- a 400 alone would pass even if the sale had already written
   damage before returning it.
2. THE ALLOW HALF IS TESTED TOO (section 2): a fix that refuses any repeat
   of the same product, rather than accumulating correctly, would pass
   section 1 while breaking a completely legitimate multi-line sale.
3. NO FIXTURE MANUFACTURES THE STATE THAT HIDES THE BUG: "behind" (section
   6) is built via `record_sync_freshness` -- the SAME function
   `SyncService._record_sync_success` calls in production -- exactly like
   retail_oversell_relaxation_test.py's own `_mark_behind`.
4. EVERY MUTATION PROOF IS ACTUALLY RUN (see the four RED/GREEN pairs
   documented in this session's report) -- reverting the fix, accumulating
   across products instead of per-product, refusing on any repeat, and
   decoupling the exception-recording flag from the accumulated check are
   each proven to flip the relevant test red before being reverted back.

Self-contained bootstrap, matching retail_oversell_relaxation_test.py (no
shared conftest.py exists here): SYNC_RELAY_BASE_URL deliberately left
UNSET so app.py's own module-scope SyncService is never constructed, and
every test controls `_active_service` explicitly via the autouse fixture
below.

Run:
    pytest products/retail/tests/retail_multiline_stock_test.py -v
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

DATA = Path(tempfile.mkdtemp(prefix='aura_retail_multiline_stock_'))
(DATA / 'database' / 'subsystems').mkdir(parents=True, exist_ok=True)
os.environ.update(AURA_STANDALONE='1', AURA_BUNDLE_DIR=str(BACKEND_DIR), AURA_APP_DATA=str(DATA))
os.environ.pop('AURA_DEV', None)
# Deliberately NOT set -- see retail_oversell_relaxation_test.py's own
# module docstring for why leaving this unset is what makes every test here
# start from "sync switched off" unless it explicitly registers a service.
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
)

API = '/api/sub/retail'


def teardown_module(module):
    shutil.rmtree(DATA, ignore_errors=True)


# ── Fixtures / helpers (deliberately duplicated per-file convention -- see
#    retail_oversell_relaxation_test.py's own module docstring for why
#    nothing here is shared via a conftest.py) ─────────────────────────────

def _reset_sync_freshness():
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
    unregister_active_service()
    _reset_sync_freshness()
    yield
    unregister_active_service()


def _make_user(role, company_id, email_prefix='multiline'):
    email = f'{email_prefix}-{role}-{uuid.uuid4().hex[:10]}@test.local'
    password = 'MultilineStockPW1'
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
    company_id = str(uuid.uuid4())
    admin, _uid = _make_user('admin', company_id)
    return admin, company_id


def _create_product(client, initial_stock=0):
    sku = f'MLS-{uuid.uuid4().hex[:8]}'
    r = client.post(f'{API}/products', json={
        'name': f'Multiline Stock Widget {sku}', 'sku': sku, 'sell_price': 10.0,
        'cost_price': 5.0, 'tax_rate': 0, 'initial_stock': initial_stock,
    })
    assert r.status_code == 200, r.get_json()
    return r.get_json()['data']['id']


def _sale_payload(items, amount_paid=None, branch_id=None):
    total_qty = sum(i['quantity'] for i in items)
    payload = {
        'items': items,
        'amount_paid': amount_paid if amount_paid is not None else total_qty * 1000.0,
        'payment_method': 'cash',
        'idempotency_key': str(uuid.uuid4()),
    }
    if branch_id is not None:
        payload['branch_id'] = branch_id
    return payload


def _sell_multi(client, items, branch_id=None):
    return client.post(f'{API}/sales', json=_sale_payload(items, branch_id=branch_id))


def _mark_behind(hours):
    """Verbatim copy of retail_oversell_relaxation_test.py's own helper:
    persists a REAL old timestamp through `record_sync_freshness` -- the
    same function production's `_record_sync_success` calls."""
    ts = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    conn = sch.get_retail_conn()
    try:
        sch.record_sync_freshness(conn, 'push', ts)
        sch.record_sync_freshness(conn, 'pull', ts)
        conn.commit()
    finally:
        conn.close()


def _real_sync_service():
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


def _default_branch_id(cid):
    conn = sch.get_retail_conn()
    try:
        bid = retail_api._default_branch(conn, cid)
        conn.commit()
        return bid
    finally:
        conn.close()


def _create_branch(client, name):
    r = client.post(f'{API}/branches', json={'name': name, 'address': '', 'phone': ''})
    assert r.status_code == 200, r.get_json()
    return r.get_json()['data']['id']


def _adjust_stock(client, pid, qty, branch_id, reason='Multiline stock test setup'):
    r = client.post(f'{API}/products/{pid}/stock-adjust',
                     json={'quantity': qty, 'reason': reason, 'branch_id': branch_id})
    assert r.status_code == 200, r.get_json()


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


# ═════════════════════════════════════════════════════════════════════════
# 1. THE BUG -- two lines for the same product cannot jointly exceed stock
# ═════════════════════════════════════════════════════════════════════════

def test_two_lines_for_the_same_product_cannot_jointly_exceed_stock():
    """3 + 3 against 5 on hand, on a device that is NOT behind on sync (the
    hard-refusal path). Each line is individually within stock (3 <= 5);
    only their SUM (6) exceeds it -- exactly the shape a per-line check can
    never catch. Asserts the sale wrote NOTHING: a bare 400 would also pass
    while the damage was already done if the refusal fired too late."""
    admin, cid = _new_shop()
    pid = _create_product(admin, initial_stock=5)
    before_stock = _balance(cid, pid)
    before_sales = _sales_count(cid)

    r = _sell_multi(admin, [
        {'product_id': pid, 'quantity': 3},
        {'product_id': pid, 'quantity': 3},
    ])
    assert r.status_code == 400, r.get_json()
    assert 'Insufficient stock' in r.get_json()['message'], r.get_json()

    assert _balance(cid, pid) == before_stock, (
        'a refused multi-line oversell moved stock anyway -- the two lines were '
        'each checked in isolation and both writes landed before the refusal')
    assert _sales_count(cid) == before_sales, 'a refused multi-line oversell wrote a sales row anyway'
    assert _exceptions_for(cid, product_id=pid) == []


# ═════════════════════════════════════════════════════════════════════════
# 2. THE ALLOW HALF -- a fix that refuses any repeat must not ship
# ═════════════════════════════════════════════════════════════════════════

def test_two_lines_for_the_same_product_within_stock_still_sell():
    """2 + 2 against 5 on hand -- both lines individually AND jointly within
    stock. Must succeed and decrement by exactly 4. Without this test, a fix
    that refuses any sale carrying a repeated product would pass section 1
    while breaking every completely legitimate multi-line sale."""
    admin, cid = _new_shop()
    pid = _create_product(admin, initial_stock=5)

    r = _sell_multi(admin, [
        {'product_id': pid, 'quantity': 2},
        {'product_id': pid, 'quantity': 2},
    ])
    assert r.status_code == 200, r.get_json()
    assert r.get_json()['data']['oversold_past_recorded_stock'] is False

    assert _balance(cid, pid) == 1, 'expected 5 - 2 - 2 = 1 on hand after the sale'


# ═════════════════════════════════════════════════════════════════════════
# 3. Different products must not be conflated
# ═════════════════════════════════════════════════════════════════════════

def test_two_lines_for_different_products_are_not_conflated():
    """3 of product A and 3 of product B, each with 5 on hand. Must succeed
    -- a naive accumulator keyed only on quantity (summing ALL lines
    regardless of product) would incorrectly see a combined demand of 6 and
    refuse this, even though neither product is actually oversold."""
    admin, cid = _new_shop()
    pid_a = _create_product(admin, initial_stock=5)
    pid_b = _create_product(admin, initial_stock=5)

    r = _sell_multi(admin, [
        {'product_id': pid_a, 'quantity': 3},
        {'product_id': pid_b, 'quantity': 3},
    ])
    assert r.status_code == 200, r.get_json()

    assert _balance(cid, pid_a) == 2
    assert _balance(cid, pid_b) == 2


# ═════════════════════════════════════════════════════════════════════════
# 4. This route cannot carry lines for more than one branch -- pinned
# ═════════════════════════════════════════════════════════════════════════

def test_the_same_product_at_two_branches_is_not_conflated():
    """PINNING TEST, not a positive multi-branch capability test: `create_sale`
    resolves exactly ONE `bid` for the WHOLE sale, once, before the
    validation loop (`bid = int(data.get('branch_id') or _default_branch(...))`)
    -- the per-item loop never reads `item.get('branch_id')` at all, only
    `product_id`/`quantity`/`discount_pct`. So this route CANNOT carry lines
    for more than one branch in a single sale; there is no way to construct
    the "two lines, two branches" scenario the accumulator's key is
    theoretically guarding against.

    What this test proves instead: a per-ITEM `branch_id` is silently
    ignored, and every line in the sale debits the SALE-level branch_id
    regardless of what an item claims. Setup: product has 5 on hand at
    BOTH branch 1 (the default, created by _create_product) and branch 2
    (topped up via adjust_stock). The sale's own (top-level) branch_id is
    branch 1; both items carry a (meaningless) item-level branch_id of
    branch 2 and ask for 3 units each. If item-level branch_id had any
    effect, this would be indistinguishable from
    test_two_lines_for_different_products_are_not_conflated's shape (two
    independent 5-unit pools) and would succeed. Because it has none, both
    lines are checked and written against branch 1's SHARED 5-unit balance,
    so the combined demand (6) correctly refuses -- and branch 2's balance,
    which nothing should ever have touched, is asserted unchanged."""
    admin, cid = _new_shop()
    pid = _create_product(admin, initial_stock=5)  # lands on the default branch
    branch1 = _default_branch_id(cid)
    branch2 = _create_branch(admin, 'Multiline Stock Second Branch')
    _adjust_stock(admin, pid, 5, branch_id=branch2)
    assert _balance(cid, pid, branch1) == 5
    assert _balance(cid, pid, branch2) == 5

    r = _sell_multi(admin, [
        {'product_id': pid, 'quantity': 3, 'branch_id': branch2},
        {'product_id': pid, 'quantity': 3, 'branch_id': branch2},
    ], branch_id=branch1)
    assert r.status_code == 400, r.get_json()
    assert 'Insufficient stock' in r.get_json()['message'], (
        'an item-level branch_id changed which balance a line was checked against -- '
        'this route is supposed to have no per-line branch support at all')

    assert _balance(cid, pid, branch1) == 5, 'the refused sale moved branch 1 stock anyway'
    assert _balance(cid, pid, branch2) == 5, (
        'branch 2 stock moved even though every line in the sale debits the '
        'SALE-level branch_id, never an item-level one')


# ═════════════════════════════════════════════════════════════════════════
# 5. The ordinary, single-line path is unaffected
# ═════════════════════════════════════════════════════════════════════════

def test_a_single_line_sale_is_unaffected():
    admin, cid = _new_shop()
    pid = _create_product(admin, initial_stock=5)

    r = _sell_multi(admin, [{'product_id': pid, 'quantity': 3}])
    assert r.status_code == 200, r.get_json()
    assert r.get_json()['data']['oversold_past_recorded_stock'] is False
    assert _balance(cid, pid) == 2

    # And a single-line refusal still refuses exactly as before.
    r2 = _sell_multi(admin, [{'product_id': pid, 'quantity': 100}])
    assert r2.status_code == 400, r2.get_json()
    assert 'Insufficient stock' in r2.get_json()['message'], r2.get_json()
    assert _balance(cid, pid) == 2, 'a refused single-line sale moved stock anyway'


# ═════════════════════════════════════════════════════════════════════════
# 6. Composition with stage 7d-iii: a behind device can still oversell
#    across two lines, and it is recorded
# ═════════════════════════════════════════════════════════════════════════

def test_a_behind_device_can_still_oversell_across_two_lines_and_it_is_recorded():
    """The 7d-iii composition proof. Same 3 + 3 against 5 shape as section
    1, but this device IS behind on sync -- the relaxation must still fire
    (the accumulator changes WHAT is compared, never WHEN the refusal
    relaxes), the sale must succeed, the response must say so, and --
    because the resulting balance lands negative (5 - 3 - 3 = -1) -- a
    `stock_exceptions` row must be written for the ACCUMULATED shortfall,
    not silently skipped because neither line's own quantity (3) exceeded
    `on_hand` (5) in isolation."""
    admin, cid = _new_shop()
    pid = _create_product(admin, initial_stock=5)
    bid = _default_branch_id(cid)
    _mark_behind(2)
    register_active_service(_real_sync_service())

    before_sales = _sales_count(cid)
    r = _sell_multi(admin, [
        {'product_id': pid, 'quantity': 3},
        {'product_id': pid, 'quantity': 3},
    ])
    assert r.status_code == 200, r.get_json()
    assert r.get_json()['data']['oversold_past_recorded_stock'] is True

    assert _sales_count(cid) == before_sales + 1, 'the relaxed multi-line sale did not write a sales row'
    assert _balance(cid, pid, bid) == -1, 'expected 5 - 3 - 3 = -1 on hand after the relaxed sale'

    rows = _exceptions_for(cid, product_id=pid)
    assert len(rows) == 1, (
        f'a behind device oversold across two lines and landed the balance negative, but no '
        f'stock_exceptions row was recorded for it: {rows}')
    row = rows[0]
    assert row['company_id'] == cid
    assert row['product_id'] == pid
    assert row['branch_id'] == bid
    assert row['observed_quantity_on_hand'] == -1
    assert row['resolved_at_utc'] is None
