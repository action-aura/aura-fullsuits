"""REHEARSAL-1007 B6: a customer's "total spent" must be the same number on every till.

THE DEFECT, measured in the two-till rehearsal: the Customers screen read 10.440 on one
computer and 1.450 on the other for the SAME customer. `customers.total_spent` was a
running total bumped only by sales and returns rung on THIS computer.

THE RULE: the column is a cache DERIVED from the customer's sales (less the refunds of
returns made against them), refreshed after local writes and after sync applies, and like
`loyalty_points` it never bumps `row_version` and queues no customer event.

WHAT IS PROVEN HERE
  1. Local sales and returns still give the old figures (nothing regressed).
  2. A sale that arrives from ANOTHER computer through the real SyncService apply moves the
     figure (the actual defect), and so does a synced return.
  3. Applying the same events twice does not double it (a cache is replaced, never added to).
  4. No row_version bump and no customer outbox event, on either path.
  5. A customer with no sale rows keeps an imported/hand-set figure.
  6. The sync twin of the SQL equals the core one (the W2a/W4a twin pattern).

Run (one pytest process per file):
    pytest products/retail/tests/retail_rehearsal_b6_total_spent_test.py -q
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

DATA = Path(tempfile.mkdtemp(prefix="aura_rehearsal_b6_"))
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
    email = f"b6-{uuid.uuid4().hex[:10]}@test.local"
    password = "B6PW123456789"  # pragma: allowlist secret
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
    sku = f'B6-{uuid.uuid4().hex[:6]}'
    rconn.execute("INSERT INTO products (id,company_id,sku,name,cost_price,sell_price,tax_rate) VALUES (?,?,?,?,?,?,0)",
                  (str(uuid.uuid4()), company_id, sku, sku, price / 2, price))
    pid = rconn.execute("SELECT id FROM products WHERE company_id=? AND sku=?", (company_id, sku)).fetchone()[0]
    rconn.execute("INSERT INTO inventory_balances (company_id,product_id,branch_id,quantity_on_hand) VALUES (?,?,?,?)",
                  (company_id, pid, branch_id, stock))
    import random as _random
    for _doc in ('sale', 'return'):
        rconn.execute("INSERT OR REPLACE INTO doc_sequences (company_id,doc_type,last_no) VALUES (?,?,?)",
                      (company_id, _doc, _random.randint(1, 5_000_000)))
    customer_id = str(uuid.uuid4())
    rconn.execute("INSERT INTO customers (id,company_id,name,credit_mode,credit_limit,credit_balance,total_spent) "
                  "VALUES (?,?,'B6 Customer','unlimited',0,0,0)", (customer_id, company_id))
    rconn.commit(); rconn.close()
    client = app.test_client()
    r = client.post("/api/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.get_json()
    client.get(f"{API}/settings/tax")
    return client, company_id, pid, customer_id


def _row(cid, customer_id):
    c = get_retail_conn()
    try:
        return c.execute("SELECT total_spent, row_version FROM customers WHERE id=? AND company_id=?",
                         (customer_id, cid)).fetchone()
    finally:
        c.close()


def _customer_events(customer_id):
    c = get_retail_conn()
    try:
        return c.execute("SELECT COUNT(*) FROM sync_outbox WHERE entity_type='customer' AND entity_id=?",
                         (customer_id,)).fetchone()[0]
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
    """The `sale` event a SECOND till would have queued for this customer."""
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


def test_local_sales_and_returns_still_net_correctly():
    client, cid, pid, cust = _make_shop()
    s1 = _sell(client, cust, pid)
    _sell(client, cust, pid)
    assert _row(cid, cust)['total_spent'] == 500.0
    r = client.post(f'{API}/returns', json={'sale_id': s1['id'], 'items': [{'product_id': pid, 'quantity': 1}],
                                            'reason': 'b6', 'idempotency_key': str(uuid.uuid4())})
    assert r.status_code == 200, r.get_json()
    assert _row(cid, cust)['total_spent'] == 250.0


def test_a_sale_from_the_other_till_moves_the_figure_and_replays_do_not_double_it():
    client, cid, pid, cust = _make_shop()
    _sell(client, cust, pid)                                    # this till: 250
    assert _row(cid, cust)['total_spent'] == 250.0
    uid, ev = _remote_sale_event(cust, 10.44)                   # the OTHER till: 10.440
    _apply(cid, [ev])
    assert round(_row(cid, cust)['total_spent'], 3) == 260.44, \
        'a sale applied from another till must show in this till\'s total spent'
    _apply(cid, [ev])                                           # replay: same uid, no double count
    assert round(_row(cid, cust)['total_spent'], 3) == 260.44


def test_a_return_from_the_other_till_nets_the_figure():
    client, cid, pid, cust = _make_shop()
    uid, sale_ev = _remote_sale_event(cust, 100.0)
    _apply(cid, [sale_ev])
    assert _row(cid, cust)['total_spent'] == 100.0
    _apply(cid, [_remote_return_event(uid, 30.0)])
    assert _row(cid, cust)['total_spent'] == 70.0
    _apply(cid, [_remote_return_event(uid, 500.0, seq=3)])      # over-refund floors at zero
    assert _row(cid, cust)['total_spent'] == 0.0


def test_refresh_never_bumps_row_version_or_queues_a_customer_event():
    client, cid, pid, cust = _make_shop()
    events_before = _customer_events(cust)
    version_before = _row(cid, cust)['row_version']
    _sell(client, cust, pid)
    _apply(cid, [_remote_sale_event(cust, 5.0)[1]])
    assert _row(cid, cust)['row_version'] == version_before
    assert _customer_events(cust) == events_before


def test_a_customer_with_no_sales_keeps_an_imported_figure():
    client, cid, pid, cust = _make_shop()
    conn = get_retail_conn()
    conn.execute("UPDATE customers SET total_spent=2400 WHERE id=?", (cust,))
    conn.commit()
    customer_spend.refresh_total_spent(conn, cid, cust)
    conn.commit(); conn.close()
    assert _row(cid, cust)['total_spent'] == 2400.0


def test_sync_twin_sql_equals_the_core_statement():
    assert SyncService._REFRESH_TOTAL_SPENT_SQL == customer_spend.REFRESH_TOTAL_SPENT_SQL
