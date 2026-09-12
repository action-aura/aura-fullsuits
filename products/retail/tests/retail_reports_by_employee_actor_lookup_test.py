"""
Aura Retail -- a failed actor lookup writes NULL, and must never do it in
silence.

(Filename prefix is this wave's file-ownership convention, not the subject.)

THE POLICY IS RIGHT AND IS NOT CHANGING. `_actor_user_uid()` returns None
rather than falling back to `_uid()` when it cannot resolve the signed-in
user's wire identity, and its docstring explains why at length: `_uid()` is
the LOCAL `users.id`, both values are uuid4 strings, and a local id parked in
a wire column passes every check here and resolves to nobody on the next
device. A NULL is honest and repairable; a plausible wrong value is neither.

WHAT WAS WRONG WAS THE SILENCE. Both copies of that function -- retail_api.py
and its deliberate twin in import_api.py -- wrapped the registry read in a
bare `except Exception: return None`. No log line, no counter, nothing. So a
transient `database is locked` on registry.db stamps permanently NULL
`actor_user_uid` onto real financial rows -- sales, returns, cash movements,
stock adjustments -- and afterwards nobody can tell that shop's unattributed
bucket (which legitimately holds all its pre-v13 history) apart from a
five-minute outage last Tuesday. Silent, deferred and irreversible.

import_api's copy is the worse of the two, and this file pins that
specifically: its `_stamp()` resolves ONCE PER RUN, correctly and on purpose,
so ONE swallowed exception does not unattribute one row -- it unattributes an
entire upload.

Run:
    pytest products/retail/tests/retail_reports_by_employee_actor_lookup_test.py -v
"""
import io
import logging
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

DATA = Path(tempfile.mkdtemp(prefix="aura_retail_actorlookup_"))
(DATA / "database" / "subsystems").mkdir(parents=True, exist_ok=True)
os.environ.update(AURA_STANDALONE="1", AURA_BUNDLE_DIR=str(BACKEND_DIR), AURA_APP_DATA=str(DATA))
os.environ.pop("AURA_DEV", None)

from commercial_runtime.licensing_contracts.test_support import seed_active_license  # noqa: E402
seed_active_license(str(DATA), product_code="AURA_RETAIL", platform="WINDOWS")
os.environ.pop("AURA_RETAIL_DEMO_MODE", None)

import app as _app_module  # noqa: E402

app = _app_module.init_app()
app.config["TESTING"] = True

import api.import_api as import_api_module  # noqa: E402
import api.retail_api as retail_api_module  # noqa: E402
from commercial_runtime.identity import user_accounts  # noqa: E402
from commercial_runtime.identity.registry_db import get_conn as registry_conn  # noqa: E402
from commercial_runtime.security.passwords import hash_password  # noqa: E402
from database.schema import get_retail_conn  # noqa: E402


def teardown_module(module):
    shutil.rmtree(DATA, ignore_errors=True)


# ── fixture ──────────────────────────────────────────────────────────────────

def _make_shop(with_uid=True, stock=500):
    email = f"actor-{uuid.uuid4().hex[:10]}@test.local"
    password = "ActorLookupPW1"
    company_id = str(uuid.uuid4())
    user_id = str(uuid.uuid4())
    user_uid = str(uuid.uuid4()) if with_uid else None

    conn = registry_conn()
    conn.execute(
        "INSERT INTO users (id, uid, company_id, employee_id, email, password_hash, role, status, "
        "require_password_change) VALUES (?,?,?,?,?,?,?,?,0)",
        (user_id, user_uid, company_id, f"EMP-{uuid.uuid4().hex[:6].upper()}", email,
         hash_password(password), "admin", "active"))
    conn.commit()
    conn.close()

    rconn = get_retail_conn()
    rconn.execute("INSERT INTO branches (company_id,name) VALUES (?, 'Main')", (company_id,))
    branch_id = rconn.execute("SELECT id FROM branches WHERE company_id=? ORDER BY id LIMIT 1",
                              (company_id,)).fetchone()[0]
    product_id = str(uuid.uuid4())
    rconn.execute(
        "INSERT INTO products (id,company_id,sku,name,cost_price,sell_price,tax_rate) "
        "VALUES (?,?,?,'Actor Item',5,100,0)", (product_id, company_id, f'ACT-{uuid.uuid4().hex[:8]}'))
    rconn.execute(
        "INSERT INTO inventory_balances (company_id,product_id,branch_id,quantity_on_hand) "
        "VALUES (?,?,?,?)", (company_id, product_id, branch_id, stock))
    rconn.commit()
    rconn.close()

    client = app.test_client()
    r = client.post("/api/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.get_data(as_text=True)
    client.get('/api/sub/retail/settings/tax')

    import random as _random
    dconn = get_retail_conn()
    for doc_type in ('sale', 'return', 'po', 'receipt', 'supplier_payment'):
        dconn.execute("INSERT OR REPLACE INTO doc_sequences (company_id,doc_type,last_no) VALUES (?,?,?)",
                      (company_id, doc_type, _random.randint(1, 5_000_000)))
    dconn.commit()
    dconn.close()

    return {'client': client, 'company_id': company_id, 'branch_id': branch_id,
            'product_id': product_id, 'user_id': user_id, 'user_uid': user_uid}


def _sell(shop):
    r = shop['client'].post('/api/sub/retail/sales', json={
        'items': [{'product_id': shop['product_id'], 'quantity': 1}],
        'payment_method': 'cash', 'amount_paid': 100000,
        'idempotency_key': str(uuid.uuid4())})
    assert r.status_code == 200, r.get_data(as_text=True)
    return r.get_json()['data']


class _BrokenRegistry:
    """Stands in for registry.db being briefly unavailable, and counts its own
    invocations -- a test that asserted only "the uid is NULL" would pass
    identically if the lookup were never attempted at all."""

    def __init__(self, exc=None):
        self.calls = 0
        self._exc = exc or sqlite3.OperationalError('database is locked')

    def __call__(self, *a, **kw):
        self.calls += 1
        raise self._exc


def _reset(counter):
    for key in counter:
        counter[key] = 0


# ── 1. the write side, retail_api ────────────────────────────────────────────

def test_a_registry_outage_at_write_time_is_counted_and_logged(caplog):
    """The headline. The sale still completes -- attribution is bookkeeping
    and a sale is the business -- and the row is still honestly NULL. What
    must ALSO happen is that somebody can find out it did."""
    shop = _make_shop()
    _reset(retail_api_module.ACTOR_LOOKUP_FAILURES)
    broken = _BrokenRegistry()
    real = retail_api_module._registry_conn
    retail_api_module._registry_conn = broken
    try:
        with caplog.at_level(logging.ERROR, logger='api.retail_api'):
            sale = _sell(shop)
    finally:
        retail_api_module._registry_conn = real

    assert broken.calls >= 1, 'the lookup was never attempted -- this proves nothing'

    conn = get_retail_conn()
    row = conn.execute("SELECT actor_user_uid FROM sales WHERE id=?", (sale['id'],)).fetchone()
    conn.close()
    assert row['actor_user_uid'] is None, 'the NULL-over-fallback policy changed'

    assert retail_api_module.ACTOR_LOOKUP_FAILURES['write_lookup_error'] >= 1, (
        "a financial row was written with no actor and the failure was not counted")
    assert any(r.levelno >= logging.ERROR for r in caplog.records), (
        "nothing was logged at ERROR -- the outage is still silent")
    assert any('write_lookup_error' in r.getMessage() for r in caplog.records), caplog.text


def test_the_null_is_still_a_null_and_never_the_local_user_id():
    """The policy this fix must NOT quietly relax while making it noisy.
    `session['mt_user_id']` is a uuid4 string too, so a fallback to it would
    satisfy every not-null check in the suite and be wrong forever."""
    shop = _make_shop()
    broken = _BrokenRegistry()
    real = retail_api_module._registry_conn
    retail_api_module._registry_conn = broken
    try:
        sale = _sell(shop)
    finally:
        retail_api_module._registry_conn = real

    conn = get_retail_conn()
    row = conn.execute("SELECT actor_user_uid, cashier FROM sales WHERE id=?",
                       (sale['id'],)).fetchone()
    conn.close()
    assert row['actor_user_uid'] is None
    assert row['actor_user_uid'] != shop['user_id']
    # ...and the free-text evidence trail v13 was careful not to disturb is
    # still there, carrying the LOCAL id exactly as it always has.
    assert row['cashier'] == shop['user_id'], row['cashier']


def test_a_missing_users_row_is_counted_apart_from_a_failed_read():
    """Two different problems that both end in a NULL. One is an outage; the
    other is a session naming an account that is not there. Counting them
    together would make each one unreadable.

    Driven at the function rather than through a route: deleting the account
    mid-session makes `mt_login_required` refuse the request outright (401),
    so no write path can be made to reach the lookup in that state. The
    session shape is the real one `mt_auth.create_session` writes."""
    _reset(retail_api_module.ACTOR_LOOKUP_FAILURES)
    absent = str(uuid.uuid4())
    with app.test_request_context('/'):
        from flask import session as flask_session
        flask_session['mt_user_id'] = absent
        assert retail_api_module._actor_user_uid() is None
    assert retail_api_module.ACTOR_LOOKUP_FAILURES['write_no_user_row'] >= 1
    assert retail_api_module.ACTOR_LOOKUP_FAILURES['write_lookup_error'] == 0, (
        'an absent account was reported as a database failure')


def test_an_account_with_no_uid_yet_is_counted_as_its_own_reason():
    """Transient by design -- account_schema's `_backfill_uids` stamps these
    on the next launch and both account-creation routes write one inline --
    but a row written during that window is still unattributed forever, and
    it is a third distinct reason."""
    shop = _make_shop(with_uid=False)
    _reset(retail_api_module.ACTOR_LOOKUP_FAILURES)
    sale = _sell(shop)
    conn = get_retail_conn()
    row = conn.execute("SELECT actor_user_uid FROM sales WHERE id=?", (sale['id'],)).fetchone()
    conn.close()
    assert row['actor_user_uid'] is None
    assert retail_api_module.ACTOR_LOOKUP_FAILURES['write_blank_uid'] >= 1


def test_the_healthy_path_counts_nothing():
    """A counter that ticks on success tells you nothing on failure."""
    shop = _make_shop()
    _reset(retail_api_module.ACTOR_LOOKUP_FAILURES)
    sale = _sell(shop)
    conn = get_retail_conn()
    row = conn.execute("SELECT actor_user_uid FROM sales WHERE id=?", (sale['id'],)).fetchone()
    conn.close()
    assert row['actor_user_uid'] == shop['user_uid']
    assert sum(retail_api_module.ACTOR_LOOKUP_FAILURES.values()) == 0, (
        retail_api_module.ACTOR_LOOKUP_FAILURES)


def test_the_counter_names_every_way_the_lookup_can_decline():
    """A counter with a reason missing is a silence with extra steps."""
    assert set(retail_api_module.ACTOR_LOOKUP_FAILURES) >= {
        'write_lookup_error', 'write_no_user_row', 'write_blank_uid', 'read_lookup_error'}


def test_a_report_side_lookup_failure_is_counted_too():
    """The read side resolves uids in bulk for a whole report. Its failure is
    less damaging -- the figures still render, just nameless -- but a page of
    raw uids with no trace of WHY is exactly how a permanent registry problem
    gets mistaken for old unattributed data."""
    shop = _make_shop()
    _sell(shop)
    _reset(retail_api_module.ACTOR_LOOKUP_FAILURES)
    broken = _BrokenRegistry()
    real = retail_api_module._registry_conn
    retail_api_module._registry_conn = broken
    try:
        r = shop['client'].get('/api/sub/retail/reports/by-employee?days=30')
    finally:
        retail_api_module._registry_conn = real

    assert r.status_code == 200, r.get_data(as_text=True)
    assert broken.calls >= 1
    assert retail_api_module.ACTOR_LOOKUP_FAILURES['read_lookup_error'] >= 1
    assert all(row['employee_name'] is None for row in r.get_json()['data'])


# ── 2. the write side, import_api -- one failure, a whole upload ─────────────

# Opening stock is what makes an import land ATTRIBUTED rows: `products` is a
# row-version table, not an actor table, so the v13 stamp this file is about
# reaches `inventory_movements` -- the ledger the upload writes when it
# declares a product's starting quantity. That is also the highest-leverage
# write in the product (see import_api's own CAP_STOCK_ADJUST comment), which
# is precisely why its attribution matters.
_CSV = (b"name,sku,sell_price,initial_stock\n"
        b"Imported Alpha,IMP-ALPHA-%s,10,7\n"
        b"Imported Beta,IMP-BETA-%s,20,8\n"
        b"Imported Gamma,IMP-GAMMA-%s,30,9\n")

_MAPPING = ('{"name": "name", "sku": "sku", "sell_price": "sell_price", '
            '"initial_stock": "initial_stock"}')


def _import_products(shop):
    tag = uuid.uuid4().hex[:8].encode()
    payload = _CSV % (tag, tag, tag)
    return shop['client'].post('/api/import/execute', data={
        'file': (io.BytesIO(payload), 'products.csv'),
        'system': 'retail', 'entity': 'products', 'mapping': _MAPPING,
    }, content_type='multipart/form-data')


def _imported_movements(shop):
    conn = get_retail_conn()
    rows = conn.execute(
        "SELECT m.actor_user_uid FROM inventory_movements m JOIN products p ON m.product_id=p.id "
        "WHERE m.company_id=? AND p.sku LIKE 'IMP-%'", (shop['company_id'],)).fetchall()
    conn.close()
    return rows


def test_the_import_fixture_really_imports_something():
    """Everything below asserts on rows an import wrote. A mapping that
    quietly matched nothing, or a file that declared no stock, would make all
    of it vacuous."""
    shop = _make_shop()
    r = _import_products(shop)
    assert r.status_code == 200, r.get_data(as_text=True)
    body = r.get_json()
    assert body['success'] is True, body
    assert body.get('imported', 0) >= 3, body

    rows = _imported_movements(shop)
    assert len(rows) >= 3, (rows, body)
    assert all(row['actor_user_uid'] == shop['user_uid'] for row in rows), (
        'the healthy import did not stamp the actor at all')


def test_one_swallowed_lookup_unattributes_the_whole_upload_and_says_so(caplog):
    """The reason this copy matters more than retail_api's. `_stamp()`
    resolves ONCE per run -- right, and deliberate -- so a single failed
    lookup is not one unattributed row, it is every row in the file."""
    shop = _make_shop()
    _reset(import_api_module.ACTOR_LOOKUP_FAILURES)
    broken = _BrokenRegistry()
    real = import_api_module._registry_conn
    import_api_module._registry_conn = broken
    try:
        with caplog.at_level(logging.ERROR, logger='api.import_api'):
            r = _import_products(shop)
    finally:
        import_api_module._registry_conn = real

    assert r.status_code == 200, r.get_data(as_text=True)
    assert broken.calls >= 1, 'the lookup was never attempted -- this proves nothing'

    rows = _imported_movements(shop)
    assert len(rows) >= 3, 'the import wrote nothing, so this proves nothing about attribution'
    assert all(row['actor_user_uid'] is None for row in rows), (
        'the NULL-over-fallback policy changed on the import path')

    assert import_api_module.ACTOR_LOOKUP_FAILURES['import_lookup_error'] >= 1, (
        'an entire upload landed unattributed and nothing counted it')
    assert any('UPLOAD' in r.getMessage().upper() for r in caplog.records), (
        f"the log line does not say the whole run is affected: {caplog.text}")


def test_the_import_counter_is_its_own_and_does_not_collide_with_the_route_copy():
    """The two `_actor_user_uid` copies are deliberately duplicated rather
    than imported (retail_api.py is a peer route module). Their counters are
    duplicated the same way -- and their reason keys are disjoint, so summing
    the two mappings for a support bundle cannot silently merge a route
    failure with an import failure."""
    assert set(import_api_module.ACTOR_LOOKUP_FAILURES) == {
        'import_lookup_error', 'import_no_user_row', 'import_blank_uid'}
    assert not (set(import_api_module.ACTOR_LOOKUP_FAILURES)
                & set(retail_api_module.ACTOR_LOOKUP_FAILURES))
    assert import_api_module.ACTOR_LOOKUP_FAILURES \
        is not retail_api_module.ACTOR_LOOKUP_FAILURES
