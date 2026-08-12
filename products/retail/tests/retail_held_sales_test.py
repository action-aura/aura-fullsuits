"""
Aura Retail -- hold / resume sale ("park a sale") regression suite.

Covers: holding a cart creates a held_sales row and returns a hold_number;
listing held sales for the acting company; resuming atomically returns the
cart snapshot AND removes the held row (double-resume is a clean 404, not a
duplicate); discarding removes the row without ever touching sales/
sale_items; company_id scoping (one company cannot list/resume/discard
another company's held sale); holding an empty cart is rejected; a full
hold -> resume -> checkout round trip produces a normal, correctly-priced
completed sale via the UNCHANGED POST /sales endpoint; and a sale that is
never held checks out identically either way (this feature must be a
strictly additive change with zero behavior difference for the normal,
non-held flow).

Run individually (this codebase has a known cross-file pytest pollution
issue -- see other *_test.py files' module-level app bootstrap):
    pytest products/retail/tests/retail_held_sales_test.py -v
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

DATA = Path(tempfile.mkdtemp(prefix="aura_retail_held_"))
(DATA / "database" / "subsystems").mkdir(parents=True, exist_ok=True)
os.environ.update(AURA_STANDALONE="1", AURA_BUNDLE_DIR=str(BACKEND_DIR), AURA_APP_DATA=str(DATA))
os.environ.pop("AURA_DEV", None)

from commercial_runtime.licensing_contracts.test_support import seed_active_license  # noqa: E402
seed_active_license(str(DATA), product_code="AURA_RETAIL", platform="WINDOWS")
os.environ.pop("AURA_RETAIL_DEMO_MODE", None)

import app as _app_module  # noqa: E402

app = _app_module.init_app()
app.config["TESTING"] = True

from commercial_runtime.identity.registry_db import get_conn as registry_conn  # noqa: E402
from database.schema import get_retail_conn, RETAIL_SCHEMA_VERSION  # noqa: E402
from commercial_runtime.security.passwords import hash_password  # noqa: E402


def teardown_module(module):
    shutil.rmtree(DATA, ignore_errors=True)


def _make_admin_and_product(price=100.0, tax_rate=15.0, stock=50):
    email = f"held-{uuid.uuid4().hex[:10]}@test.local"
    password = "HeldSalesPW1"
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
    rconn.execute("INSERT INTO branches (company_id,name) VALUES (?, 'Main')", (company_id,))
    branch_id = rconn.execute(
        "SELECT id FROM branches WHERE company_id=? ORDER BY id LIMIT 1", (company_id,)
    ).fetchone()[0]
    # products.id is client-generated UUID TEXT on this schema (multi-device
    # sync foundation), NOT an autoincrement int -- must be supplied
    # explicitly on INSERT, same convention as every other UUID-aware test
    # in this suite (e.g. retail_pricing_test.py, retail_security_test.py).
    # The original version of this helper (written against this branch's
    # pre-UUID base) omitted `id` and read it back via a SELECT after the
    # fact, which silently inserted a NULL id on this schema and then blew
    # up downstream with `NOT NULL constraint failed:
    # inventory_balances.product_id`.
    product_id = str(uuid.uuid4())
    rconn.execute(
        "INSERT INTO products (id,company_id,sku,name,cost_price,sell_price,tax_rate) VALUES (?,?,'HS-1','Held Sale Item',5,?,?)",
        (product_id, company_id, price, tax_rate),
    )
    rconn.execute(
        "INSERT INTO inventory_balances (company_id,product_id,branch_id,quantity_on_hand) VALUES (?,?,?,?)",
        (company_id, product_id, branch_id, stock),
    )
    rconn.commit()
    rconn.close()

    client = app.test_client()
    client.post("/api/auth/login", json={"email": email, "password": password})
    client.get("/api/sub/retail/settings/tax")

    import random as _random
    dconn = get_retail_conn()
    dconn.execute(
        "INSERT OR REPLACE INTO doc_sequences (company_id,doc_type,last_no) VALUES (?,'sale',?)",
        (company_id, _random.randint(1, 5_000_000)),
    )
    dconn.execute(
        "INSERT OR REPLACE INTO doc_sequences (company_id,doc_type,last_no) VALUES (?,'hold',?)",
        (company_id, _random.randint(1, 5_000_000)),
    )
    dconn.commit()
    dconn.close()

    return client, company_id, product_id, branch_id


def _cart_item(pid, name='Held Sale Item', qty=2, unit_price=100.0, tax_rate=15.0):
    return {
        'product_id': pid, 'name': name, 'quantity': qty,
        'unit_price': unit_price, 'tax_rate': tax_rate,
        'line_total': unit_price * qty, 'max_stock': 999,
    }


def _hold(client, pid, qty=2, label='', customer_id=None, discount_pct=0, price=100.0, tax_rate=15.0):
    item = _cart_item(pid, qty=qty, unit_price=price, tax_rate=tax_rate)
    # Display-only figure sent by the client, same shape _saveHold() sends --
    # not recomputed with the full tax formula here since the route never
    # treats it as authoritative (see retail_api.py's held-sales section).
    subtotal = item['line_total']
    return client.post('/api/sub/retail/held-sales', json={
        'items': [item], 'label': label, 'customer_id': customer_id,
        'discount_pct': discount_pct, 'payment_method': 'cash',
        'subtotal': subtotal, 'total': subtotal,
    })


def test_schema_version_is_at_least_11_and_table_exists():
    # Documents the actual version this test suite was written against after
    # being rebased onto feat/email-outbox-foundation (v9) -- see
    # schema.py's RETAIL_SCHEMA_VERSION v11 comment and
    # _migrate_add_held_sales for the full renumbering story, including why
    # this branch's chain deliberately skips v10 (claimed for real by the
    # unmerged feat/shift-cash-drawer branch on the same v9 base).
    assert RETAIL_SCHEMA_VERSION >= 11
    conn = get_retail_conn()
    cols = {r[1]: r[2] for r in conn.execute("PRAGMA table_info(held_sales)").fetchall()}
    conn.close()
    for expected in ('company_id', 'branch_id', 'customer_id', 'hold_number', 'label',
                      'cart_json', 'item_count', 'subtotal', 'total', 'held_by', 'created_at'):
        assert expected in cols
    # customer_id must be TEXT, matching customers.id's UUID shape on this
    # schema -- the original commit declared this INTEGER (written back when
    # customers.id was still a plain autoincrement int on this branch's old
    # base); see _migrate_add_held_sales's docstring for the full story.
    assert cols['customer_id'].upper() == 'TEXT'


def test_hold_empty_cart_rejected():
    client, cid, pid, bid = _make_admin_and_product()
    r = client.post('/api/sub/retail/held-sales', json={'items': []})
    assert r.status_code == 400
    assert 'empty' in r.get_json()['message'].lower()


def test_hold_creates_row_and_returns_hold_number():
    client, cid, pid, bid = _make_admin_and_product()
    r = _hold(client, pid, qty=3, label='Table 4')
    assert r.status_code == 200, r.get_json()
    data = r.get_json()['data']
    assert data['hold_number'].startswith('HOLD-')
    assert data['item_count'] == 1

    conn = get_retail_conn()
    row = conn.execute("SELECT * FROM held_sales WHERE id=?", (data['id'],)).fetchone()
    conn.close()
    assert row is not None
    assert row['company_id'] == cid
    assert row['label'] == 'Table 4'


def test_list_held_sales_scoped_to_company():
    client_a, cid_a, pid_a, bid_a = _make_admin_and_product()
    client_b, cid_b, pid_b, bid_b = _make_admin_and_product()

    _hold(client_a, pid_a, qty=1)
    _hold(client_a, pid_a, qty=2)
    _hold(client_b, pid_b, qty=1)

    ra = client_a.get('/api/sub/retail/held-sales')
    rb = client_b.get('/api/sub/retail/held-sales')
    assert len(ra.get_json()['data']) == 2
    assert len(rb.get_json()['data']) == 1


def test_resume_returns_snapshot_and_deletes_row_atomically():
    client, cid, pid, bid = _make_admin_and_product()
    held = _hold(client, pid, qty=4, label='John - forgot wallet', discount_pct=10).get_json()['data']

    r1 = client.post(f"/api/sub/retail/held-sales/{held['id']}/resume", json={})
    assert r1.status_code == 200, r1.get_json()
    snap = r1.get_json()['data']
    assert snap['hold_number'] == held['hold_number']
    assert snap['label'] == 'John - forgot wallet'
    assert snap['discount_pct'] == 10
    assert len(snap['items']) == 1
    assert snap['items'][0]['product_id'] == pid
    assert snap['items'][0]['quantity'] == 4

    # Row is gone -- a second resume of the same id is a clean 404, not a
    # duplicate cart restore.
    r2 = client.post(f"/api/sub/retail/held-sales/{held['id']}/resume", json={})
    assert r2.status_code == 404

    conn = get_retail_conn()
    count = conn.execute("SELECT COUNT(*) FROM held_sales WHERE id=?", (held['id'],)).fetchone()[0]
    conn.close()
    assert count == 0


def test_hold_and_resume_preserves_uuid_customer_id():
    """Regression test for the INTEGER -> TEXT customer_id fix
    (_migrate_add_held_sales). customers.id is a client-generated UUID on
    this schema (multi-device sync foundation) -- holding a sale against a
    real customer must round-trip that UUID unmangled through the
    held_sales row and back out through resume, not silently drop or
    truncate it the way an INTEGER-declared column combined with the old
    frontend's `+customerId` numeric coercion would have (NaN -> null on
    the wire; see subsystem-retail.js's _saveHold())."""
    client, cid, pid, bid = _make_admin_and_product()
    cust = client.post('/api/sub/retail/customers', json={'name': 'Held Sale Customer'})
    assert cust.status_code == 200, cust.get_json()
    customer_id = cust.get_json()['data']['id']
    assert len(customer_id) == 36  # real UUID string, not a small int

    held = _hold(client, pid, qty=1, customer_id=customer_id).get_json()['data']

    conn = get_retail_conn()
    row = conn.execute("SELECT customer_id FROM held_sales WHERE id=?", (held['id'],)).fetchone()
    conn.close()
    assert row['customer_id'] == customer_id  # stored verbatim, not coerced/truncated

    resumed = client.post(f"/api/sub/retail/held-sales/{held['id']}/resume", json={}).get_json()['data']
    assert resumed['customer_id'] == customer_id  # round-trips back out unchanged


def test_discard_removes_row_without_touching_sales():
    client, cid, pid, bid = _make_admin_and_product()
    held = _hold(client, pid, qty=1).get_json()['data']

    r = client.delete(f"/api/sub/retail/held-sales/{held['id']}")
    assert r.status_code == 200, r.get_json()

    conn = get_retail_conn()
    held_count = conn.execute("SELECT COUNT(*) FROM held_sales WHERE id=?", (held['id'],)).fetchone()[0]
    sales_count = conn.execute("SELECT COUNT(*) FROM sales WHERE company_id=?", (cid,)).fetchone()[0]
    conn.close()
    assert held_count == 0
    assert sales_count == 0  # discarding a draft must never create/touch a sale


def test_discard_nonexistent_held_sale_404s():
    client, cid, pid, bid = _make_admin_and_product()
    r = client.delete('/api/sub/retail/held-sales/999999999')
    assert r.status_code == 404


def test_company_cannot_resume_or_discard_another_companys_held_sale():
    client_a, cid_a, pid_a, bid_a = _make_admin_and_product()
    client_b, cid_b, pid_b, bid_b = _make_admin_and_product()
    held = _hold(client_a, pid_a, qty=1).get_json()['data']

    r_resume = client_b.post(f"/api/sub/retail/held-sales/{held['id']}/resume", json={})
    assert r_resume.status_code == 404

    r_discard = client_b.delete(f"/api/sub/retail/held-sales/{held['id']}")
    assert r_discard.status_code == 404

    # Still resumable by the owning company -- company B's failed attempts
    # must not have deleted it.
    r_owner = client_a.post(f"/api/sub/retail/held-sales/{held['id']}/resume", json={})
    assert r_owner.status_code == 200


def test_hold_then_resume_then_checkout_produces_normal_correctly_priced_sale():
    """Full round trip: hold a cart, "start a new sale" (nothing to do -- the
    cart is already cleared server-side by construction), resume the held
    sale, and check it out through the UNCHANGED POST /sales endpoint. The
    resulting sale must be priced exactly the same as if it had never been
    held -- POST /sales always recomputes unit_price/tax/total from live
    product data (AUDIT-002/AUDIT-003), never from the held snapshot."""
    client, cid, pid, bid = _make_admin_and_product(price=100.0, tax_rate=15.0, stock=10)
    held = _hold(client, pid, qty=2).get_json()['data']

    resumed = client.post(f"/api/sub/retail/held-sales/{held['id']}/resume", json={}).get_json()['data']
    items = resumed['items']
    assert len(items) == 1 and items[0]['quantity'] == 2

    sale_resp = client.post('/api/sub/retail/sales', json={
        'items': [{'product_id': it['product_id'], 'quantity': it['quantity']} for it in items],
        'amount_paid': 999999, 'payment_method': resumed['payment_method'],
        'idempotency_key': str(uuid.uuid4()),
    })
    assert sale_resp.status_code == 200, sale_resp.get_json()
    sale = sale_resp.get_json()['data']
    assert sale['total'] == 230.0  # 2 x 100 = 200, +15% tax = 230

    # Stock decremented exactly once, by the resumed quantity -- holding and
    # resuming must not itself touch inventory.
    conn = get_retail_conn()
    on_hand = conn.execute(
        "SELECT quantity_on_hand FROM inventory_balances WHERE company_id=? AND product_id=? AND branch_id=?",
        (cid, pid, bid),
    ).fetchone()[0]
    conn.close()
    assert on_hand == 8  # 10 - 2


def test_normal_never_held_checkout_is_unaffected():
    """Control case: a sale that is NEVER held must check out identically to
    how it always has -- this feature must be a strictly additive change
    with zero behavior difference for the pre-existing flow."""
    client, cid, pid, bid = _make_admin_and_product(price=50.0, tax_rate=10.0, stock=20)
    r = client.post('/api/sub/retail/sales', json={
        'items': [{'product_id': pid, 'quantity': 3}],
        'amount_paid': 999999, 'payment_method': 'cash', 'idempotency_key': str(uuid.uuid4()),
    })
    assert r.status_code == 200, r.get_json()
    sale = r.get_json()['data']
    assert sale['total'] == 165.0  # 3 x 50 = 150, +10% tax = 165

    conn = get_retail_conn()
    held_count = conn.execute("SELECT COUNT(*) FROM held_sales WHERE company_id=?", (cid,)).fetchone()[0]
    conn.close()
    assert held_count == 0  # a normal sale never creates a held_sales row
