"""Aura Retail -- STOCK-BOOKS (crew B): stock moves reach the books, through the
REAL routes. Every figure below is hand-computed (JOD, 3 decimals) from the
scenario's own inputs, never taken from the app's own output.

Plan: tmp/stockbooks/plan.md section 4 (T1, T2, T3, T5, T6, T7, T10).

  T1  PO order -> receive: 1260 then 1200, ties; receive twice refused.
  T2  a LEGACY PO (order journal debited 1200) books nothing at receipt.
  T3  cancel: reverses the order, drops out of the supplier's balance; every
      refusal (twice, after receive, with an active payment, cashier).
  T5  adjustments / exception counts at the unit cost HERE (not cost_price).
  T6  opening stock at create / import: avg_cost set, journal Dr 1200 / Cr 3900.
  T7  transfers: send -> 1250, receive -> 1200 + shortage to 5910, recall.
  T10 a cancelled PO is absent from the supplier statement and aging.

Self-contained bootstrap (same convention as retail_gl_integration_test.py).

Run:
    pytest products/retail/tests/retail_stock_books_routes_test.py -q
"""
import csv
import io
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

DATA = Path(tempfile.mkdtemp(prefix="aura_stock_books_routes_"))
(DATA / "database" / "subsystems").mkdir(parents=True, exist_ok=True)
os.environ.update(AURA_STANDALONE="1", AURA_BUNDLE_DIR=str(BACKEND_DIR), AURA_APP_DATA=str(DATA))
os.environ.pop("AURA_DEV", None)

from commercial_runtime.licensing_contracts.test_support import seed_active_license  # noqa: E402

seed_active_license(str(DATA), product_code="AURA_RETAIL", platform="WINDOWS")

import app as _app_module  # noqa: E402

app = _app_module.init_app()
app.config["TESTING"] = True

from commercial_runtime.identity import user_accounts  # noqa: E402
from commercial_runtime.identity.registry_db import get_conn as registry_conn  # noqa: E402
from commercial_runtime.security.passwords import hash_password  # noqa: E402
from database.schema import get_retail_conn  # noqa: E402
from core.retail import general_ledger as gl  # noqa: E402

API = '/api/sub/retail'


def teardown_module(module):
    shutil.rmtree(DATA, ignore_errors=True)


# ── bootstrap ────────────────────────────────────────────────────────────────

def _login_user(role, company_id):
    email = f'sb-{role}-{uuid.uuid4().hex[:8]}@test.local'
    password = 'StockBooksPW1'  # pragma: allowlist secret -- throwaway fixture value
    user_id = str(uuid.uuid4())
    conn = registry_conn()
    conn.execute(
        "INSERT INTO users (id, company_id, employee_id, email, password_hash, role, status, "
        "require_password_change) VALUES (?,?,?,?,?,?,?,0)",
        (user_id, company_id, f'EMP-{uuid.uuid4().hex[:6]}', email, hash_password(password), role, 'active'))
    conn.execute(
        "INSERT INTO user_permissions (id, user_id, subsystem, access_level) VALUES (?,?,?,?)",
        (str(uuid.uuid4()), user_id, 'retail', 'full'))
    user_accounts.seed_capabilities_for_user(conn, user_id, role)
    conn.commit(); conn.close()
    c = app.test_client()
    r = c.post('/api/auth/login', json={'email': email, 'password': password})
    assert r.status_code == 200, r.get_json()
    c.test_company_id = company_id
    return c


@pytest.fixture
def client():
    return _login_user('admin', str(uuid.uuid4()))


@pytest.fixture
def cashier(client):
    return _login_user('cashier', client.test_company_id)


def _seed_product(c, **kw):
    payload = {'name': 'SB Widget', 'sku': f'SB-{uuid.uuid4().hex[:8]}', 'cost_price': 1.0,
               'sell_price': 5.0, 'tax_rate': 0, 'initial_stock': 0}
    payload.update(kw)
    r = c.post(f'{API}/products', json=payload)
    assert r.status_code == 200, r.get_json()
    return r.get_json()['data']['id']


def _seed_supplier(c):
    r = c.post(f'{API}/suppliers', json={'name': f'SB Supplier {uuid.uuid4().hex[:6]}'})
    assert r.status_code == 200, r.get_json()
    return r.get_json()['data']['id']


def _tb(cid):
    conn = get_retail_conn()
    try:
        return gl.trial_balance(conn, cid)
    finally:
        conn.close()


def _bal(cid, code):
    return [a for a in _tb(cid)['accounts'] if a['code'] == code][0]['balance']


def _journals(cid, **where):
    sql = "SELECT * FROM gl_journals WHERE company_id=?"
    args = [cid]
    for k, v in where.items():
        sql += f" AND {k}=?"
        args.append(v)
    conn = get_retail_conn()
    try:
        return [dict(r) for r in conn.execute(sql + " ORDER BY id", args).fetchall()]
    finally:
        conn.close()


def _inventory_tie(c):
    r = c.get(f'{API}/reports/trial-balance')
    assert r.status_code == 200, r.get_json()
    return r.get_json()['data']['inventory']


def _supplier_balance(c, sid):
    r = c.get(f'{API}/suppliers/{sid}/statement')
    assert r.status_code == 200, r.get_json()
    return r.get_json()['data']['balance']


def _make_po(c, sid, pid, qty=10, cost=4.0, paid=0):
    body = {'supplier_id': sid, 'items': [{'product_id': pid, 'quantity': qty, 'unit_cost': cost}]}
    if paid:
        body['amount_paid'] = paid
    r = c.post(f'{API}/purchase-orders', json=body)
    assert r.status_code == 200, r.get_json()
    return r.get_json()['data']['id']


def _po_order_uid(cid, po_id):
    rows = _journals(cid, source_type='purchase_order', source_id=po_id)
    assert len(rows) == 1, rows
    return rows[0]['uid']


# ═════════════════════════════════════════════════════════════════════════════
# T1 -- PO order then receive
# ═════════════════════════════════════════════════════════════════════════════

def test_t1_po_order_books_1260_then_receive_moves_it_to_inventory(client):
    cid = client.test_company_id
    sid = _seed_supplier(client)
    pid = _seed_product(client)
    po_id = _make_po(client, sid, pid, qty=10, cost=4.0)

    # An ORDER is not stock: 10 x 4.000 = 40.000 sits in Goods Ordered.
    assert _bal(cid, gl.ACCOUNT_GOODS_ORDERED) == 40.0
    assert _bal(cid, gl.ACCOUNT_AP) == 40.0
    assert _supplier_balance(client, sid) == 40.0
    assert _bal(cid, gl.ACCOUNT_INVENTORY) == 0.0
    order_uid = _po_order_uid(cid, po_id)

    r = client.post(f'{API}/purchase-orders/{po_id}/receive')
    assert r.status_code == 200, r.get_json()
    assert _bal(cid, gl.ACCOUNT_INVENTORY) == 40.0
    assert _bal(cid, gl.ACCOUNT_GOODS_ORDERED) == 0.0
    tie = _inventory_tie(client)
    assert tie['status'] == 'ties', tie
    assert tie['inventory_gl'] == 40.0 and tie['stock_value'] == 40.0

    receipts = _journals(cid, source_type='po_receipt')
    assert len(receipts) == 1
    assert receipts[0]['uid'] == gl.stock_journal_uid(order_uid, 'po_receipt')

    # DENY half: receiving again is refused and books nothing more.
    n_journals = len(_journals(cid))
    r = client.post(f'{API}/purchase-orders/{po_id}/receive')
    assert r.status_code == 409
    assert len(_journals(cid)) == n_journals == 2
    assert _bal(cid, gl.ACCOUNT_INVENTORY) == 40.0


# ═════════════════════════════════════════════════════════════════════════════
# T2 -- legacy PO: the order already debited 1200, receipt must not book again
# ═════════════════════════════════════════════════════════════════════════════

def _hand_post_legacy_po(c, cid, sid, pid):
    """A PO shaped like one created BEFORE this wave: the row is inserted
    directly and its order journal debits Inventory (1200) -- the journals
    are append-only, so this is the only honest way to make a legacy one."""
    conn = get_retail_conn()
    conn.execute(
        "INSERT INTO purchase_orders (company_id,po_number,supplier_id,status,subtotal,total,ordered_at,"
        "amount_paid,payment_status) VALUES (?,?,?,'pending',40,40,'2026-01-01',0,'unpaid')",
        (cid, f'PO-LEGACY-{uuid.uuid4().hex[:6]}', sid))
    po_id = conn.execute("SELECT MAX(id) FROM purchase_orders WHERE company_id=?", (cid,)).fetchone()[0]
    conn.execute("INSERT INTO purchase_order_items (po_id,product_id,quantity,unit_cost,total) "
                 "VALUES (?,?,10,4,40)", (po_id, pid))
    gl.post_journal(conn, cid, None, '2026-01-01', 'purchase_order', po_id, 'JOD',
                    [{'account_code': gl.ACCOUNT_INVENTORY, 'debit': 40},
                     {'account_code': gl.ACCOUNT_AP, 'credit': 40}], reference='legacy')
    conn.execute("UPDATE suppliers SET credit_balance=40 WHERE id=?", (sid,))
    conn.commit(); conn.close()
    return po_id


def test_t2_legacy_po_receive_posts_no_second_journal(client):
    cid = client.test_company_id
    sid = _seed_supplier(client)
    pid = _seed_product(client)
    po_id = _hand_post_legacy_po(client, cid, sid, pid)
    assert len(_journals(cid)) == 1

    r = client.post(f'{API}/purchase-orders/{po_id}/receive')
    assert r.status_code == 200, r.get_json()
    assert len(_journals(cid)) == 1, "a legacy PO's stock is already in 1200; receipt must not count it twice"
    assert _bal(cid, gl.ACCOUNT_INVENTORY) == 40.0   # not 80
    assert _bal(cid, gl.ACCOUNT_GOODS_ORDERED) == 0.0


# ═════════════════════════════════════════════════════════════════════════════
# T3 -- cancel
# ═════════════════════════════════════════════════════════════════════════════

def test_t3_cancel_reverses_the_order_and_clears_the_supplier(client):
    cid = client.test_company_id
    sid = _seed_supplier(client)
    pid = _seed_product(client)
    po_id = _make_po(client, sid, pid)
    assert _supplier_balance(client, sid) == 40.0
    order_uid = _po_order_uid(cid, po_id)

    r = client.post(f'{API}/purchase-orders/{po_id}/cancel')
    assert r.status_code == 200, r.get_json()
    assert _bal(cid, gl.ACCOUNT_GOODS_ORDERED) == 0.0
    assert _bal(cid, gl.ACCOUNT_AP) == 0.0
    assert _supplier_balance(client, sid) == 0.0          # was 40
    reversals = _journals(cid, source_type='reversal')
    assert len(reversals) == 1 and reversals[0]['uid'] == gl.reversal_uid(order_uid)

    # DENY half: cancel again, receive, pay -- all refused, nothing posted.
    n = len(_journals(cid))
    assert client.post(f'{API}/purchase-orders/{po_id}/cancel').status_code == 409
    assert len(_journals(cid, source_type='reversal')) == 1
    r = client.post(f'{API}/purchase-orders/{po_id}/receive')
    assert r.status_code == 409
    assert _bal(cid, gl.ACCOUNT_INVENTORY) == 0.0
    r = client.post(f'{API}/purchase-orders/{po_id}/pay', json={'amount': 5})
    assert r.status_code == 409
    assert len(_journals(cid)) == n


def test_t3_cancel_a_received_po_is_refused(client):
    sid = _seed_supplier(client)
    pid = _seed_product(client)
    po_id = _make_po(client, sid, pid)
    assert client.post(f'{API}/purchase-orders/{po_id}/receive').status_code == 200
    assert client.post(f'{API}/purchase-orders/{po_id}/cancel').status_code == 409


def test_t3_cashier_cannot_cancel(client, cashier):
    sid = _seed_supplier(client)
    pid = _seed_product(client)
    po_id = _make_po(client, sid, pid)
    assert cashier.post(f'{API}/purchase-orders/{po_id}/cancel').status_code == 403
    assert client.get(f'{API}/purchase-orders/{po_id}').get_json()['data']['po']['status'] == 'pending'


def _po_payment_ids(cid, po_id):
    conn = get_retail_conn()
    try:
        return [r[0] for r in conn.execute(
            "SELECT id FROM payments WHERE company_id=? AND related_type='po' AND related_id=?",
            (cid, po_id)).fetchall()]
    finally:
        conn.close()


def test_t3_cancel_refused_while_a_payment_is_active_then_allowed_after_void(client):
    cid = client.test_company_id
    sid = _seed_supplier(client)
    pid = _seed_product(client)
    po_id = _make_po(client, sid, pid, paid=15)
    # Deny half: an active down payment blocks the cancel, nothing changes.
    r = client.post(f'{API}/purchase-orders/{po_id}/cancel')
    assert r.status_code == 409 and r.get_json().get('code') == 'PO_HAS_PAYMENTS', r.get_json()
    assert client.get(f'{API}/purchase-orders/{po_id}').get_json()['data']['po']['status'] == 'pending'
    assert len(_journals(cid, source_type='reversal')) == 0

    [pay_id] = _po_payment_ids(cid, po_id)
    r = client.post(f'{API}/payments/{pay_id}/void', json={'reason': 'test'})
    assert r.status_code == 200, r.get_json()

    # Allow half: once voided the order cancels, and the cancel's mirror of the
    # ORDER journal (Dr AP 25, Dr Cash 15, Cr 1260 40) also repairs the cash/AP
    # the void itself never booked (PD1, see the strict xfail below).
    r = client.post(f'{API}/purchase-orders/{po_id}/cancel')
    assert r.status_code == 200, r.get_json()
    assert _bal(cid, gl.ACCOUNT_CASH) == 0.0
    assert _bal(cid, gl.ACCOUNT_AP) == 0.0
    assert _bal(cid, gl.ACCOUNT_GOODS_ORDERED) == 0.0
    assert _supplier_balance(client, sid) == 0.0


@pytest.mark.xfail(strict=True, reason=(
    "PD1 (pre-existing, plan section 6 R-PD1): voiding a PO-CREATION down payment moves the "
    "supplier subledger but posts no GL, because _reverse_payment_journal only finds "
    "customer_payment/supplier_payment/po_payment journals and the down payment lives inside "
    "the purchase_order journal. Cancel repairs it; an open PO does not. Needs W4a."))
def test_pd1_void_of_a_po_down_payment_keeps_ap_tied(client):
    cid = client.test_company_id
    sid = _seed_supplier(client)
    pid = _seed_product(client)
    po_id = _make_po(client, sid, pid, paid=15)
    [pay_id] = _po_payment_ids(cid, po_id)
    assert client.post(f'{API}/payments/{pay_id}/void', json={'reason': 'test'}).status_code == 200
    assert _bal(cid, gl.ACCOUNT_AP) == _supplier_balance(client, sid)   # GL 25 vs supplier 40 today


def test_t3_legacy_po_cancel_reverses_the_inventory_debit(client):
    cid = client.test_company_id
    sid = _seed_supplier(client)
    pid = _seed_product(client)
    po_id = _hand_post_legacy_po(client, cid, sid, pid)
    assert _bal(cid, gl.ACCOUNT_INVENTORY) == 40.0
    r = client.post(f'{API}/purchase-orders/{po_id}/cancel')
    assert r.status_code == 200, r.get_json()
    assert _bal(cid, gl.ACCOUNT_INVENTORY) == 0.0     # Cr 1200 40
    assert _bal(cid, gl.ACCOUNT_AP) == 0.0            # Dr AP 40


# ═════════════════════════════════════════════════════════════════════════════
# T10 -- a cancelled PO is absent from statement and aging; a pending one stays
# ═════════════════════════════════════════════════════════════════════════════

def test_t10_cancelled_po_leaves_statement_and_aging_pending_stays(client):
    sid = _seed_supplier(client)
    pid = _seed_product(client)
    keep = _make_po(client, sid, pid, qty=5, cost=4.0)      # 20.000, stays pending
    gone = _make_po(client, sid, pid, qty=10, cost=4.0)     # 40.000, cancelled
    assert _supplier_balance(client, sid) == 60.0
    assert client.post(f'{API}/purchase-orders/{gone}/cancel').status_code == 200

    st = client.get(f'{API}/suppliers/{sid}/statement').get_json()['data']
    assert st['balance'] == 20.0
    assert [e['amount'] for e in st['events'] if e['kind'] == 'charge'] == [20.0]
    aging = client.get(f'{API}/reports/aging?type=payable').get_json()['data']
    assert round(sum(aging.values()), 3) == 20.0
    assert client.get(f'{API}/purchase-orders/{keep}').get_json()['data']['po']['status'] == 'pending'


# ═════════════════════════════════════════════════════════════════════════════
# T5 -- adjustments valued at the cost HERE
# ═════════════════════════════════════════════════════════════════════════════

def _adjust(c, pid, qty, branch_id=None):
    body = {'quantity': qty, 'reason': 'test'}
    if branch_id is not None:
        body['branch_id'] = branch_id
    r = c.post(f'{API}/products/{pid}/stock-adjust', json=body)
    assert r.status_code == 200, r.get_json()


def test_t5_opening_adjust_and_sale_keep_inventory_equal_to_stock_times_cost(client):
    cid = client.test_company_id
    pid = _seed_product(client, cost_price=2.0, initial_stock=10)
    assert _bal(cid, gl.ACCOUNT_INVENTORY) == 20.0 and _bal(cid, gl.ACCOUNT_OPENING_EQUITY) == 20.0

    _adjust(client, pid, -3)       # Dr 5910 6 / Cr 1200 6
    assert _bal(cid, gl.ACCOUNT_INVENTORY) == 14.0          # 7 x 2.000
    assert _bal(cid, gl.ACCOUNT_SHRINKAGE) == 6.0
    _adjust(client, pid, 1)        # Dr 1200 2 / Cr 5910 2
    assert _bal(cid, gl.ACCOUNT_INVENTORY) == 16.0
    assert _bal(cid, gl.ACCOUNT_SHRINKAGE) == 4.0

    r = client.post(f'{API}/sales', json={
        'items': [{'product_id': pid, 'quantity': 2}], 'payment_method': 'cash',
        'amount_paid': 10.0, 'idempotency_key': str(uuid.uuid4())})
    assert r.status_code == 200, r.get_json()
    assert _bal(cid, gl.ACCOUNT_COGS) == 4.0
    assert _bal(cid, gl.ACCOUNT_INVENTORY) == 12.0          # 6 x 2.000
    tb = _tb(cid)
    assert tb['is_balanced']
    assert _inventory_tie(client)['status'] == 'ties'


def test_t5_adjustment_uses_the_branch_cost_not_cost_price(client):
    """cost_price is 2.000 but 10 units were RECEIVED at 3.000; losing one must
    take 3.000 out of the books, not 2.000."""
    cid = client.test_company_id
    sid = _seed_supplier(client)
    pid = _seed_product(client, cost_price=2.0)
    po_id = _make_po(client, sid, pid, qty=10, cost=3.0)
    assert client.post(f'{API}/purchase-orders/{po_id}/receive').status_code == 200
    assert _bal(cid, gl.ACCOUNT_INVENTORY) == 30.0
    _adjust(client, pid, -1)
    assert _bal(cid, gl.ACCOUNT_SHRINKAGE) == 3.0
    assert _bal(cid, gl.ACCOUNT_INVENTORY) == 27.0
    assert _inventory_tie(client)['status'] == 'ties'


# ═════════════════════════════════════════════════════════════════════════════
# T6 -- opening stock at create and at import
# ═════════════════════════════════════════════════════════════════════════════

def test_t6_opening_stock_sets_the_cost_basis_and_survives_a_cost_edit(client):
    cid = client.test_company_id
    pid = _seed_product(client, cost_price=0.396, initial_stock=4)
    assert _bal(cid, gl.ACCOUNT_INVENTORY) == 1.584
    assert _bal(cid, gl.ACCOUNT_OPENING_EQUITY) == 1.584
    conn = get_retail_conn()
    avg = conn.execute("SELECT avg_cost FROM products WHERE id=?", (pid,)).fetchone()[0]
    conn.close()
    assert avg == 0.396

    r = client.post(f'{API}/sales', json={
        'items': [{'product_id': pid, 'quantity': 2}], 'payment_method': 'cash',
        'amount_paid': 10.0, 'idempotency_key': str(uuid.uuid4())})
    assert r.status_code == 200, r.get_json()
    assert _bal(cid, gl.ACCOUNT_COGS) == 0.792
    assert _bal(cid, gl.ACCOUNT_INVENTORY) == 0.792          # positive (was -0.792)

    # Editing the typed cost must not re-value stock that was already booked.
    assert client.patch(f'{API}/products/{pid}', json={'cost_price': 9}).status_code == 200
    tie = _inventory_tie(client)
    assert tie['stock_value'] == 0.792 and tie['status'] == 'ties', tie

    # Never the v38 backfill's source type.
    assert _journals(cid, source_type='opening_balance') == []
    assert len(_journals(cid, source_type='opening_entry')) == 1


def test_t6_stock_without_a_cost_books_nothing_and_says_not_opened(client):
    cid = client.test_company_id
    _seed_product(client, cost_price=0, initial_stock=5)
    assert _journals(cid, source_type='opening_entry') == []
    tie = _inventory_tie(client)
    assert tie['status'] == 'not_opened' and tie['unbooked_movements'] == 1, tie


def _import(c, rows):
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(['Product Name', 'SKU', 'Selling Price', 'Cost Price', 'Stock'])
    for r in rows:
        w.writerow(r)
    r = c.post('/api/import/execute', data={
        'system': 'retail', 'entity': 'products',
        'mapping': '{"name":"Product Name","sku":"SKU","sell_price":"Selling Price",'
                   '"cost_price":"Cost Price","initial_stock":"Stock"}',
        'file': (io.BytesIO(buf.getvalue().encode()), 'p.csv')}, content_type='multipart/form-data')
    assert r.status_code == 200, r.get_json()
    return r.get_json()


def test_t6_import_books_new_rows_and_a_redeclared_figure(client):
    cid = client.test_company_id
    a, b = f'IA-{uuid.uuid4().hex[:6]}', f'IB-{uuid.uuid4().hex[:6]}'
    _import(client, [['Imp A', a, '5.000', '1.500', '3'], ['Imp B', b, '5.000', '2.000', '4']])
    assert _bal(cid, gl.ACCOUNT_INVENTORY) == 12.5           # 3 x 1.500 + 4 x 2.000
    assert _bal(cid, gl.ACCOUNT_OPENING_EQUITY) == 12.5
    assert _inventory_tie(client)['status'] == 'ties'

    # Re-declare A as 5: a +2 correction at the cost it already carries = 3.000.
    out = _import(client, [['Imp A', a, '5.000', '1.500', '5']])
    assert out['stock_errors'] == []
    assert _bal(cid, gl.ACCOUNT_INVENTORY) == 15.5
    assert _bal(cid, gl.ACCOUNT_OPENING_EQUITY) == 15.5
    assert _journals(cid, source_type='opening_balance') == []
    assert _inventory_tie(client)['status'] == 'ties'


# ═════════════════════════════════════════════════════════════════════════════
# T7 -- transfers
# ═════════════════════════════════════════════════════════════════════════════

def _two_branch_shop(client, cost=2.0, qty=10):
    """Two branches, one product of `qty` @ `cost` at the FIRST (source)."""
    cid = client.test_company_id
    conn = get_retail_conn()
    conn.execute("INSERT INTO branches (company_id,name) VALUES (?,'Warehouse')", (cid,))
    conn.execute("INSERT INTO branches (company_id,name) VALUES (?,'Storefront')", (cid,))
    conn.commit()
    ids = [r[0] for r in conn.execute(
        "SELECT id FROM branches WHERE company_id=? ORDER BY id", (cid,)).fetchall()]
    conn.close()
    pid = _seed_product(client, cost_price=cost, initial_stock=qty)
    conn = get_retail_conn()
    src = conn.execute("SELECT branch_id FROM inventory_balances WHERE company_id=? AND product_id=?",
                       (cid, pid)).fetchone()[0]
    conn.close()
    dest = [i for i in ids if i != src][0]
    return pid, src, dest


def _xfer(c, src, dest, lines):
    r = c.post(f'{API}/stock-transfers', json={
        'source_branch_id': src, 'destination_branch_id': dest,
        'items': [{'product_id': p, 'quantity': q} for p, q in lines]})
    assert r.status_code == 200, r.get_json()
    return r.get_json()['data']['id']


def _xfer_items(c, tid):
    return c.get(f'{API}/stock-transfers/{tid}').get_json()['data']['items']


def _xfer_receive(c, tid, counts):
    items = _xfer_items(c, tid)
    return c.post(f'{API}/stock-transfers/{tid}/receive', json={
        'items': [{'id': i['id'], 'quantity_received': q} for i, q in zip(items, counts)]})


def _on_hand(cid, pid, bid):
    conn = get_retail_conn()
    try:
        row = conn.execute("SELECT quantity_on_hand FROM inventory_balances WHERE company_id=? AND "
                           "product_id=? AND branch_id=?", (cid, pid, bid)).fetchone()
        return float(row[0]) if row else 0.0
    finally:
        conn.close()


def test_t7_send_then_short_receive_writes_the_loss_to_shrinkage(client):
    cid = client.test_company_id
    pid, src, dest = _two_branch_shop(client)
    tid = _xfer(client, src, dest, [(pid, 4)])
    assert client.post(f'{API}/stock-transfers/{tid}/send').status_code == 200
    assert _bal(cid, gl.ACCOUNT_STOCK_IN_TRANSIT) == 8.0
    assert _bal(cid, gl.ACCOUNT_INVENTORY) == 12.0
    assert _inventory_tie(client)['status'] == 'ties'

    r = _xfer_receive(client, tid, [3])
    assert r.status_code == 200, r.get_json()
    short = r.get_json()['data']['shortages']
    assert len(short) == 1 and short[0]['written_off_value'] == 2.0
    assert _bal(cid, gl.ACCOUNT_STOCK_IN_TRANSIT) == 0.0      # never negative, never stuck
    assert _bal(cid, gl.ACCOUNT_INVENTORY) == 18.0            # 6 @ source + 3 @ dest, x 2.000
    assert _bal(cid, gl.ACCOUNT_SHRINKAGE) == 2.0
    assert _inventory_tie(client)['status'] == 'ties'
    # The journal lines themselves: Dr 1200 6, Dr 5910 2, Cr 1250 8.
    [j] = _journals(cid, uid=gl.stock_journal_uid(tid, 'transfer_receive'))
    conn = get_retail_conn()
    lines = {(r['code']): (r['debit'], r['credit']) for r in conn.execute(
        "SELECT a.code AS code, l.debit, l.credit FROM gl_journal_lines l "
        "JOIN gl_accounts a ON a.id=l.account_id WHERE l.journal_id=?", (j['id'],)).fetchall()}
    conn.close()
    assert lines == {'1200': (6.0, 0.0), '1250': (0.0, 8.0), '5910': (2.0, 0.0)}
    # Deny half: a second receive is refused and posts nothing.
    assert _xfer_receive(client, tid, [3]).status_code == 409
    assert len(_journals(cid, source_type='stock_transfer')) == 2


def test_t7_over_receipt_credits_shrinkage(client):
    cid = client.test_company_id
    pid, src, dest = _two_branch_shop(client)
    tid = _xfer(client, src, dest, [(pid, 4)])
    assert client.post(f'{API}/stock-transfers/{tid}/send').status_code == 200
    assert _xfer_receive(client, tid, [5]).status_code == 200
    assert _bal(cid, gl.ACCOUNT_STOCK_IN_TRANSIT) == 0.0
    assert _bal(cid, gl.ACCOUNT_INVENTORY) == 22.0            # 6 + 5 units x 2.000
    assert _bal(cid, gl.ACCOUNT_SHRINKAGE) == -2.0            # a credit on a debit-normal account


def test_t7_legacy_in_transit_transfer_posts_nothing_on_receive(client):
    """In transit BEFORE this wave: no send journal, so receiving must not
    credit a 1250 that never held it."""
    cid = client.test_company_id
    pid, src, dest = _two_branch_shop(client)
    tid = str(uuid.uuid4())
    conn = get_retail_conn()
    conn.execute("INSERT INTO stock_transfers (id,company_id,source_branch_id,destination_branch_id,status) "
                 "VALUES (?,?,?,?,'in_transit')", (tid, cid, src, dest))
    conn.execute("INSERT INTO stock_transfer_items (id,transfer_id,product_id,quantity_sent,unit_cost) "
                 "VALUES (?,?,?,4,2)", (str(uuid.uuid4()), tid, pid))
    conn.commit(); conn.close()
    n = len(_journals(cid))
    assert _xfer_receive(client, tid, [4]).status_code == 200
    assert len(_journals(cid)) == n
    assert _bal(cid, gl.ACCOUNT_STOCK_IN_TRANSIT) == 0.0


def test_t7_recall_puts_stock_and_books_back(client):
    cid = client.test_company_id
    pid, src, dest = _two_branch_shop(client)
    tid = _xfer(client, src, dest, [(pid, 4)])
    assert client.post(f'{API}/stock-transfers/{tid}/send').status_code == 200
    assert _on_hand(cid, pid, src) == 6.0

    r = client.post(f'{API}/stock-transfers/{tid}/recall')
    assert r.status_code == 200, r.get_json()
    assert _on_hand(cid, pid, src) == 10.0
    assert _bal(cid, gl.ACCOUNT_STOCK_IN_TRANSIT) == 0.0
    assert _bal(cid, gl.ACCOUNT_INVENTORY) == 20.0
    assert _inventory_tie(client)['status'] == 'ties'
    send_j = _journals(cid, uid=gl.stock_journal_uid(tid, 'transfer_send'))[0]
    assert _journals(cid, source_type='reversal')[0]['uid'] == gl.reversal_uid(send_j['uid'])

    # Deny half: receive after recall, recall twice, recall a pending one.
    assert _xfer_receive(client, tid, [4]).status_code == 409
    assert client.post(f'{API}/stock-transfers/{tid}/recall').status_code == 409
    assert _on_hand(cid, pid, dest) == 0.0
    pending = _xfer(client, src, dest, [(pid, 1)])
    assert client.post(f'{API}/stock-transfers/{pending}/recall').status_code == 409


def test_t7_cancel_after_send_is_still_refused(client):
    """Unchanged by the wave: cancel is for a PENDING transfer; recall is the
    way back for a sent one."""
    pid, src, dest = _two_branch_shop(client)
    tid = _xfer(client, src, dest, [(pid, 4)])
    assert client.post(f'{API}/stock-transfers/{tid}/send').status_code == 200
    assert client.post(f'{API}/stock-transfers/{tid}/cancel').status_code == 409


def test_t7_rounding_the_1250_credit_equals_the_send_debit_to_the_fil(client):
    """Two lines of 0.5 x 0.333: each rounds to 0.167 at send (0.1665 half-up),
    so the send journal debits 0.334. The receipt must credit exactly that."""
    cid = client.test_company_id
    pid1, src, dest = _two_branch_shop(client, cost=0.333, qty=10)
    pid2 = _seed_product(client, cost_price=0.333, initial_stock=10)
    tid = _xfer(client, src, dest, [(pid1, 0.5), (pid2, 0.5)])
    assert client.post(f'{API}/stock-transfers/{tid}/send').status_code == 200
    assert _bal(cid, gl.ACCOUNT_STOCK_IN_TRANSIT) == 0.334
    assert _xfer_receive(client, tid, [0.5, 0.5]).status_code == 200
    assert _bal(cid, gl.ACCOUNT_STOCK_IN_TRANSIT) == 0.0
    assert _bal(cid, gl.ACCOUNT_SHRINKAGE) == 0.0


# ═════════════════════════════════════════════════════════════════════════════
# STOCK-BOOKS verify round (F1b, F2, F3, F4, F5, F6, F7)
# ═════════════════════════════════════════════════════════════════════════════

def _sell(c, pid, qty):
    r = c.post(f'{API}/sales', json={'items': [{'product_id': pid, 'quantity': qty}], 'payment_method': 'cash',
                                    'amount_paid': 100.0, 'idempotency_key': str(uuid.uuid4())})
    assert r.status_code == 200, r.get_json()
    return r.get_json()['data']


def _return(c, sale, pid, qty):
    r = c.post(f'{API}/returns', json={'sale_id': sale['id'], 'items': [{'product_id': pid, 'quantity': qty}],
                                      'idempotency_key': str(uuid.uuid4())})
    assert r.status_code == 200, r.get_json()


def _branch_avg(cid, pid, bid):
    conn = get_retail_conn()
    try:
        row = conn.execute("SELECT quantity_on_hand, avg_cost FROM inventory_balances WHERE company_id=? AND "
                           "product_id=? AND branch_id=?", (cid, pid, bid)).fetchone()
        return float(row[0]), float(row[1])
    finally:
        conn.close()


def _working_branch(cid, pid):
    conn = get_retail_conn()
    try:
        return conn.execute("SELECT branch_id FROM inventory_balances WHERE company_id=? AND product_id=?",
                            (cid, pid)).fetchone()[0]
    finally:
        conn.close()


def test_f1b_return_restock_blends_at_the_sale_snapshot_and_the_books_agree(client):
    """10 @ 2.000; sell 4 (snapshot 2.000) -> 6 left @ 2.000; receive 6 @ 6.000 ->
    12 @ (12+36)/12 = 4.000; return the 4: (12 x 4.000 + 4 x 2.000)/16 = 3.500.
    Books: opening 20 - COGS 8 + PO 36 + COGS reversal 8 = 56 = 16 x 3.500."""
    cid = client.test_company_id
    sid = _seed_supplier(client)
    pid = _seed_product(client, cost_price=2.0, initial_stock=10)
    bid = _working_branch(cid, pid)
    sale = _sell(client, pid, 4)
    po = _make_po(client, sid, pid, qty=6, cost=6.0)
    assert client.post(f'{API}/purchase-orders/{po}/receive').status_code == 200
    assert _branch_avg(cid, pid, bid) == (12.0, 4.0)
    _return(client, sale, pid, 4)
    assert _branch_avg(cid, pid, bid) == (16.0, 3.5)
    tie = _inventory_tie(client)
    assert tie['inventory_gl'] == 56.0 and tie['stock_value'] == 56.0 and tie['status'] == 'ties', tie
    # The restock movement carries the snapshot cost it was blended at (it said 0
    # before, so a peer could not blend it the same way).
    conn = get_retail_conn()
    cost = conn.execute("SELECT unit_cost FROM inventory_movements WHERE company_id=? AND "
                        "movement_type='return_in'", (cid,)).fetchone()[0]
    conn.close()
    assert cost == 2.0


def test_f1b_verifier_m4_case_is_a_rounding_difference_not_a_real_one(client):
    """10 @ 2, sell 2, receive 8 @ 6, return 1 (the verifier's case). 3dp cannot
    hold 66/17, so the shelf value is 17 x 3.882 = 65.994 against books of 66 --
    a 6 fil rounding gap inside the 17 x 0.0005 allowance, shown amber. Before the
    fix the restock was not blended at all: avg stayed 4.000, shelf 68 vs books 66,
    a real 'differs'."""
    cid = client.test_company_id
    sid = _seed_supplier(client)
    pid = _seed_product(client, cost_price=2.0, initial_stock=10)
    bid = _working_branch(cid, pid)
    sale = _sell(client, pid, 2)
    po = _make_po(client, sid, pid, qty=8, cost=6.0)
    assert client.post(f'{API}/purchase-orders/{po}/receive').status_code == 200
    _return(client, sale, pid, 1)
    assert _branch_avg(cid, pid, bid) == (17.0, 3.882)
    tie = _inventory_tie(client)
    assert tie['inventory_gl'] == 66.0 and tie['stock_value'] == 65.994, tie
    assert tie['status'] == 'rounding', tie


def test_f2_recall_of_a_zero_cost_transfer_keeps_the_source_average(client):
    """A transfer sent before costs were recorded has unit_cost 0 on its lines.
    Recalling it put 4 units back 'at 0' and blended the source average from 2.000
    to (6 x 2 + 4 x 0)/10 = 1.200 -- cost invented out of thin air. Now: 10 @ 2.000."""
    cid = client.test_company_id
    pid, src, dest = _two_branch_shop(client, cost=2.0, qty=10)
    tid = _xfer(client, src, dest, [(pid, 4)])
    assert client.post(f'{API}/stock-transfers/{tid}/send').status_code == 200
    conn = get_retail_conn()
    conn.execute("UPDATE stock_transfer_items SET unit_cost=0 WHERE transfer_id=?", (tid,))
    conn.commit(); conn.close()
    assert _branch_avg(cid, pid, src) == (6.0, 2.0)
    assert client.post(f'{API}/stock-transfers/{tid}/recall').status_code == 200
    assert _branch_avg(cid, pid, src) == (10.0, 2.0)
    assert _inventory_tie(client)['status'] == 'ties'


def test_f2_recall_with_a_real_cost_still_blends(client):
    """Allow half: a line that left at a real cost blends back exactly as before.
    6 on hand @ 3.000 after a send of 4 @ 3.000 costed line -> back to 10 @ 3.000."""
    cid = client.test_company_id
    pid, src, dest = _two_branch_shop(client, cost=2.0, qty=10)
    tid = _xfer(client, src, dest, [(pid, 4)])
    assert client.post(f'{API}/stock-transfers/{tid}/send').status_code == 200
    conn = get_retail_conn()
    conn.execute("UPDATE inventory_balances SET avg_cost=3 WHERE company_id=? AND product_id=? AND branch_id=?",
                 (cid, pid, src))
    conn.execute("UPDATE stock_transfer_items SET unit_cost=1 WHERE transfer_id=?", (tid,))
    conn.commit(); conn.close()
    assert client.post(f'{API}/stock-transfers/{tid}/recall').status_code == 200
    # (6 x 3.000 + 4 x 1.000) / 10 = 2.200
    assert _branch_avg(cid, pid, src) == (10.0, 2.2)


def test_f3_credit_po_without_a_supplier_is_refused_but_a_fully_paid_one_is_not(client):
    cid = client.test_company_id
    pid = _seed_product(client, cost_price=2.0)
    items = [{'product_id': pid, 'quantity': 10, 'unit_cost': 4.0}]
    n_before = len(_journals(cid))
    # Deny: nothing paid, and part paid -- both leave an unpaid remainder.
    for paid in (0, 15):
        body = {'items': items}
        if paid:
            body['amount_paid'] = paid
        r = client.post(f'{API}/purchase-orders', json=body)
        assert r.status_code == 409, r.get_json()
        assert r.get_json()['code'] == 'SUPPLIER_REQUIRED_FOR_CREDIT_PO'
        assert r.get_json()['message'] == 'Choose the supplier you will owe, or pay the order in full.'
    assert len(_journals(cid)) == n_before            # refused before anything was booked
    # Allow: paid in full, no supplier -> nothing is owed to anyone.
    r = client.post(f'{API}/purchase-orders', json={'items': items, 'amount_paid': 40})
    assert r.status_code == 200, r.get_json()
    assert _bal(cid, gl.ACCOUNT_GOODS_ORDERED) == 40.0
    po = r.get_json()['data']['id']
    assert client.post(f'{API}/purchase-orders/{po}/receive').status_code == 200
    assert _inventory_tie(client)['status'] == 'ties'  # 10 x 4.000 on the shelf and in the books
    # And with a supplier the unpaid order still goes through.
    sid = _seed_supplier(client)
    assert client.post(f'{API}/purchase-orders', json={'supplier_id': sid, 'items': items}).status_code == 200


def _raise_reorder_request(cid, pid):
    rid = str(uuid.uuid4())
    conn = get_retail_conn()
    conn.execute("INSERT INTO reorder_requests (id,company_id,branch_id,product_id,status) "
                 "VALUES (?,?,?,?, 'pending')", (rid, cid, None, pid))
    conn.commit(); conn.close()
    return rid


def test_f3_accepting_a_reorder_for_a_product_without_a_supplier_is_refused(client):
    cid = client.test_company_id
    pid = _seed_product(client, cost_price=2.0, reorder_level=5)
    rid = _raise_reorder_request(cid, pid)
    n_po = len(client.get(f'{API}/purchase-orders').get_json()['data'])
    r = client.post(f'{API}/reorder-requests/{rid}/accept', json={})
    assert r.status_code == 409, r.get_json()
    assert r.get_json()['code'] == 'SUPPLIER_REQUIRED_FOR_CREDIT_PO'
    assert r.get_json()['message'] == 'Set a supplier for this product first.'
    assert len(client.get(f'{API}/purchase-orders').get_json()['data']) == n_po     # no PO drafted
    # The request is still pending (nothing was consumed).
    conn = get_retail_conn()
    assert conn.execute("SELECT status FROM reorder_requests WHERE id=?", (rid,)).fetchone()[0] == 'pending'
    conn.close()
    # Allow half: give the product a supplier and the same request accepts.
    sid = _seed_supplier(client)
    assert client.patch(f'{API}/products/{pid}', json={'supplier_id': sid}).status_code == 200
    assert client.post(f'{API}/reorder-requests/{rid}/accept', json={}).status_code == 200


def test_f4_deleting_a_product_with_stock_writes_it_off(client):
    cid = client.test_company_id
    keep = _seed_product(client, cost_price=1.0, initial_stock=3)
    pid = _seed_product(client, cost_price=2.0, initial_stock=5)
    assert _bal(cid, gl.ACCOUNT_INVENTORY) == 13.0          # 3 x 1.000 + 5 x 2.000
    bid = _working_branch(cid, pid)
    assert client.delete(f'{API}/products/{pid}').status_code == 200
    # 5 x 2.000 = 10.000 left Inventory for Shrinkage; the 3 x 1.000 stay.
    assert _bal(cid, gl.ACCOUNT_INVENTORY) == 3.0
    assert _bal(cid, gl.ACCOUNT_SHRINKAGE) == 10.0
    assert _branch_avg(cid, pid, bid)[0] == 0.0
    tie = _inventory_tie(client)
    assert tie['inventory_gl'] == 3.0 and tie['stock_value'] == 3.0 and tie['status'] == 'ties', tie
    assert _tb(cid)['is_balanced']
    # Allow half: deleting a product with NO stock books nothing.
    empty = _seed_product(client, cost_price=2.0, initial_stock=0)
    n = len(_journals(cid))
    assert client.delete(f'{API}/products/{empty}').status_code == 200
    assert len(_journals(cid)) == n
    # And a second delete of the first is the usual 404, with no second write-off.
    assert client.delete(f'{API}/products/{pid}').status_code == 404
    assert _bal(cid, gl.ACCOUNT_SHRINKAGE) == 10.0
    assert keep


def test_f5_pay_checks_the_status_inside_the_write_transaction(client, monkeypatch):
    """COUNT THE CHECK (ENGINEERING.md shape 1): a 409 for 'cancelled' cannot tell a
    status read that ran under the write lock from one that ran before it. Record
    the statements pay_purchase_order issues and prove the order: BEGIN IMMEDIATE,
    THEN the purchase_orders read, THEN an UPDATE that carries the status guard."""
    import api.retail_api as ra
    sid = _seed_supplier(client)
    pid = _seed_product(client)
    po_id = _make_po(client, sid, pid)
    seen = []
    real = ra.get_retail_conn

    def traced():
        c = real()
        c.set_trace_callback(lambda s: seen.append(' '.join(str(s).split())))
        return c

    monkeypatch.setattr(ra, 'get_retail_conn', traced)
    r = client.post(f'{API}/purchase-orders/{po_id}/pay', json={'amount': 5})
    assert r.status_code == 200, r.get_json()
    begin = [i for i, s in enumerate(seen) if s.upper().startswith('BEGIN IMMEDIATE')]
    read = [i for i, s in enumerate(seen) if s.upper().startswith('SELECT') and 'FROM purchase_orders' in s
            and 'po_number,status' in s]
    upd = [i for i, s in enumerate(seen) if s.upper().startswith('UPDATE PURCHASE_ORDERS SET AMOUNT_PAID')]
    assert len(begin) == 1 and len(read) == 1 and len(upd) == 1, seen
    assert begin[0] < read[0] < upd[0], 'the status read must happen inside the write transaction'
    assert "<> 'cancelled'" in seen[upd[0]], seen[upd[0]]


def test_f5_cancel_then_pay_is_refused_and_pays_nothing(client):
    cid = client.test_company_id
    sid = _seed_supplier(client)
    pid = _seed_product(client)
    po_id = _make_po(client, sid, pid)
    assert client.post(f'{API}/purchase-orders/{po_id}/cancel').status_code == 200
    n = len(_journals(cid))
    r = client.post(f'{API}/purchase-orders/{po_id}/pay', json={'amount': 5})
    assert r.status_code == 409
    assert _po_payment_ids(cid, po_id) == [] and len(_journals(cid)) == n
    assert _supplier_balance(client, sid) == 0.0


def _movement(cid, pid, bid, reference, mtype='purchase_in', qty=1, cost=4.0):
    conn = get_retail_conn()
    conn.execute("INSERT INTO inventory_movements (company_id,product_id,branch_id,movement_type,quantity,"
                 "unit_cost,reference,created_by,uid) VALUES (?,?,?,?,?,?,?,?,?)",
                 (cid, pid, bid, mtype, qty, cost, reference, 'test', str(uuid.uuid4())))
    conn.commit(); conn.close()


def _synced_style_journal(cid, source_type, reference):
    """A journal as the sync apply writes it: no local source_id at all."""
    conn = get_retail_conn()
    gl.post_journal(conn, cid, None, '2026-01-01', source_type, None, 'JOD',
                    [{'account_code': gl.ACCOUNT_INVENTORY, 'debit': 4},
                     {'account_code': gl.ACCOUNT_GOODS_ORDERED, 'credit': 4}], reference=reference)
    conn.commit(); conn.close()


def _local_po_row(cid, sid, po_number):
    conn = get_retail_conn()
    conn.execute("INSERT INTO purchase_orders (company_id,po_number,supplier_id,status,subtotal,total,ordered_at,"
                 "amount_paid,payment_status) VALUES (?,?,?,'received',4,4,'2026-01-01',0,'unpaid')",
                 (cid, po_number, sid))
    conn.commit(); conn.close()


def test_f6_two_computers_with_the_same_po_number_do_not_book_each_other(client):
    """Computer A's PO-DUP journal arrives here (synced: source_id NULL, reference
    'PO-DUP'). THIS computer also has its own PO-DUP, received before the wave, with
    no journal. Matching on the number text let A's journal 'book' our unbooked
    receipt; matching on the LOCAL PO id does not."""
    from core.retail import stock_books
    cid = client.test_company_id
    sid = _seed_supplier(client)
    pid = _seed_product(client, cost_price=4.0, initial_stock=1)   # gives the company its working branch
    bid = _working_branch(cid, pid)
    ref = 'PO-DUP'
    _local_po_row(cid, sid, ref)
    _movement(cid, pid, bid, ref)
    _synced_style_journal(cid, 'po_receipt', ref)
    conn = get_retail_conn()
    try:
        # The seed product's opening stock is booked; the PO-DUP receipt is the one unbooked movement.
        unbooked_with_dup = stock_books.unbooked_movement_count(conn, cid)
        assert unbooked_with_dup == 1
    finally:
        conn.close()
    # Allow half 1: a receipt whose journal IS this computer's own (source_id = the
    # local PO id) is booked.
    ref2 = 'PO-OWN'
    _local_po_row(cid, sid, ref2)
    conn = get_retail_conn()
    own_id = conn.execute("SELECT id FROM purchase_orders WHERE po_number=?", (ref2,)).fetchone()[0]
    gl.post_journal(conn, cid, None, '2026-01-01', 'po_receipt', own_id, 'JOD',
                    [{'account_code': gl.ACCOUNT_INVENTORY, 'debit': 4},
                     {'account_code': gl.ACCOUNT_GOODS_ORDERED, 'credit': 4}], reference=ref2)
    conn.commit(); conn.close()
    _movement(cid, pid, bid, ref2)
    # Allow half 2: a receipt whose PO does NOT exist here (it came from a sibling)
    # is booked by the sibling's synced journal -- the text match is the only link.
    _synced_style_journal(cid, 'po_receipt', 'PO-SIBLING')
    _movement(cid, pid, bid, 'PO-SIBLING')
    conn = get_retail_conn()
    try:
        assert stock_books.unbooked_movement_count(conn, cid) == unbooked_with_dup
    finally:
        conn.close()


def test_f7_cancelling_a_reorder_drafted_po_frees_the_request(client):
    cid = client.test_company_id
    sid = _seed_supplier(client)
    pid = _seed_product(client, cost_price=2.0, reorder_level=5, supplier_id=sid)
    other = _seed_product(client, cost_price=2.0, reorder_level=5, supplier_id=sid)
    rid = _raise_reorder_request(cid, pid)
    rid_other = _raise_reorder_request(cid, other)
    po = client.post(f'{API}/reorder-requests/{rid}/accept', json={}).get_json()['data']['purchase_order_id']
    po_other = client.post(f'{API}/reorder-requests/{rid_other}/accept', json={}).get_json()['data']['purchase_order_id']

    def status(r):
        conn = get_retail_conn()
        try:
            return conn.execute("SELECT status FROM reorder_requests WHERE id=?", (r,)).fetchone()[0]
        finally:
            conn.close()

    def raise_again(p):
        conn = get_retail_conn()
        try:
            conn.execute("INSERT INTO reorder_requests (id,company_id,branch_id,product_id,status) "
                         "VALUES (?,?,?,?, 'pending')", (str(uuid.uuid4()), cid, None, p))
            conn.commit()
            return True
        except Exception:
            return False
        finally:
            conn.close()

    assert status(rid) == 'accepted' and raise_again(pid) is False      # the slot is held while accepted
    assert client.post(f'{API}/purchase-orders/{po}/cancel').status_code == 200
    assert status(rid) == 'declined'
    assert raise_again(pid) is True                                      # a new request can be raised
    # Deny half: cancelling one PO leaves another product's accepted request alone.
    assert status(rid_other) == 'accepted'
    # And a PO NOT drafted from a request leaves every request untouched.
    plain = _make_po(client, sid, other)
    assert client.post(f'{API}/purchase-orders/{plain}/cancel').status_code == 200
    assert status(rid_other) == 'accepted'
    assert po_other
