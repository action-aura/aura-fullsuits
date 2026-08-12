"""
Aura Retail -- feat/audit-log-viewer: real end-to-end coverage for
GET /api/sub/retail/audit-log (products/retail/backend/api/retail_api.py).

_audit() has written to `audit_log` from ~26 real call sites (product CRUD,
stock adjustments, customer/supplier CRUD, PO lifecycle, reorder decisions,
returns/refunds, payments, payment voids) since the very first version of
this file, but until this route nothing ever read any of it back. This
suite drives real refund/void/product-change actions through the actual
HTTP routes (never a raw INSERT into audit_log) and confirms:

  - The viewer route itself: pagination, date/action/entity filters, and
    that every returned row carries the SAME user_id _audit() actually
    wrote for that request.
  - The admin-device gate: fails closed (403) by default, opens once this
    device is promoted to admin for the company, via the SAME real
    device_registry/device_context path
    commercial_runtime/identity/tests/test_device_routes.py already uses
    (see _promote_to_admin below) -- this route does not invent its own
    admin-device mechanism.
  - Company scoping: a second company's audit_log rows never appear in the
    first company's response. The admin-device gate itself is
    single-company by design (device_context.resolve_local_device raises
    DeviceCompanyMismatchError if the SAME local device resolves against a
    SECOND company_id -- Phase 0 decision 0-a, see device_context.py's
    module docstring), so the cross-company test below runs with
    _is_admin_device monkeypatched True for both companies -- that gate is
    real, already-tested per-install infrastructure this feature didn't
    invent; that section is only about the SQL `WHERE company_id=?`
    scoping this route itself is responsible for.

Run:
    pytest products/retail/tests/retail_audit_log_test.py -v
"""
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

DATA = Path(tempfile.mkdtemp(prefix="aura_retail_auditlog_"))
(DATA / "database" / "subsystems").mkdir(parents=True, exist_ok=True)
os.environ.update(AURA_STANDALONE="1", AURA_BUNDLE_DIR=str(BACKEND_DIR), AURA_APP_DATA=str(DATA))
os.environ.pop("AURA_DEV", None)

from commercial_runtime.licensing_contracts.test_support import seed_active_license  # noqa: E402

seed_active_license(str(DATA), product_code="AURA_RETAIL", platform="WINDOWS")
os.environ.pop("AURA_RETAIL_DEMO_MODE", None)

import app as _app_module  # noqa: E402

app = _app_module.init_app()
app.config["TESTING"] = True
app.config["PROPAGATE_EXCEPTIONS"] = False

from commercial_runtime.identity.registry_db import get_conn as registry_conn  # noqa: E402
from commercial_runtime.identity import device_context, device_registry  # noqa: E402
from commercial_runtime.security.passwords import hash_password  # noqa: E402
from database.schema import get_retail_conn  # noqa: E402
from api import retail_api  # noqa: E402


def teardown_module(module):
    shutil.rmtree(DATA, ignore_errors=True)


@pytest.fixture(autouse=True)
def _reset_device_cache():
    # Same reset commercial_runtime/identity/tests/test_device_routes.py
    # uses -- resolve_local_device() caches its result in-process, so any
    # test that flips the DB's is_admin_device flag directly needs a clean
    # slate to actually observe it on the next call.
    device_context._cached_device = None
    yield
    device_context._cached_device = None


def _make_admin(email_prefix='aud'):
    """Real company + real admin user + real login, matching the harness
    every other retail_*_test.py file in this directory already uses."""
    email = f"{email_prefix}-{uuid.uuid4().hex[:10]}@test.local"
    password = "AuditLogPW1"
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
    try:
        rconn.execute("INSERT INTO branches (company_id,name) VALUES (?, 'Main')", (company_id,))
        rconn.commit()
    finally:
        rconn.close()

    client = app.test_client()
    r = client.post("/api/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.get_json()

    # retail_settings/doc_sequences are created LAZILY by _ensure_credit_schema
    # (see database/schema.py's module docstring) -- hitting any route that
    # calls it (settings/tax does) materializes those tables before this
    # helper touches doc_sequences directly below. Matches
    # retail_returns_wave0_test.py's identical ordering.
    client.get('/api/sub/retail/settings/tax')

    # sales.sale_number carries a bare (not company-scoped) UNIQUE constraint
    # (see retail_returns_wave0_test.py's identical comment) -- every fresh
    # company in this shared temp DB would otherwise generate "SALE-000001"
    # as its first sale and collide with every other test's first sale.
    import random as _random
    rconn2 = get_retail_conn()
    try:
        rconn2.execute(
            "INSERT OR REPLACE INTO doc_sequences (company_id,doc_type,last_no) VALUES (?,'sale',?)",
            (company_id, _random.randint(1, 5_000_000)),
        )
        rconn2.commit()
    finally:
        rconn2.close()

    return client, company_id, user_id


def _promote_to_admin(client, company_id):
    """Flips THIS test process's local device to admin for `company_id`,
    the real way (device_registry.upsert_local_device + set_admin_device),
    mirroring test_device_routes.py's
    test_my_device_reflects_admin_flag_as_bool_true. Must run before any
    earlier call in this process bound the local device to a DIFFERENT
    company_id -- resolve_local_device() raises DeviceCompanyMismatchError
    on a mismatch (Phase 0 decision 0-a), which is why the cross-company
    test below takes a different approach entirely."""
    conn = registry_conn()
    device_id = device_context.local_device_uuid()
    device_registry.upsert_local_device(conn, device_id, company_id)
    device_registry.set_admin_device(conn, company_id, device_id)
    conn.close()
    device_context._cached_device = None
    # Materialize the resolution once through the real HTTP surface too --
    # exactly what app-shell.js's own GET /api/devices/me call does on
    # init() before it decides whether to show the Audit Log nav entry.
    r = client.get('/api/devices/me')
    assert r.status_code == 200, r.get_json()
    assert r.get_json()['device']['is_admin_device'] is True


def _create_product(client, name='Widget', price=20.0, sku=None):
    r = client.post('/api/sub/retail/products', json={
        'name': name, 'sku': sku or f"SKU-{uuid.uuid4().hex[:8]}",
        'sell_price': price, 'cost_price': price / 2, 'initial_stock': 10,
    })
    assert r.status_code == 200, r.get_json()
    return r.get_json()['data']['id']


def _sell(client, pid, quantity=1):
    r = client.post('/api/sub/retail/sales', json={
        'items': [{'product_id': pid, 'quantity': quantity}],
        'amount_paid': 999999, 'payment_method': 'cash', 'idempotency_key': str(uuid.uuid4()),
    })
    assert r.status_code == 200, r.get_json()
    return r.get_json()['data']


def _seed_real_actions(client, company_id):
    """Drives one of each action family the task calls out (refund, void,
    product edit) through the REAL API -- never a raw INSERT into
    audit_log -- so every resulting row carries genuine, _audit()-written
    user_id attribution. Returns the ids created, for assertions."""
    pid = _create_product(client, name='Espresso Machine', price=150.0)  # PRODUCT_CREATED
    upd = client.patch(f'/api/sub/retail/products/{pid}', json={'name': 'Espresso Machine Pro'})  # PRODUCT_UPDATED
    assert upd.status_code == 200, upd.get_json()

    sale = _sell(client, pid, quantity=1)
    ret = client.post('/api/sub/retail/returns', json={  # RETURN_PROCESSED (refund)
        'sale_id': sale['id'], 'items': [{'product_id': pid, 'quantity': 1}],
        'reason': 'defective', 'idempotency_key': str(uuid.uuid4()),
    })
    assert ret.status_code == 200, ret.get_json()

    cust = client.post('/api/sub/retail/customers', json={'name': 'Audit Test Customer'})  # CUSTOMER_CREATED
    assert cust.status_code == 200, cust.get_json()
    cust_id = cust.get_json()['data']['id']
    pay = client.post(f'/api/sub/retail/customers/{cust_id}/payments',  # CUSTOMER_PAYMENT
                       json={'amount': 25.0, 'method': 'cash'})
    assert pay.status_code == 200, pay.get_json()

    rconn = get_retail_conn()
    try:
        payment_row = rconn.execute(
            "SELECT id FROM payments WHERE company_id=? AND party_type='customer' AND party_id=? ORDER BY id DESC LIMIT 1",
            (company_id, cust_id)
        ).fetchone()
    finally:
        rconn.close()
    payment_id = payment_row['id']
    void = client.post(f'/api/sub/retail/payments/{payment_id}/void',  # PAYMENT_VOIDED (void)
                        json={'reason': 'test void'})
    assert void.status_code == 200, void.get_json()

    return {'product_id': pid, 'sale_id': sale['id'], 'customer_id': cust_id, 'payment_id': payment_id}


# ── Admin-device gate ─────────────────────────────────────────────────────

def test_audit_log_403s_for_a_non_admin_device():
    client, cid, uid = _make_admin()
    r = client.get('/api/sub/retail/audit-log')
    assert r.status_code == 403, r.get_json()
    assert r.get_json()['status'] == 'error'


def test_audit_log_opens_once_this_device_is_promoted_to_admin():
    client, cid, uid = _make_admin()
    _promote_to_admin(client, cid)
    r = client.get('/api/sub/retail/audit-log')
    assert r.status_code == 200, r.get_json()
    assert r.get_json()['status'] == 'success'


# ── Real data, correct attribution ────────────────────────────────────────

def test_admin_device_sees_seeded_actions_with_correct_user_attribution():
    client, cid, uid = _make_admin()
    _promote_to_admin(client, cid)
    ids = _seed_real_actions(client, cid)

    r = client.get('/api/sub/retail/audit-log?limit=100')
    assert r.status_code == 200, r.get_json()
    body = r.get_json()
    assert body['status'] == 'success'

    actions = {row['action'] for row in body['data']}
    assert {'PRODUCT_CREATED', 'PRODUCT_UPDATED', 'RETURN_PROCESSED',
            'CUSTOMER_PAYMENT', 'PAYMENT_VOIDED'} <= actions

    # Every row this request seeded is attributed to the real logged-in
    # user -- never the 'system' fallback _uid() only uses when no session
    # user_id is present at all.
    assert body['data'], "expected at least the rows _seed_real_actions wrote"
    assert all(row['user_id'] == uid for row in body['data'])

    product_rows = [row for row in body['data'] if row['entity'] == 'product']
    assert any(str(row['entity_id']) == str(ids['product_id']) for row in product_rows)

    payment_void_rows = [row for row in body['data'] if row['action'] == 'PAYMENT_VOIDED']
    assert len(payment_void_rows) == 1
    assert str(payment_void_rows[0]['entity_id']) == str(ids['payment_id'])
    assert 'test void' in payment_void_rows[0]['details']


# ── Filters ────────────────────────────────────────────────────────────────

def test_action_filter_returns_only_that_action():
    client, cid, uid = _make_admin()
    _promote_to_admin(client, cid)
    _seed_real_actions(client, cid)

    r = client.get('/api/sub/retail/audit-log?action=PAYMENT_VOIDED')
    body = r.get_json()
    assert body['status'] == 'success'
    assert len(body['data']) == 1
    assert body['data'][0]['action'] == 'PAYMENT_VOIDED'


def test_entity_filter_returns_only_that_entity():
    client, cid, uid = _make_admin()
    _promote_to_admin(client, cid)
    _seed_real_actions(client, cid)

    r = client.get('/api/sub/retail/audit-log?entity=return')
    body = r.get_json()
    assert body['status'] == 'success'
    assert body['data'], "expected the RETURN_PROCESSED row"
    assert all(row['entity'] == 'return' for row in body['data'])


def test_date_range_filter_excludes_out_of_range_rows():
    client, cid, uid = _make_admin()
    _promote_to_admin(client, cid)
    _seed_real_actions(client, cid)

    r = client.get('/api/sub/retail/audit-log?date_from=2099-01-01')
    body = r.get_json()
    assert body['status'] == 'success'
    assert body['data'] == []
    assert body['meta']['total'] == 0

    r2 = client.get('/api/sub/retail/audit-log?date_to=2099-01-01')
    body2 = r2.get_json()
    assert body2['meta']['total'] >= 5


# ── Pagination ─────────────────────────────────────────────────────────────

def test_pagination_limits_page_size_and_pages_dont_overlap():
    client, cid, uid = _make_admin()
    _promote_to_admin(client, cid)
    _seed_real_actions(client, cid)  # writes at least 6 audit rows

    r1 = client.get('/api/sub/retail/audit-log?limit=2&page=1')
    body1 = r1.get_json()
    assert len(body1['data']) == 2
    assert body1['meta']['total'] >= 6
    assert body1['meta']['page'] == 1
    assert body1['meta']['limit'] == 2

    r2 = client.get('/api/sub/retail/audit-log?limit=2&page=2')
    body2 = r2.get_json()
    assert len(body2['data']) == 2

    ids1 = {row['id'] for row in body1['data']}
    ids2 = {row['id'] for row in body2['data']}
    assert ids1.isdisjoint(ids2), "consecutive pages must never return the same row"


def test_limit_is_capped_and_page_below_one_is_clamped():
    client, cid, uid = _make_admin()
    _promote_to_admin(client, cid)
    _seed_real_actions(client, cid)

    r = client.get('/api/sub/retail/audit-log?limit=99999&page=0')
    body = r.get_json()
    assert body['status'] == 'success'
    assert body['meta']['limit'] <= 200
    assert body['meta']['page'] == 1


# ── Filter metadata for the frontend's dropdowns ────────────────────────────

def test_meta_reports_distinct_actions_and_entities_actually_on_record():
    client, cid, uid = _make_admin()
    _promote_to_admin(client, cid)
    _seed_real_actions(client, cid)

    r = client.get('/api/sub/retail/audit-log')
    meta = r.get_json()['meta']
    assert 'PAYMENT_VOIDED' in meta['actions']
    assert 'PRODUCT_CREATED' in meta['actions']
    assert 'product' in meta['entities']
    assert 'return' in meta['entities']


# ── Company scoping (no cross-bleed) ────────────────────────────────────────

def test_second_company_never_sees_first_companys_audit_rows(monkeypatch):
    # The admin-device gate is real, single-company, per-install
    # infrastructure with its own dedicated tests above (and in
    # commercial_runtime/identity/tests/test_device_routes.py) -- bypassing
    # it here isolates what THIS test is actually about: the route's own
    # `WHERE company_id=?` scoping, not device/company binding.
    monkeypatch.setattr(retail_api, '_is_admin_device', lambda cid: True)

    client_a, cid_a, uid_a = _make_admin('aud-a')
    ids_a = _seed_real_actions(client_a, cid_a)

    client_b, cid_b, uid_b = _make_admin('aud-b')
    ids_b = _seed_real_actions(client_b, cid_b)

    r_a = client_a.get('/api/sub/retail/audit-log?limit=200')
    r_b = client_b.get('/api/sub/retail/audit-log?limit=200')
    assert r_a.status_code == 200, r_a.get_json()
    assert r_b.status_code == 200, r_b.get_json()

    rows_a = r_a.get_json()['data']
    rows_b = r_b.get_json()['data']
    assert rows_a and rows_b

    ids_row_a = {row['id'] for row in rows_a}
    ids_row_b = {row['id'] for row in rows_b}
    assert ids_row_a.isdisjoint(ids_row_b), "company A and company B audit rows must never overlap"

    # Every row company A sees is attributed to company A's own user, and
    # every entity_id referenced belongs to company A's own seeded data --
    # never company B's, and vice versa.
    assert {row['user_id'] for row in rows_a} == {uid_a}
    assert {row['user_id'] for row in rows_b} == {uid_b}

    product_ids_a = {str(row['entity_id']) for row in rows_a if row['entity'] == 'product'}
    product_ids_b = {str(row['entity_id']) for row in rows_b if row['entity'] == 'product'}
    assert str(ids_a['product_id']) in product_ids_a
    assert str(ids_a['product_id']) not in product_ids_b
    assert str(ids_b['product_id']) in product_ids_b
    assert str(ids_b['product_id']) not in product_ids_a
