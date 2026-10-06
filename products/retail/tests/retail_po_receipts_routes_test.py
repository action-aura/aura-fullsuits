"""Aura Retail -- W4a-2 (retail schema v46): partial receive and supplier debt
at DELIVERY, through the REAL routes on one computer.

Every figure is hand-computed (JOD, 3 dp) from the scenario's inputs, never
read back from the app. Cross-computer behaviour is proven elsewhere
(retail_po_receipts_twodevice_test.py, retail_po_receipts_apply_test.py).

  P1  partial 6 of 10 -> 'partial', stock +6, AP/Inventory/supplier = 24.000;
      the rest -> 'received', everything 40.000 and the receipts sum to the total
  P2  OVER_RECEIPT refused and nothing written; an empty body receives the rest
  P3  close short: partial -> closed, balance stays at what arrived, nothing more
      can be received, refusals (not partial / no supplier)
  P4  cancel after a receipt -> 409 PO_HAS_RECEIPTS; a pending receipt-basis
      order cancels with nothing to reverse
  P5  a down payment before delivery: supplier reads -15, AP ties, a void
      reverses its journal (PD1 fixed for receipt-basis orders)
  P6  an identical receipt already recorded -> already_recorded, stock once
  P7  the statement shows one charge per receipt and ends on the balance
  P8  a LEGACY order-basis order: partial receive moves its share of 1260
      (three thirds sum to the order's debit EXACTLY), close short releases
      the rest, the statement shows the "closed short" credit
  P9  the queued receipt events carry exactly the agreed keys, and receiving
      queues NO header status update
  P10 the sync hook is installed by install_party_balance_refresh and
      re-derives status from receipts

Run (one file per process):
    pytest products/retail/tests/retail_po_receipts_routes_test.py -q
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

DATA = Path(tempfile.mkdtemp(prefix="aura_po_receipts_routes_"))
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
from core.retail import purchase_orders as po_core  # noqa: E402
from api import retail_api  # noqa: E402

API = '/api/sub/retail'

RECEIPT_KEYS = {'uid', 'po_uid', 'branch_uid', 'received_at', 'value', 'line_count',
                'created_by', 'actor_user_uid', 'terminal_id', 'created_at_utc'}
LINE_KEYS = {'uid', 'receipt_uid', 'po_item_uid', 'quantity', 'movement_uid'}


def teardown_module(module):
    shutil.rmtree(DATA, ignore_errors=True)


@pytest.fixture(autouse=True)
def _no_background_push(monkeypatch):
    """These tests READ the outbox; the real route's `_sync_nudge()` starts a
    background push whose ack DELETES outbox rows (see retail_po_sync_routes_test)."""
    monkeypatch.setattr(retail_api, '_sync_nudge', lambda: None)


def _login_user(role, company_id):
    email = f'rc-{role}-{uuid.uuid4().hex[:8]}@test.local'
    password = 'PoReceiptPW1'  # pragma: allowlist secret -- throwaway fixture value
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
    payload = {'name': 'RC Widget', 'sku': f'RC-{uuid.uuid4().hex[:8]}', 'cost_price': 1.0,
               'sell_price': 5.0, 'tax_rate': 0, 'initial_stock': 0}
    payload.update(kw)
    r = c.post(f'{API}/products', json=payload)
    assert r.status_code == 200, r.get_json()
    return r.get_json()['data']['id']


def _seed_supplier(c):
    r = c.post(f'{API}/suppliers', json={'name': f'RC Supplier {uuid.uuid4().hex[:6]}'})
    assert r.status_code == 200, r.get_json()
    return r.get_json()['data']['id']


def _make_po(c, sid, pid, qty=10, cost=4.0, paid=0):
    body = {'items': [{'product_id': pid, 'quantity': qty, 'unit_cost': cost}]}
    if sid:
        body['supplier_id'] = sid
    if paid:
        body['amount_paid'] = paid
    r = c.post(f'{API}/purchase-orders', json=body)
    assert r.status_code == 200, r.get_json()
    return r.get_json()['data']['id']


def _q(sql, *args):
    conn = get_retail_conn()
    try:
        return [dict(r) for r in conn.execute(sql, args).fetchall()]
    finally:
        conn.close()


def _po(po_id):
    return _q("SELECT * FROM purchase_orders WHERE id=?", po_id)[0]


def _item(po_id):
    return _q("SELECT * FROM purchase_order_items WHERE po_id=? ORDER BY id", po_id)[0]


def _on_hand(cid, pid):
    return _q("SELECT COALESCE(SUM(quantity_on_hand),0) AS q FROM inventory_balances "
              "WHERE company_id=? AND product_id=?", cid, pid)[0]['q']


def _receive(c, po_id, qty=None, key=None):
    if qty is None and key is None:
        return c.post(f'{API}/purchase-orders/{po_id}/receive')
    body = {}
    if qty is not None:
        item = _item(po_id)
        body['lines'] = [{'item_id': item['id'], 'quantity': qty}]
    if key is not None:
        body['receipt_key'] = key
    return c.post(f'{API}/purchase-orders/{po_id}/receive', json=body)


def _bal(cid, code):
    conn = get_retail_conn()
    try:
        tb = gl.trial_balance(conn, cid)
    finally:
        conn.close()
    return [a for a in tb['accounts'] if a['code'] == code][0]['balance']


def _ap_ties(cid):
    conn = get_retail_conn()
    try:
        return gl.subledger_tie(conn, cid, gl.trial_balance(conn, cid))['ap_ties']
    finally:
        conn.close()


def _journals(cid, **where):
    sql = "SELECT * FROM gl_journals WHERE company_id=?"
    args = [cid]
    for k, v in where.items():
        sql += f" AND {k}=?"
        args.append(v)
    return _q(sql + " ORDER BY id", *args)


def _supplier_balance(c, sid):
    r = c.get(f'{API}/suppliers/{sid}/statement')
    assert r.status_code == 200, r.get_json()
    return r.get_json()['data']['balance']


def _events(entity_type, event_type=None):
    sql = "SELECT * FROM sync_outbox WHERE entity_type=?"
    args = [entity_type]
    if event_type:
        sql += " AND event_type=?"; args.append(event_type)
    rows = _q(sql + " ORDER BY rowid", *args)
    for r in rows:
        r['payload'] = json.loads(r['payload'])
    return rows


# ═════════════════════════════════════════════════════════════════════════════
# P1 -- partial receive, then the rest
# ═════════════════════════════════════════════════════════════════════════════

def test_p1_partial_then_rest_derives_status_stock_and_debt(client):
    cid = client.test_company_id
    sid = _seed_supplier(client)
    pid = _seed_product(client)
    po_id = _make_po(client, sid, pid, qty=10, cost=4.0)             # total 40.000

    # An ORDER books nothing any more (W4a-2): the debt arrives with the goods.
    assert _journals(cid) == []
    assert _supplier_balance(client, sid) == 0.0
    assert _po(po_id)['ap_basis'] == 'receipt' and _po(po_id)['order_journal_uid'] is None

    r = _receive(client, po_id, 6)                                    # 6 x 4.000 = 24.000
    assert r.status_code == 200, r.get_json()
    assert r.get_json()['data']['status'] == 'partial'
    po = _po(po_id)
    assert po['status'] == 'partial' and po['received_at'] is None
    assert _item(po_id)['received_qty'] == 6
    assert _on_hand(cid, pid) == 6
    assert _bal(cid, gl.ACCOUNT_AP) == 24.0
    assert _bal(cid, gl.ACCOUNT_INVENTORY) == 24.0
    assert _supplier_balance(client, sid) == 24.0
    assert _ap_ties(cid)
    assert len(_journals(cid, source_type='po_receipt')) == 1

    r = _receive(client, po_id)                                       # no body: the remaining 4
    assert r.status_code == 200, r.get_json()
    assert r.get_json()['data']['status'] == 'received'
    po = _po(po_id)
    assert po['status'] == 'received' and po['received_at']
    assert _item(po_id)['received_qty'] == 10 and _on_hand(cid, pid) == 10
    assert _bal(cid, gl.ACCOUNT_AP) == 40.0 and _bal(cid, gl.ACCOUNT_INVENTORY) == 40.0
    assert _supplier_balance(client, sid) == 40.0 and _ap_ties(cid)
    # The receipts sum to the order's total exactly (24.000 + 16.000).
    vals = [r_['value'] for r_ in _q("SELECT value FROM purchase_order_receipts WHERE po_id=? ORDER BY id", po_id)]
    assert vals == [24.0, 16.0]
    assert len(_journals(cid, source_type='po_receipt')) == 2

    # Deny half: nothing left to receive.
    n = len(_journals(cid))
    assert _receive(client, po_id).status_code == 409
    assert len(_journals(cid)) == n and _on_hand(cid, pid) == 10


# ═════════════════════════════════════════════════════════════════════════════
# P2 -- over-receipt refused
# ═════════════════════════════════════════════════════════════════════════════

def test_p2_over_receipt_is_refused_and_writes_nothing(client):
    cid = client.test_company_id
    sid = _seed_supplier(client)
    pid = _seed_product(client)
    po_id = _make_po(client, sid, pid, qty=10, cost=4.0)
    assert _receive(client, po_id, 7).status_code == 200

    n_receipts = len(_q("SELECT id FROM purchase_order_receipts WHERE po_id=?", po_id))
    n_journals = len(_journals(cid))
    r = _receive(client, po_id, 4)                                    # only 3 remain
    assert r.status_code == 409 and r.get_json().get('code') == 'OVER_RECEIPT', r.get_json()
    assert _on_hand(cid, pid) == 7
    assert len(_q("SELECT id FROM purchase_order_receipts WHERE po_id=?", po_id)) == n_receipts
    assert len(_journals(cid)) == n_journals

    # Allow half: exactly the remainder is fine.
    assert _receive(client, po_id, 3).status_code == 200
    assert _po(po_id)['status'] == 'received'

    # A bad body is a 400, not a receipt.
    po2 = _make_po(client, sid, pid, qty=2, cost=1.0)
    item = _item(po2)
    for bad in ([{'item_id': item['id'], 'quantity': 0}],
                [{'item_id': item['id'], 'quantity': -1}],
                [{'item_id': 999999, 'quantity': 1}]):
        r = client.post(f'{API}/purchase-orders/{po2}/receive', json={'lines': bad})
        assert r.status_code == 400, (bad, r.get_json())
    assert _po(po2)['status'] == 'pending'


# ═════════════════════════════════════════════════════════════════════════════
# P3 -- close short
# ═════════════════════════════════════════════════════════════════════════════

def test_p3_close_short_keeps_what_arrived_and_releases_the_rest(client, cashier):
    cid = client.test_company_id
    sid = _seed_supplier(client)
    pid = _seed_product(client)
    po_id = _make_po(client, sid, pid, qty=10, cost=4.0)

    # Deny: not partial yet.
    r = client.post(f'{API}/purchase-orders/{po_id}/close')
    assert r.status_code == 409 and r.get_json().get('code') == 'CLOSE_SHORT_NOT_PARTIAL'

    assert _receive(client, po_id, 6).status_code == 200
    assert cashier.post(f'{API}/purchase-orders/{po_id}/close').status_code == 403
    assert _po(po_id)['status'] == 'partial'

    r = client.post(f'{API}/purchase-orders/{po_id}/close')
    assert r.status_code == 200, r.get_json()
    po = _po(po_id)
    assert po['status'] == 'closed' and po['closed_at']
    # No debt for the 4 that never came: receipt basis booked only the 6.
    assert _supplier_balance(client, sid) == 24.0
    assert _bal(cid, gl.ACCOUNT_AP) == 24.0 and _ap_ties(cid)
    assert _on_hand(cid, pid) == 6

    # Deny half: nothing more can be received, closed twice is refused.
    assert _receive(client, po_id, 1).status_code == 409
    assert client.post(f'{API}/purchase-orders/{po_id}/close').status_code == 409
    assert _on_hand(cid, pid) == 6

    # The header update names the set-once timestamps, no status.
    ups = [e for e in _events('purchase_order', 'update') if e['entity_id'] == po['uid']]
    assert len(ups) == 1 and set(ups[0]['payload']) == {'uid', 'cancelled_at', 'closed_at'}
    assert ups[0]['payload']['closed_at'] == po['closed_at'] and ups[0]['payload']['cancelled_at'] is None


def test_p3b_close_short_needs_a_supplier(client):
    pid = _seed_product(client)
    po_id = _make_po(client, None, pid, qty=10, cost=4.0, paid=40)    # fully paid, no supplier
    assert _receive(client, po_id, 6).status_code == 200
    assert _po(po_id)['status'] == 'partial'
    r = client.post(f'{API}/purchase-orders/{po_id}/close')
    assert r.status_code == 409 and r.get_json().get('code') == 'CLOSE_SHORT_NEEDS_SUPPLIER', r.get_json()
    assert _po(po_id)['status'] == 'partial'


# ═════════════════════════════════════════════════════════════════════════════
# P4 -- cancel
# ═════════════════════════════════════════════════════════════════════════════

def test_p4_cancel_after_a_receipt_is_refused_pending_cancels_cleanly(client):
    cid = client.test_company_id
    sid = _seed_supplier(client)
    pid = _seed_product(client)
    gone = _make_po(client, sid, pid, qty=10, cost=4.0)
    part = _make_po(client, sid, pid, qty=10, cost=4.0)
    assert _receive(client, part, 6).status_code == 200

    r = client.post(f'{API}/purchase-orders/{part}/cancel')
    assert r.status_code == 409 and r.get_json().get('code') == 'PO_HAS_RECEIPTS', r.get_json()
    assert _po(part)['status'] == 'partial' and _po(part)['cancelled_at'] is None

    n = len(_journals(cid))
    r = client.post(f'{API}/purchase-orders/{gone}/cancel')
    assert r.status_code == 200, r.get_json()
    assert _po(gone)['status'] == 'cancelled' and _po(gone)['cancelled_at']
    assert len(_journals(cid)) == n, "a receipt-basis order posted nothing, so there is nothing to reverse"
    assert _supplier_balance(client, sid) == 24.0                     # only the part-received one


# ═════════════════════════════════════════════════════════════════════════════
# P5 -- paid before delivery
# ═════════════════════════════════════════════════════════════════════════════

def test_p5_down_payment_before_delivery_is_an_advance_and_a_void_reverses_it(client):
    cid = client.test_company_id
    sid = _seed_supplier(client)
    pid = _seed_product(client)
    po_id = _make_po(client, sid, pid, qty=10, cost=4.0, paid=15)

    # Nothing arrived, 15.000 went out: the supplier owes the shop 15.000.
    assert _supplier_balance(client, sid) == -15.0
    assert _bal(cid, gl.ACCOUNT_AP) == -15.0 and _bal(cid, gl.ACCOUNT_CASH) == -15.0
    assert _ap_ties(cid)
    assert _po(po_id)['amount_paid'] == 15.0

    # An instalment through the pay route lands the same way.
    r = client.post(f'{API}/purchase-orders/{po_id}/pay', json={'amount': 5})
    assert r.status_code == 200, r.get_json()
    assert _supplier_balance(client, sid) == -20.0 and _ap_ties(cid)

    # Delivery of 6 (24.000): AP = 24 - 20 paid = 4.000 owed.
    assert _receive(client, po_id, 6).status_code == 200
    assert _supplier_balance(client, sid) == 4.0 and _bal(cid, gl.ACCOUNT_AP) == 4.0 and _ap_ties(cid)

    # PD1: voiding the down payment brings AP back in step with the supplier.
    pays = _q("SELECT id, amount FROM payments WHERE related_type='po' AND related_id=? ORDER BY id", po_id)
    r = client.post(f'{API}/payments/{pays[0]["id"]}/void', json={'reason': 'typo'})
    assert r.status_code == 200, r.get_json()
    assert _supplier_balance(client, sid) == 19.0                     # 24 - the 5 still paid
    assert _bal(cid, gl.ACCOUNT_AP) == 19.0 and _ap_ties(cid)


# ═════════════════════════════════════════════════════════════════════════════
# P6 -- the identical delivery is already here
# ═════════════════════════════════════════════════════════════════════════════

def test_p6_an_identical_receipt_already_recorded_adds_nothing(client):
    cid = client.test_company_id
    sid = _seed_supplier(client)
    pid = _seed_product(client)
    po_id = _make_po(client, sid, pid, qty=10, cost=4.0)
    po = _po(po_id)

    # Simulate the same delivery ALREADY recorded here (a retry whose first
    # attempt landed): its header carries the uid this request's key mints.
    key = 'p6-key-0001'
    sibling_uid = po_core.keyed_receipt_uid(po['uid'], key)
    conn = get_retail_conn()
    conn.execute("INSERT INTO purchase_order_receipts (company_id,uid,po_id,received_at,value,line_count,legacy) "
                 "VALUES (?,?,?,?,24,NULL,0)", (cid, sibling_uid, po_id, '2026-10-06'))
    conn.commit(); conn.close()

    n_journals = len(_journals(cid))
    r = _receive(client, po_id, 6, key=key)
    assert r.status_code == 200, r.get_json()
    assert r.get_json().get('already_recorded') is True
    assert _on_hand(cid, pid) == 0, "the sibling's movement is not here in this fixture; we must add none"
    assert len(_journals(cid)) == n_journals
    assert _q("SELECT COUNT(*) AS n FROM purchase_order_receipts WHERE po_id=?", po_id)[0]['n'] == 1
    # (the outbox is shared by every test in this file: look for THIS receipt)
    assert [e for e in _events('purchase_order_receipt', 'create') if e['entity_id'] == sibling_uid] == []


# ═════════════════════════════════════════════════════════════════════════════
# P7 -- statement
# ═════════════════════════════════════════════════════════════════════════════

def test_p7_statement_has_one_charge_per_receipt_and_ends_on_the_balance(client):
    sid = _seed_supplier(client)
    pid = _seed_product(client)
    po_id = _make_po(client, sid, pid, qty=10, cost=4.0)
    po_number = _po(po_id)['po_number']
    st = client.get(f'{API}/suppliers/{sid}/statement').get_json()['data']
    assert st['events'] == [] and st['balance'] == 0.0                 # typed order: no debt

    assert _receive(client, po_id, 6).status_code == 200
    assert _receive(client, po_id, 4).status_code == 200
    st = client.get(f'{API}/suppliers/{sid}/statement').get_json()['data']
    charges = [e for e in st['events'] if e['kind'] == 'charge']
    assert [e['amount'] for e in charges] == [24.0, 16.0]
    assert all(e['ref'] == po_number for e in charges)
    assert st['events'][-1]['running_balance'] == st['balance'] == 40.0
    # Aging agrees with the balance (all of it is current).
    aging = client.get(f'{API}/reports/aging?type=payable').get_json()['data']
    assert round(sum(aging.values()), 3) == 40.0


# ═════════════════════════════════════════════════════════════════════════════
# P8 -- a legacy order-basis order
# ═════════════════════════════════════════════════════════════════════════════

def _make_order_basis_po(c, cid, sid, pid, qty=3, cost=33.3333):
    """An order shaped like one created BEFORE v46: ap_basis 'order' and its order
    journal (Dr 1260 / Cr AP for the whole total) already posted. Built from a
    route-created order, flipped and journalled by hand -- journals are
    append-only, so this is the only honest way to make a legacy one."""
    po_id = _make_po(c, sid, pid, qty=qty, cost=cost)
    po = _po(po_id)
    conn = get_retail_conn()
    jid = gl.post_purchase_order(conn, cid, po['branch_id'], po_id, po['po_number'], 'JOD',
                                 0.0, po['total'], po_uid=po['uid'])
    juid = conn.execute("SELECT uid FROM gl_journals WHERE id=?", (jid,)).fetchone()[0]
    conn.execute("UPDATE purchase_orders SET ap_basis='order', order_journal_uid=? WHERE id=?", (juid, po_id))
    conn.commit(); conn.close()
    return po_id


def test_p8_order_basis_partial_receive_moves_its_share_and_close_short_releases_the_rest(client):
    cid = client.test_company_id
    sid = _seed_supplier(client)
    pid = _seed_product(client)
    po_id = _make_order_basis_po(client, cid, sid, pid)               # 3 x 33.3333 -> total 100.000
    assert _po(po_id)['total'] == 100.0
    assert _bal(cid, gl.ACCOUNT_GOODS_ORDERED) == 100.0 and _bal(cid, gl.ACCOUNT_AP) == 100.0
    assert _supplier_balance(client, sid) == 100.0                     # order basis: owed from the order

    # One third arrives: 33.333 moves from 1260 to Inventory; the debt is unchanged.
    assert _receive(client, po_id, 1).status_code == 200
    assert _po(po_id)['status'] == 'partial'
    assert _bal(cid, gl.ACCOUNT_INVENTORY) == 33.333
    assert _bal(cid, gl.ACCOUNT_GOODS_ORDERED) == 66.667
    assert _supplier_balance(client, sid) == 100.0 and _ap_ties(cid)

    # Close short: the undelivered 66.667 is released from AP and 1260.
    r = client.post(f'{API}/purchase-orders/{po_id}/close')
    assert r.status_code == 200, r.get_json()
    assert _bal(cid, gl.ACCOUNT_GOODS_ORDERED) == 0.0
    assert _bal(cid, gl.ACCOUNT_AP) == 33.333
    assert _supplier_balance(client, sid) == 33.333 and _ap_ties(cid)
    st = client.get(f'{API}/suppliers/{sid}/statement').get_json()['data']
    kinds = {e['kind']: e['amount'] for e in st['events']}
    assert kinds['charge'] == 100.0 and kinds['closed_short'] == 66.667
    assert st['events'][-1]['running_balance'] == 33.333
    assert len(_journals(cid, source_type='po_close_short')) == 1


def test_p8b_three_thirds_of_an_order_basis_order_sum_to_its_debit_exactly(client):
    cid = client.test_company_id
    sid = _seed_supplier(client)
    pid = _seed_product(client)
    po_id = _make_order_basis_po(client, cid, sid, pid)
    for _ in range(3):
        assert _receive(client, po_id, 1).status_code == 200
    assert _po(po_id)['status'] == 'received'
    assert _bal(cid, gl.ACCOUNT_INVENTORY) == 100.0                    # not 99.999 / 100.001
    assert _bal(cid, gl.ACCOUNT_GOODS_ORDERED) == 0.0
    assert _supplier_balance(client, sid) == 100.0 and _ap_ties(cid)


# ═════════════════════════════════════════════════════════════════════════════
# P9 -- queued events
# ═════════════════════════════════════════════════════════════════════════════

def test_p9_receive_queues_receipt_and_lines_and_no_header_status_update(client):
    sid = _seed_supplier(client)
    pid = _seed_product(client)
    po_id = _make_po(client, sid, pid, qty=10, cost=4.0)
    po, item = _po(po_id), _item(po_id)
    pc = [e for e in _events('purchase_order', 'create') if e['entity_id'] == po['uid']][0]['payload']
    assert _receive(client, po_id, 6).status_code == 200

    # (the outbox is shared by every test in this file: filter to THIS order)
    recs = [e for e in _events('purchase_order_receipt', 'create') if e['payload']['po_uid'] == po['uid']]
    assert len(recs) == 1 and set(recs[0]['payload']) == RECEIPT_KEYS
    rp = recs[0]['payload']
    assert rp['po_uid'] == po['uid'] and rp['value'] == 24.0 and rp['line_count'] == 1
    # W4a-2 verify R1: a partial with no key is a random uuid4 (no retry dedupe).
    assert str(uuid.UUID(rp['uid'])) == rp['uid']
    lines = [e for e in _events('purchase_order_receipt_line', 'create')
             if e['payload']['receipt_uid'] == rp['uid']]
    assert len(lines) == 1 and set(lines[0]['payload']) == LINE_KEYS
    lp = lines[0]['payload']
    assert lp['receipt_uid'] == rp['uid'] and lp['po_item_uid'] == item['uid'] and lp['quantity'] == 6
    assert lp['uid'] == po_core.receipt_line_uid(rp['uid'], item['uid'])
    assert lp['movement_uid'] == po_core.receipt_line_movement_uid(rp['uid'], item['uid'])
    assert [e for e in _events('purchase_order', 'update') if e['entity_id'] == po['uid']] == []
    # The create payload carries the basis (read right after the create: a
    # background forwarder in the app can ack-delete old outbox rows later).
    assert pc['ap_basis'] == 'receipt' and pc['cancelled_at'] is None and pc['closed_at'] is None


# ═════════════════════════════════════════════════════════════════════════════
# P10 -- the hook the sync apply calls
# ═════════════════════════════════════════════════════════════════════════════

class _FakeService:
    def __init__(self):
        self.hook = None

    def set_po_state_refresh(self, hook):
        self.hook = hook

    def apply_pull_result(self, conn, result, *a, **k):
        return None


def test_p10_install_party_balance_refresh_installs_the_po_state_hook(client):
    cid = client.test_company_id
    sid = _seed_supplier(client)
    pid = _seed_product(client)
    po_id = _make_po(client, sid, pid, qty=10, cost=4.0)
    po, item = _po(po_id), _item(po_id)

    svc = _FakeService()
    retail_api.install_party_balance_refresh(svc)
    assert callable(svc.hook), "the sync service was never handed the purchase-order hook"

    # A receipt that ARRIVED by sync is just rows; the hook derives the state.
    conn = get_retail_conn()
    conn.execute("INSERT INTO purchase_order_receipts (company_id,uid,po_id,received_at,value,line_count,legacy) "
                 "VALUES (?,?,?,?,24,1,0)", (cid, 'r-hook', po_id, '2026-10-06'))
    rid = conn.execute("SELECT id FROM purchase_order_receipts WHERE uid='r-hook'").fetchone()[0]
    conn.execute("INSERT INTO purchase_order_receipt_lines (receipt_id,uid,po_item_id,quantity) VALUES (?,?,?,6)",
                 (rid, 'l-hook', item['id']))
    svc.hook(conn, cid, po_id)
    conn.commit(); conn.close()
    assert _po(po_id)['status'] == 'partial' and _item(po_id)['received_qty'] == 6


# ═════════════════════════════════════════════════════════════════════════════
# V2 verify round 2 (R1-R4)
# ═════════════════════════════════════════════════════════════════════════════

def _receipts(po_id):
    return _q("SELECT * FROM purchase_order_receipts WHERE po_id=? ORDER BY id", po_id)


def test_r1_double_submit_with_one_key_records_one_receipt(client):
    """W4a-2 verify R1 / probe A: a double Enter or a retry carries the same
    key. Before: two receipts, stock 6 for ONE delivery of 3. It also proves the
    `already_recorded` answer is reachable through the real route."""
    cid = client.test_company_id
    sid = _seed_supplier(client)
    pid = _seed_product(client)
    po_id = _make_po(client, sid, pid, qty=10, cost=4.0)
    r1 = _receive(client, po_id, 3, key='dbl-submit-key-1')
    r2 = _receive(client, po_id, 3, key='dbl-submit-key-1')
    assert r1.status_code == 200 and r2.status_code == 200, (r1.get_json(), r2.get_json())
    assert r1.get_json().get('already_recorded') is None
    assert r2.get_json().get('already_recorded') is True
    assert len(_receipts(po_id)) == 1
    assert _on_hand(cid, pid) == 3, 'one delivery of 3 is 3 on the shelf, not 6'
    assert len(_journals(cid, source_type='po_receipt')) == 1
    assert _ap_ties(cid) and _supplier_balance(client, sid) == 12.0


def test_r1_a_retry_after_a_second_delivery_is_still_already_recorded(client):
    """The key check runs BEFORE the "how much is left" guard: after 3 then 5
    (2 left) a retry of the first 3 must answer already_recorded, not a
    confusing OVER_RECEIPT -- and must add nothing."""
    cid = client.test_company_id
    sid = _seed_supplier(client)
    pid = _seed_product(client)
    po_id = _make_po(client, sid, pid, qty=10, cost=4.0)
    assert _receive(client, po_id, 3, key='first-delivery-k').status_code == 200
    assert _receive(client, po_id, 5, key='second-delivery-k').status_code == 200
    r = _receive(client, po_id, 3, key='first-delivery-k')
    assert r.status_code == 200 and r.get_json().get('already_recorded') is True, r.get_json()
    assert _on_hand(cid, pid) == 8 and len(_receipts(po_id)) == 2


def test_r1_two_different_keys_are_two_deliveries(client):
    cid = client.test_company_id
    sid = _seed_supplier(client)
    pid = _seed_product(client)
    po_id = _make_po(client, sid, pid, qty=10, cost=4.0)
    assert _receive(client, po_id, 3, key='key-number-one').status_code == 200
    assert _receive(client, po_id, 3, key='key-number-two').status_code == 200
    assert len(_receipts(po_id)) == 2 and _on_hand(cid, pid) == 6
    # no key at all: still recorded (no retry dedupe, by design)
    assert _receive(client, po_id, 1).status_code == 200
    assert len(_receipts(po_id)) == 3 and _on_hand(cid, pid) == 7


def test_r1_a_malformed_receipt_key_is_a_400_and_writes_nothing(client):
    cid = client.test_company_id
    sid = _seed_supplier(client)
    pid = _seed_product(client)
    po_id = _make_po(client, sid, pid, qty=10, cost=4.0)
    for bad in ('short', 'has space in it', 'x' * 65, 12345678, ['abcdefgh'], ''):
        r = _receive(client, po_id, 3, key=bad)
        assert r.status_code == 400 and r.get_json().get('code') == 'BAD_RECEIPT_KEY', (bad, r.get_json())
    assert _receipts(po_id) == [] and _on_hand(cid, pid) == 0


def test_r1_completing_receipt_uid_is_the_history_it_completes(client):
    """The route's completing uid is exactly completing_receipt_uid(po, priors):
    the same on any computer that saw the same receipts."""
    sid = _seed_supplier(client)
    pid = _seed_product(client)
    po_id = _make_po(client, sid, pid, qty=10, cost=4.0)
    po = _po(po_id)
    assert _receive(client, po_id, 4, key='partial-first-k').status_code == 200
    first = _receipts(po_id)[0]['uid']
    assert _receive(client, po_id).status_code == 200            # the rest completes it
    rest = _receipts(po_id)[1]['uid']
    assert rest == po_core.completing_receipt_uid(po['uid'], [first])
    assert rest != po_core.completing_receipt_uid(po['uid'], [])
    # whole order in one go on a computer with no history
    po2 = _make_po(client, sid, pid, qty=5, cost=1.0)
    assert _receive(client, po2).status_code == 200
    assert _receipts(po2)[0]['uid'] == po_core.completing_receipt_uid(_po(po2)['uid'], [])


def test_r2_fractional_quantities_can_be_finished_in_decimal(client):
    """Probe C: 0.1 + 0.2 + 0.7 of 1 unit. The float SQL SUM made the remaining
    0.69999... and refused 0.7 as OVER_RECEIPT; the order could never complete."""
    cid = client.test_company_id
    sid = _seed_supplier(client)
    pid = _seed_product(client)
    po_id = _make_po(client, sid, pid, qty=1, cost=3.0)
    codes = [_receive(client, po_id, q, key=f'frac-key-{i}-xx').status_code
             for i, q in enumerate((0.1, 0.2, 0.7))]
    assert codes == [200, 200, 200], codes
    assert _po(po_id)['status'] == 'received'
    assert [r['value'] for r in _receipts(po_id)] == [0.3, 0.6, 2.1]
    assert _supplier_balance(client, sid) == 3.0 and _ap_ties(cid)
    # Deny half: a hair over the true remainder is still refused (no slack).
    po2 = _make_po(client, sid, pid, qty=1, cost=3.0)
    assert _receive(client, po2, 0.3, key='frac2-key-aaaa').status_code == 200
    r = _receive(client, po2, 0.7000001, key='frac2-key-bbbb')
    assert r.status_code == 409 and r.get_json().get('code') == 'OVER_RECEIPT', r.get_json()


def _hand_receipt_without_lines(cid, po_id, line_count):
    conn = get_retail_conn()
    conn.execute("INSERT INTO purchase_order_receipts (company_id,uid,po_id,received_at,value,line_count,legacy) "
                 "VALUES (?,?,?,?,10,?,0)", (cid, str(uuid.uuid4()), po_id, '2026-10-06', line_count))
    conn.commit(); conn.close()


def test_r3_close_short_refuses_while_receipt_lines_or_order_lines_are_parked(client):
    """Probe G: a sibling's receipt header is here, its LINES are not -- the
    order reads 'partial' and Close was offered, releasing debt for goods that
    arrived. Same two guards receive has, plus the order-journal one."""
    cid = client.test_company_id
    sid = _seed_supplier(client)
    pid = _seed_product(client)

    # (a) receipt header present, its line parked
    po_a = _make_po(client, sid, pid, qty=10, cost=4.0)
    assert _receive(client, po_a, 4, key='r3-first-delivery').status_code == 200
    _hand_receipt_without_lines(cid, po_a, 1)
    n_journals = len(_journals(cid))
    r = client.post(f'{API}/purchase-orders/{po_a}/close')
    assert r.status_code == 409 and r.get_json().get('code') == 'PO_RECEIPTS_NOT_ARRIVED', r.get_json()
    assert _po(po_a)['closed_at'] is None and len(_journals(cid)) == n_journals

    # (b) an order line parked (item_count says 2, one row here)
    po_b = _make_po(client, sid, pid, qty=10, cost=4.0)
    assert _receive(client, po_b, 4, key='r3-second-delivr').status_code == 200
    conn = get_retail_conn()
    conn.execute("UPDATE purchase_orders SET item_count=2 WHERE id=?", (po_b,))
    conn.commit(); conn.close()
    r = client.post(f'{API}/purchase-orders/{po_b}/close')
    assert r.status_code == 409 and r.get_json().get('code') == 'PO_LINES_NOT_ARRIVED', r.get_json()
    assert _po(po_b)['closed_at'] is None

    # (c) order basis, its order journal has not arrived
    po_c = _make_order_basis_po(client, cid, sid, pid)
    assert _receive(client, po_c, 1, key='r3-third-delivery').status_code == 200
    conn = get_retail_conn()
    conn.execute("UPDATE purchase_orders SET order_journal_uid=? WHERE id=?", (str(uuid.uuid4()), po_c))
    conn.commit(); conn.close()
    r = client.post(f'{API}/purchase-orders/{po_c}/close')
    assert r.status_code == 409 and r.get_json().get('code') == 'PO_BOOKS_NOT_ARRIVED', r.get_json()

    # Allow half: with nothing parked the close goes through.
    po_d = _make_po(client, sid, pid, qty=10, cost=4.0)
    assert _receive(client, po_d, 4, key='r3-fourth-delivry').status_code == 200
    assert client.post(f'{API}/purchase-orders/{po_d}/close').status_code == 200


def test_r4_a_closed_order_is_paid_against_what_arrived(client):
    """Probe I: 24 of 40 delivered and 24 paid. payment_status read 'partial'
    for ever because 'paid' compared against the full 40."""
    sid = _seed_supplier(client)
    pid = _seed_product(client)
    po_id = _make_po(client, sid, pid, qty=10, cost=4.0)
    assert _receive(client, po_id, 6, key='r4-delivery-key').status_code == 200
    assert client.post(f'{API}/purchase-orders/{po_id}/pay', json={'amount': 24}).status_code == 200
    assert _po(po_id)['payment_status'] == 'partial', 'while open, 24 of a 40 order is partial'
    assert client.post(f'{API}/purchase-orders/{po_id}/close').status_code == 200
    assert _po(po_id)['payment_status'] == 'paid'
    # Deny half: a closed order that is NOT paid up to what arrived is not 'paid'.
    po2 = _make_po(client, sid, pid, qty=10, cost=4.0)
    assert _receive(client, po2, 6, key='r4-delivery-two').status_code == 200
    assert client.post(f'{API}/purchase-orders/{po2}/pay', json={'amount': 10}).status_code == 200
    assert client.post(f'{API}/purchase-orders/{po2}/close').status_code == 200
    assert _po(po2)['payment_status'] == 'partial'
