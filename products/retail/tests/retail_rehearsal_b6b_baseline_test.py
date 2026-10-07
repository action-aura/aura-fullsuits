"""REHEARSAL-1007 B6b: a total spent that came from a PREVIOUS system survives the first sale here.

THE DEFECT (found by B6's own review): B6 made `customers.total_spent` a cache =
MAX(0, this system's sales - refunds), with one exception -- a customer with NO sale row
kept whatever the column held. So a customer imported with "total spent 500" showed 500
until their first sale here, then showed only that sale (the 500 of history vanished).

THE RULE (schema v49): the history is its own column, `customers.total_spent_baseline`,
written by the importer (the figure the person typed IS the history before this system).
The cache is baseline + MAX(0, sales - refunds): a refund larger than this system's sales
floors the DERIVED part at zero and can never eat the imported history.

WHAT IS PROVEN HERE
  1. An imported customer with 500 keeps 500 and gains each new sale (RED on 005d0494:
     the first sale replaced the 500).
  2. A refund larger than this system's sales leaves the baseline intact.
  3. The baseline travels in the customer events the importer queues, and a second
     install applying them ends with the same baseline AND the same cache; an event from
     an older device (no key) leaves a local baseline alone; a stale event does not apply.
  4. The sync twin of the SQL equals the core statement.
  5. A re-import of the same customer replaces the baseline (the new typed figure) and
     refreshes the cache, so history is never double counted.

Run (one pytest process per file):
    pytest products/retail/tests/retail_rehearsal_b6b_baseline_test.py -q
"""
import csv
import io
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

DATA = Path(tempfile.mkdtemp(prefix="aura_rehearsal_b6b_"))
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
from core.retail import customer_spend  # noqa: E402
from database.schema import get_retail_conn  # noqa: E402
import api.retail_api as retail_api  # noqa: E402

API = '/api/sub/retail'


def teardown_module(module):
    shutil.rmtree(DATA, ignore_errors=True)


def _make_shop(price=250.0, stock=50):
    email = f"b6b-{uuid.uuid4().hex[:10]}@test.local"
    password = "B6bPW123456789"  # pragma: allowlist secret
    company_id = str(uuid.uuid4())
    conn = registry_conn()
    conn.execute(
        "INSERT INTO users (id, company_id, employee_id, email, password_hash, role, status, "
        "require_password_change) VALUES (?,?,?,?,?,?,?,0)",
        (str(uuid.uuid4()), company_id, "EMP-0001", email, hash_password(password), "admin", "active"))
    conn.commit(); conn.close()
    rconn = get_retail_conn()
    rconn.execute("INSERT INTO branches (company_id,name) VALUES (?, 'Main')", (company_id,))
    branch_id = rconn.execute("SELECT id FROM branches WHERE company_id=? ORDER BY id LIMIT 1",
                              (company_id,)).fetchone()[0]
    sku = f'B6B-{uuid.uuid4().hex[:6]}'
    rconn.execute("INSERT INTO products (id,company_id,sku,name,cost_price,sell_price,tax_rate) VALUES (?,?,?,?,?,?,0)",
                  (str(uuid.uuid4()), company_id, sku, sku, price / 2, price))
    pid = rconn.execute("SELECT id FROM products WHERE company_id=? AND sku=?", (company_id, sku)).fetchone()[0]
    rconn.execute("INSERT INTO inventory_balances (company_id,product_id,branch_id,quantity_on_hand) VALUES (?,?,?,?)",
                  (company_id, pid, branch_id, stock))
    import random as _random
    for _doc in ('sale', 'return'):
        rconn.execute("INSERT OR REPLACE INTO doc_sequences (company_id,doc_type,last_no) VALUES (?,?,?)",
                      (company_id, _doc, _random.randint(1, 5_000_000)))
    rconn.commit(); rconn.close()
    client = app.test_client()
    r = client.post("/api/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.get_json()
    client.get(f"{API}/settings/tax")
    return client, company_id, pid


def _import_customer(client, name, email, total_spent):
    """The REAL /api/import/execute route with a real CSV (not import_api internals)."""
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=['name', 'email', 'total_spent'])
    w.writeheader(); w.writerow({'name': name, 'email': email, 'total_spent': total_spent})
    r = client.post("/api/import/execute", data={
        'system': 'retail', 'entity': 'customers',
        'mapping': json.dumps({'name': 'name', 'email': 'email', 'total_spent': 'total_spent'}),
        'file': (io.BytesIO(buf.getvalue().encode('utf-8')), 'customers.csv'),
    }, content_type='multipart/form-data')
    assert r.status_code == 200, r.get_json()
    assert r.get_json()['success'] is True, r.get_json()


def _customer(cid, email):
    c = get_retail_conn()
    try:
        return dict(c.execute("SELECT * FROM customers WHERE company_id=? AND email=?",
                              (cid, email)).fetchone())
    finally:
        c.close()


def _sell(client, customer_id, pid, qty=1):
    r = client.post(f'{API}/sales', json={
        'items': [{'product_id': pid, 'quantity': qty}], 'customer_id': customer_id,
        'amount_paid': 250.0 * qty, 'payment_method': 'cash', 'idempotency_key': str(uuid.uuid4())})
    assert r.status_code == 200, r.get_json()
    return r.get_json()['data']


def _apply(cid, events):
    conn = get_retail_conn()
    try:
        svc = SyncService(client_factory=lambda: None, get_conn=get_retail_conn,
                          local_company_id_provider=lambda: cid,
                          local_ensure_schema=retail_api._ensure_credit_schema)
        svc.apply_pull_result(conn, {"events": events, "cursor": len(events)})
        conn.commit()
    finally:
        conn.close()


def _remote_sale_event(customer_id, total, seq=1):
    uid = str(uuid.uuid4())
    return uid, {"entity_type": "sale", "event_type": "create", "seq": seq, "payload": {
        "uid": uid, "sale_number": f"REMOTE-{uuid.uuid4().hex[:8]}", "customer_id": customer_id,
        "subtotal": total, "discount_amount": 0, "tax_amount": 0, "total": total, "amount_paid": total,
        "change_amount": 0, "payment_method": "cash", "status": "completed", "cashier": "B",
        "created_at": "2026-10-07 12:00:00", "created_at_utc": "2026-10-07T12:00:00+00:00"}}


def _remote_return_event(sale_uid, refund, seq=2):
    return {"entity_type": "return", "event_type": "create", "seq": seq, "payload": {
        "uid": str(uuid.uuid4()), "return_number": f"RRET-{uuid.uuid4().hex[:8]}", "sale_uid": sale_uid,
        "refund_amount": refund, "refund_method": "cash", "status": "completed",
        "tender_refund_amount": refund, "ar_forgiven_amount": 0, "store_credit_amount": 0,
        "created_at": "2026-10-07 13:00:00", "created_at_utc": "2026-10-07T13:00:00+00:00"}}


def _imported_customer_events(customer_id):
    c = get_retail_conn()
    try:
        rows = c.execute("SELECT event_type, payload FROM sync_outbox WHERE entity_type='customer' "
                         "AND entity_id=? ORDER BY rowid", (customer_id,)).fetchall()
        return [(r['event_type'], json.loads(r['payload'])) for r in rows]
    finally:
        c.close()


def test_an_imported_total_survives_the_first_sale_here():
    client, cid, pid = _make_shop()
    email = f"imp-{uuid.uuid4().hex[:6]}@t.local"
    _import_customer(client, 'Imported', email, 500)
    cust = _customer(cid, email)
    assert cust['total_spent'] == 500.0 and cust['total_spent_baseline'] == 500.0
    _sell(client, cust['id'], pid)
    assert _customer(cid, email)['total_spent'] == 750.0, \
        'the 500 of history from the previous system must survive the first sale made here'
    _sell(client, cust['id'], pid)
    assert _customer(cid, email)['total_spent'] == 1000.0


def test_a_refund_larger_than_this_systems_sales_leaves_the_baseline_intact():
    client, cid, pid = _make_shop()
    email = f"imp-{uuid.uuid4().hex[:6]}@t.local"
    _import_customer(client, 'Imported', email, 500)
    cust = _customer(cid, email)
    uid, sale_ev = _remote_sale_event(cust['id'], 100.0)
    _apply(cid, [sale_ev])
    assert _customer(cid, email)['total_spent'] == 600.0
    _apply(cid, [_remote_return_event(uid, 400.0, seq=2)])      # refund 400 > the 100 sold here
    row = _customer(cid, email)
    assert row['total_spent'] == 500.0, 'a refund of more than this system sold must floor the DERIVED part, not eat history'
    assert row['total_spent_baseline'] == 500.0


def test_the_baseline_travels_and_a_second_install_ends_with_the_same_figures():
    client, cid, pid = _make_shop()
    email = f"imp-{uuid.uuid4().hex[:6]}@t.local"
    _import_customer(client, 'Imported', email, 500)
    cust = _customer(cid, email)
    events = _imported_customer_events(cust['id'])
    assert events and events[0][0] == 'create'
    assert events[0][1]['total_spent_baseline'] == 500.0, 'the create payload must carry the baseline'
    assert 'total_spent' not in events[0][1], 'the cache itself stays derived, never shipped'

    # A second install (a different company id, as every receiving device stamps its own).
    other = str(uuid.uuid4())
    rc = get_retail_conn()
    try:
        rc.execute("INSERT INTO branches (company_id,name) VALUES (?, 'Main')", (other,))
        rc.commit()
    finally:
        rc.close()
    # Same wire event, but a fresh row id: both "installs" share one test database file, and
    # `customers.id` is its primary key (on real hardware the second install has its own file).
    wire = dict(events[0][1]) | {"id": str(uuid.uuid4())}
    _apply(other, [{"entity_type": "customer", "event_type": "create", "seq": 1, "payload": wire}])
    peer = _customer(other, email)  # noqa
    assert peer['total_spent_baseline'] == 500.0
    assert peer['total_spent'] == 500.0, 'the receiver refreshes its cache from the baseline it just applied'


def test_an_event_without_the_key_and_a_stale_event_leave_the_baseline_alone():
    client, cid, pid = _make_shop()
    email = f"imp-{uuid.uuid4().hex[:6]}@t.local"
    _import_customer(client, 'Imported', email, 500)
    cust = _customer(cid, email)
    base = {"id": cust['id'], "name": "Renamed", "phone": "", "email": email, "address": "",
            "credit_mode": "none", "credit_limit": 0, "row_version": 5, "updated_at_utc": "2026-10-07T10:00:00+00:00"}
    _apply(cid, [{"entity_type": "customer", "event_type": "update", "seq": 1,
                  "payload": dict(base) | {"_changed_fields": ["name"]}}])          # older device: no key
    row = _customer(cid, email)
    assert row['name'] == 'Renamed' and row['total_spent_baseline'] == 500.0 and row['total_spent'] == 500.0
    stale = dict(base) | {"row_version": 1, "total_spent_baseline": 9999.0,
                          "_changed_fields": ["total_spent_baseline"]}
    _apply(cid, [{"entity_type": "customer", "event_type": "update", "seq": 2, "payload": stale}])
    assert _customer(cid, email)['total_spent_baseline'] == 500.0, 'a stale event must not rewrite the baseline'
    fresh = dict(base) | {"row_version": 9, "total_spent_baseline": 700.0,
                          "_changed_fields": ["total_spent_baseline"]}
    _apply(cid, [{"entity_type": "customer", "event_type": "update", "seq": 3, "payload": fresh}])
    row = _customer(cid, email)
    assert row['total_spent_baseline'] == 700.0 and row['total_spent'] == 700.0


def test_reimporting_replaces_the_baseline_without_double_counting_history():
    client, cid, pid = _make_shop()
    email = f"imp-{uuid.uuid4().hex[:6]}@t.local"
    _import_customer(client, 'Imported', email, 500)
    cust = _customer(cid, email)
    _sell(client, cust['id'], pid)
    assert _customer(cid, email)['total_spent'] == 750.0
    _import_customer(client, 'Imported', email, 600)             # same customer, corrected history
    row = _customer(cid, email)
    assert row['total_spent_baseline'] == 600.0
    assert row['total_spent'] == 850.0, 'baseline 600 + the 250 sale made here, not 600 alone and not 500+600'


def test_a_customer_with_no_sales_and_no_baseline_shows_zero():
    client, cid, pid = _make_shop()
    c = get_retail_conn()
    cust = str(uuid.uuid4())
    c.execute("INSERT INTO customers (id,company_id,name) VALUES (?,?,'plain')", (cust, cid))
    customer_spend.refresh_total_spent(c, cid, cust)
    c.commit()
    row = c.execute("SELECT total_spent, total_spent_baseline FROM customers WHERE id=?", (cust,)).fetchone()
    c.close()
    assert row['total_spent'] == 0 and row['total_spent_baseline'] == 0


def test_sync_twin_sql_equals_the_core_statement():
    assert SyncService._REFRESH_TOTAL_SPENT_SQL == customer_spend.REFRESH_TOTAL_SPENT_SQL
