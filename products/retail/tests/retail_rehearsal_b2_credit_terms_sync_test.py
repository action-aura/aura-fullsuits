"""REHEARSAL-1007 B2: a customer's credit terms (credit_mode / credit_limit) must reach
the other till.

THE DEFECT, measured in the two-till rehearsal: the owner set a customer to "buy on
credit" on computer A; computer B kept the default 'none' and refused the credit sale,
because credit_mode/credit_limit were local-only columns (update_customer bumped nothing
and queued nothing for a credit-only edit, and the apply side had no column for them).

HOW THIS FILE MODELS "TILL B" HONESTLY: A and B cannot be two live apps in one process
(AURA_APP_DATA resolves at import), so B is this same database put back into the state a
second till is in right after it pulled the customer's CREATE -- credit terms at their
defaults, row_version 1 -- and the real SyncService.apply_pull_result is then fed the
EXACT outbox event A's PATCH queued. The sale that must flip from refused to allowed is
rung through the real create_sale route, which reads those columns itself.

Run (one pytest process per file):
    pytest products/retail/tests/retail_rehearsal_b2_credit_terms_sync_test.py -q
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

DATA = Path(tempfile.mkdtemp(prefix="aura_rehearsal_b2_"))
(DATA / "database" / "subsystems").mkdir(parents=True, exist_ok=True)
os.environ.update(AURA_STANDALONE="1", AURA_BUNDLE_DIR=str(BACKEND_DIR),
                  AURA_APP_DATA=str(DATA), AURA_SITE_RELAY_ENABLED="0")
os.environ.pop("AURA_DEV", None)

from commercial_runtime.licensing_contracts.test_support import seed_active_license  # noqa: E402

seed_active_license(str(DATA), product_code="AURA_RETAIL", platform="WINDOWS")

import app as _app_module  # noqa: E402

app = _app_module.init_app()
app.config["TESTING"] = True

from commercial_runtime.identity.registry_db import get_conn as registry_conn  # noqa: E402
from commercial_runtime.security.passwords import hash_password  # noqa: E402
from commercial_runtime.sync.sync_service import SyncService  # noqa: E402
from database.schema import get_retail_conn  # noqa: E402
import api.retail_api as retail_api  # noqa: E402

API = '/api/sub/retail'


def teardown_module(module):
    shutil.rmtree(DATA, ignore_errors=True)


@pytest.fixture
def client():
    email = f'b2-{uuid.uuid4().hex[:8]}@test.local'
    password = 'B2PW1234567'  # pragma: allowlist secret
    company_id = str(uuid.uuid4())
    conn = registry_conn()
    conn.execute(
        "INSERT INTO users (id, company_id, employee_id, email, password_hash, role, status, "
        "require_password_change) VALUES (?,?,?,?,?,?,?,0)",
        (str(uuid.uuid4()), company_id, 'EMP-0001', email, hash_password(password), 'admin', 'active'))
    conn.commit(); conn.close()
    c = app.test_client()
    r = c.post('/api/auth/login', json={'email': email, 'password': password})
    assert r.status_code == 200, r.get_json()
    c.test_company_id = company_id
    return c


def _product(c):
    r = c.post(f'{API}/products', json={
        'name': 'B2 Widget', 'sku': f'B2-{uuid.uuid4().hex[:8]}', 'cost_price': 50,
        'sell_price': 100, 'tax_rate': 0, 'initial_stock': 500})
    assert r.status_code == 200, r.get_json()
    return r.get_json()['data']['id']


def _credit_sale(c, pid, cust):
    return c.post(f'{API}/sales', json={
        'items': [{'product_id': pid, 'quantity': 1}], 'payment_method': 'credit',
        'amount_paid': 0, 'customer_id': cust, 'idempotency_key': str(uuid.uuid4())})


def _outbox(cust_id, event_type):
    conn = get_retail_conn()
    try:
        row = conn.execute(
            "SELECT payload FROM sync_outbox WHERE entity_type='customer' AND entity_id=? "
            "AND event_type=? ORDER BY rowid DESC LIMIT 1", (cust_id, event_type)).fetchone()
        return json.loads(row['payload'])
    finally:
        conn.close()


def _put_till_b_in_pre_sync_state(cust_id):
    """Till B right after pulling the CREATE: default credit terms, row_version 1."""
    conn = get_retail_conn()
    conn.execute("UPDATE customers SET credit_mode='none', credit_limit=0, row_version=1 WHERE id=?", (cust_id,))
    conn.commit(); conn.close()


def _apply_on_b(event_type, payload, company_id_str):
    conn = get_retail_conn()
    try:
        svc = SyncService(client_factory=lambda: None, get_conn=get_retail_conn,
                          local_company_id_provider=lambda: company_id_str,
                          local_ensure_schema=retail_api._ensure_credit_schema)
        svc.apply_pull_result(conn, {
            "events": [{"entity_type": "customer", "event_type": event_type,
                        "payload": payload, "seq": 1}],
            "cursor": 1})
        conn.commit()
    finally:
        conn.close()


def _terms(cust_id):
    conn = get_retail_conn()
    try:
        r = conn.execute("SELECT credit_mode, credit_limit, row_version FROM customers WHERE id=?",
                         (cust_id,)).fetchone()
        return r['credit_mode'], float(r['credit_limit']), r['row_version']
    finally:
        conn.close()


def _company_of(cust_id):
    conn = get_retail_conn()
    try:
        return str(conn.execute("SELECT company_id FROM customers WHERE id=?", (cust_id,)).fetchone()[0])
    finally:
        conn.close()


def test_credit_terms_set_on_a_reach_b_and_b_allows_the_credit_sale(client):
    pid = _product(client)
    cust = client.post(f'{API}/customers', json={'name': 'B2 Regular'}).get_json()['data']['id']

    # A sets the terms. The PATCH must now queue a customer update that NAMES them.
    r = client.patch(f'{API}/customers/{cust}', json={'credit_mode': 'unlimited', 'credit_limit': 750})
    assert r.status_code == 200, r.get_json()
    payload = _outbox(cust, 'update')
    assert payload['credit_mode'] == 'unlimited' and float(payload['credit_limit']) == 750.0
    assert {'credit_mode', 'credit_limit'} <= set(payload['_changed_fields'])
    assert payload['row_version'] == 2

    # Till B before the event arrives: stale terms, the credit sale is refused.
    _put_till_b_in_pre_sync_state(cust)
    refused = _credit_sale(client, pid, cust)
    assert refused.status_code == 400, refused.get_json()
    assert 'not allowed to buy on credit' in refused.get_json()['message']

    # The very event A queued arrives on B ...
    _apply_on_b('update', payload, _company_of(cust))
    assert _terms(cust) == ('unlimited', 750.0, 2)

    # ... and B now rings the credit sale.
    ok = _credit_sale(client, pid, cust)
    assert ok.status_code == 200, ok.get_json()
    assert ok.get_json()['data']['balance_due'] == 100.0


def test_stale_credit_event_does_not_overwrite_newer_terms(client):
    """Same last-write-wins rule as name/phone: an OLDER row_version never lands."""
    cust = client.post(f'{API}/customers', json={'name': 'B2 LWW'}).get_json()['data']['id']
    client.patch(f'{API}/customers/{cust}', json={'credit_mode': 'unlimited'})
    payload = _outbox(cust, 'update')            # row_version 2
    conn = get_retail_conn()
    conn.execute("UPDATE customers SET credit_mode='limited', credit_limit=10, row_version=5 WHERE id=?", (cust,))
    conn.commit(); conn.close()
    _apply_on_b('update', payload, _company_of(cust))
    assert _terms(cust) == ('limited', 10.0, 5)


def test_event_from_an_older_device_without_credit_keys_keeps_local_terms(client):
    """An event with NO credit_* keys (older build) must not reset terms to none/0."""
    cust = client.post(f'{API}/customers', json={'name': 'B2 Legacy'}).get_json()['data']['id']
    conn = get_retail_conn()
    conn.execute("UPDATE customers SET credit_mode='unlimited', credit_limit=500 WHERE id=?", (cust,))
    conn.commit(); conn.close()
    legacy = {'id': cust, 'name': 'B2 Legacy renamed', 'phone': '', 'email': '', 'address': '',
              'row_version': 9, 'updated_at_utc': '2026-10-07T00:00:00+00:00'}   # no _changed_fields, no credit keys
    _apply_on_b('update', legacy, _company_of(cust))
    assert _terms(cust) == ('unlimited', 500.0, 9)


def test_new_customer_create_event_carries_default_terms(client):
    cust = client.post(f'{API}/customers', json={'name': 'B2 Fresh'}).get_json()['data']['id']
    payload = _outbox(cust, 'create')
    assert payload['credit_mode'] == 'none' and float(payload['credit_limit']) == 0.0
