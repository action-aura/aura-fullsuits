"""Aura Retail -- W4a (retail schema v45): purchase orders shared between
computers, as seen THROUGH THE REAL ROUTES on ONE computer.

The two-computer behaviour is proven elsewhere (retail_po_sync_twodevice_test.py);
this file pins what the route layer must do for that to be possible:

  R1  create queues 1 `purchase_order` + N `purchase_order_item` create events
      with EXACTLY the agreed keys, stamps uids, stores `order_journal_uid`.
  R2  amount_paid is DERIVED from the active payments: create (down payment) +
      pay + void leaves the recomputed figure (a void used to leave it stale).
  R3  receive twice -> second is 409 and ONE movement.
  R4  receive when a sibling's identical receipt already put this stock here
      (same deterministic movement uid) adds NO stock and queues NO duplicate.
  R5  receive/cancel while the order's journal has not arrived -> 409
      PO_BOOKS_NOT_ARRIVED and NOTHING is written.
  R6  a PO payment's sync event names the PO by uid (`po_uid`).
  R7  receive/cancel queue the header `update` event the receiver merges.
  R8  boot backfill of legacy POs: emits once, clears the flag, emits nothing
      the second time.
  R9  the reorder-accept path stamps uids and queues the same events.

Hand-computed figures only (JOD, 3 dp). Self-contained bootstrap, same
convention as retail_stock_books_routes_test.py.

Run (one file per process):
    pytest products/retail/tests/retail_po_sync_routes_test.py -q
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

DATA = Path(tempfile.mkdtemp(prefix="aura_po_sync_routes_"))
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
from api import retail_api  # noqa: E402

API = '/api/sub/retail'

# The payload contract Builder Y's apply side and the coverage ratchet read.
PO_KEYS = {
    'uid', 'po_number', 'supplier_id', 'branch_uid', 'status', 'subtotal', 'tax_total', 'total',
    'notes', 'ordered_at', 'received_at', 'created_at', 'due_date', 'order_journal_uid',
    'split_group_id', 'split_index', 'split_count', 'routing_status', 'routed_channel',
    'routed_at', 'routed_to', 'idempotency_key',
}
ITEM_KEYS = {'uid', 'po_uid', 'product_id', 'quantity', 'unit_cost', 'total', 'received_qty'}


def teardown_module(module):
    shutil.rmtree(DATA, ignore_errors=True)


# ── bootstrap ────────────────────────────────────────────────────────────────

def _login_user(role, company_id):
    email = f'po-{role}-{uuid.uuid4().hex[:8]}@test.local'
    password = 'PoSyncPW1'  # pragma: allowlist secret -- throwaway fixture value
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


@pytest.fixture(autouse=True)
def _no_background_push(monkeypatch):
    """These tests READ the outbox. The real route calls `_sync_nudge()` after
    commit, which starts a background push thread; that thread's ack deletes
    outbox rows, so an event the route DID queue could vanish between the
    request and the assertion (measured: R6/R7 failed intermittently with a
    missing event, never the same one twice). Stubbing the nudge removes the
    race without touching what the routes queue."""
    monkeypatch.setattr(retail_api, '_sync_nudge', lambda: None)


@pytest.fixture
def client():
    return _login_user('admin', str(uuid.uuid4()))


def _seed_product(c, **kw):
    payload = {'name': 'PO Widget', 'sku': f'PO-{uuid.uuid4().hex[:8]}', 'cost_price': 1.0,
               'sell_price': 5.0, 'tax_rate': 0, 'initial_stock': 0}
    payload.update(kw)
    r = c.post(f'{API}/products', json=payload)
    assert r.status_code == 200, r.get_json()
    return r.get_json()['data']['id']


def _seed_supplier(c):
    r = c.post(f'{API}/suppliers', json={'name': f'PO Supplier {uuid.uuid4().hex[:6]}'})
    assert r.status_code == 200, r.get_json()
    return r.get_json()['data']['id']


def _make_po(c, sid, items, paid=0):
    body = {'supplier_id': sid,
            'items': [{'product_id': p, 'quantity': q, 'unit_cost': u} for (p, q, u) in items]}
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


def _events(entity_type, event_type=None, entity_id=None):
    sql = "SELECT * FROM sync_outbox WHERE entity_type=?"
    args = [entity_type]
    if event_type:
        sql += " AND event_type=?"; args.append(event_type)
    if entity_id:
        sql += " AND entity_id=?"; args.append(entity_id)
    rows = _q(sql + " ORDER BY rowid", *args)
    for r in rows:
        r['payload'] = json.loads(r['payload'])
    return rows


def _on_hand(cid, pid):
    return _q("SELECT COALESCE(SUM(quantity_on_hand),0) AS q FROM inventory_balances "
              "WHERE company_id=? AND product_id=?", cid, pid)[0]['q']


# ═════════════════════════════════════════════════════════════════════════════
# R1 -- create queues the PO and its items, with exactly the agreed keys
# ═════════════════════════════════════════════════════════════════════════════

def test_r1_create_queues_po_and_item_events_with_exact_keys(client):
    cid = client.test_company_id
    sid = _seed_supplier(client)
    p1 = _seed_product(client); p2 = _seed_product(client)
    po_id = _make_po(client, sid, [(p1, 10, 4.0), (p2, 5, 2.0)])
    po = _po(po_id)

    assert po['uid'], 'the PO must carry a wire identity'
    evs = _events('purchase_order', 'create', po['uid'])
    assert len(evs) == 1
    assert set(evs[0]['payload']) == PO_KEYS
    pl = evs[0]['payload']
    assert pl['uid'] == po['uid'] and pl['po_number'] == po['po_number']
    assert pl['supplier_id'] == sid and pl['status'] == 'pending'
    assert pl['total'] == 50.0 and pl['subtotal'] == 50.0   # 10x4 + 5x2
    # branch travels by uid, never by this computer's integer.
    assert pl['branch_uid'] == _q("SELECT uid FROM branches WHERE id=?", po['branch_id'])[0]['uid']

    # order_journal_uid is the uid of the journal the order posted.
    jr = _q("SELECT uid FROM gl_journals WHERE company_id=? AND source_type='purchase_order' "
            "AND source_id=?", cid, po_id)
    assert len(jr) == 1
    assert po['order_journal_uid'] == jr[0]['uid'] == pl['order_journal_uid']

    items = _q("SELECT * FROM purchase_order_items WHERE po_id=? ORDER BY id", po_id)
    assert len(items) == 2 and all(i['uid'] for i in items)
    ievs = _events('purchase_order_item', 'create')
    mine = [e for e in ievs if e['payload']['po_uid'] == po['uid']]
    assert len(mine) == 2
    for e in mine:
        assert set(e['payload']) == ITEM_KEYS
    assert {e['entity_id'] for e in mine} == {i['uid'] for i in items}
    by_uid = {e['payload']['uid']: e['payload'] for e in mine}
    for it in items:
        assert by_uid[it['uid']]['product_id'] == it['product_id']
        assert by_uid[it['uid']]['quantity'] == it['quantity']

    # Derived figures are NOT on the wire (every computer re-derives them).
    assert 'amount_paid' not in pl and 'payment_status' not in pl


# ═════════════════════════════════════════════════════════════════════════════
# R2 -- amount_paid is recomputed, a void REDUCES it
# ═════════════════════════════════════════════════════════════════════════════

def test_r2_amount_paid_follows_active_payments_and_void_reduces_it(client):
    sid = _seed_supplier(client)
    pid = _seed_product(client)
    po_id = _make_po(client, sid, [(pid, 10, 10.0)], paid=30)        # total 100.000
    assert (_po(po_id)['amount_paid'], _po(po_id)['payment_status']) == (30.0, 'partial')

    r = client.post(f'{API}/purchase-orders/{po_id}/pay', json={'amount': 20})
    assert r.status_code == 200, r.get_json()
    assert r.get_json()['data']['amount_paid'] == 50.0
    assert (_po(po_id)['amount_paid'], _po(po_id)['payment_status']) == (50.0, 'partial')

    pays = _q("SELECT id, amount FROM payments WHERE related_type='po' AND related_id=? "
              "ORDER BY id", po_id)
    assert [p['amount'] for p in pays] == [30.0, 20.0]

    # Void the 30: the figure must come DOWN to 20 (it used to stay at 50).
    r = client.post(f'{API}/payments/{pays[0]["id"]}/void', json={'reason': 'typo'})
    assert r.status_code == 200, r.get_json()
    po = _po(po_id)
    assert (po['amount_paid'], po['payment_status']) == (20.0, 'partial')

    # Void the rest -> unpaid, 0.
    r = client.post(f'{API}/payments/{pays[1]["id"]}/void', json={'reason': 'typo'})
    assert r.status_code == 200, r.get_json()
    po = _po(po_id)
    assert (po['amount_paid'], po['payment_status']) == (0.0, 'unpaid')


def test_r2b_paying_the_rest_marks_it_paid(client):
    sid = _seed_supplier(client)
    pid = _seed_product(client)
    po_id = _make_po(client, sid, [(pid, 10, 10.0)])
    assert client.post(f'{API}/purchase-orders/{po_id}/pay', json={'amount': 60}).status_code == 200
    r = client.post(f'{API}/purchase-orders/{po_id}/pay', json={'amount': 40})
    assert r.status_code == 200
    assert r.get_json()['data']['payment_status'] == 'paid'
    assert (_po(po_id)['amount_paid'], _po(po_id)['payment_status']) == (100.0, 'paid')
    # The PO header is NOT queued for a payment: the figure is derived.
    po = _po(po_id)
    assert _events('purchase_order', 'update', po['uid']) == []


# ═════════════════════════════════════════════════════════════════════════════
# R3 -- receive twice: one movement, 409
# ═════════════════════════════════════════════════════════════════════════════

def test_r3_receive_twice_is_refused_and_moves_stock_once(client):
    cid = client.test_company_id
    sid = _seed_supplier(client)
    pid = _seed_product(client)
    po_id = _make_po(client, sid, [(pid, 7, 3.0)])
    assert client.post(f'{API}/purchase-orders/{po_id}/receive').status_code == 200
    r = client.post(f'{API}/purchase-orders/{po_id}/receive')
    assert r.status_code == 409
    assert _on_hand(cid, pid) == 7
    moves = _q("SELECT * FROM inventory_movements WHERE product_id=? AND movement_type='purchase_in'", pid)
    assert len(moves) == 1


# ═════════════════════════════════════════════════════════════════════════════
# R4 -- a sibling's identical receipt already put this stock here
# ═════════════════════════════════════════════════════════════════════════════

def test_r4_receive_after_a_siblings_identical_movement_adds_no_second_stock(client):
    from core.retail import purchase_orders as po_core   # lazy: G's module
    cid = client.test_company_id
    sid = _seed_supplier(client)
    pid = _seed_product(client)
    po_id = _make_po(client, sid, [(pid, 10, 4.0)])
    po = _po(po_id)
    item = _q("SELECT * FROM purchase_order_items WHERE po_id=?", po_id)[0]
    mv_uid = po_core.receipt_movement_uid(po['uid'], item['uid'])

    # Simulate the sync apply of the OTHER computer's receipt: its movement row
    # (same deterministic uid) and the +10 it put on the balance.
    conn = get_retail_conn()
    conn.execute("INSERT INTO inventory_movements (company_id,product_id,branch_id,movement_type,"
                 "quantity,unit_cost,reference,created_by,uid) VALUES (?,?,?,'purchase_in',10,4,?,?,?)",
                 (cid, pid, po['branch_id'], po['po_number'], 'sibling', mv_uid))
    conn.execute("INSERT OR IGNORE INTO inventory_balances (company_id,product_id,branch_id,quantity_on_hand) "
                 "VALUES (?,?,?,0)", (cid, pid, po['branch_id']))
    conn.execute("UPDATE inventory_balances SET quantity_on_hand=quantity_on_hand+10 "
                 "WHERE company_id=? AND product_id=? AND branch_id=?", (cid, pid, po['branch_id']))
    conn.commit(); conn.close()
    before_events = len(_events('inventory_movement'))

    r = client.post(f'{API}/purchase-orders/{po_id}/receive')
    assert r.status_code == 200, r.get_json()

    assert _on_hand(cid, pid) == 10, 'the sibling already put these 10 here; a second add would make 20'
    assert len(_q("SELECT 1 FROM inventory_movements WHERE uid=?", mv_uid)) == 1
    assert len([m for m in _q("SELECT * FROM inventory_movements WHERE product_id=? "
                              "AND movement_type='purchase_in'", pid)]) == 1
    assert len(_events('inventory_movement')) == before_events, 'no duplicate movement event is queued'
    assert _po(po_id)['status'] == 'received'
    assert _q("SELECT received_qty FROM purchase_order_items WHERE po_id=?", po_id)[0]['received_qty'] == 10


def test_r4b_a_normal_receive_uses_the_deterministic_movement_uid_and_queues_it(client):
    from core.retail import purchase_orders as po_core
    cid = client.test_company_id
    sid = _seed_supplier(client)
    pid = _seed_product(client)
    po_id = _make_po(client, sid, [(pid, 10, 4.0)])
    po = _po(po_id)
    item = _q("SELECT * FROM purchase_order_items WHERE po_id=?", po_id)[0]
    assert client.post(f'{API}/purchase-orders/{po_id}/receive').status_code == 200
    mv_uid = po_core.receipt_movement_uid(po['uid'], item['uid'])
    assert len(_q("SELECT 1 FROM inventory_movements WHERE uid=?", mv_uid)) == 1
    assert len(_events('inventory_movement', 'create', mv_uid)) == 1
    assert _on_hand(cid, pid) == 10


# ═════════════════════════════════════════════════════════════════════════════
# R5 -- the order's books have not arrived: refuse, write nothing
# ═════════════════════════════════════════════════════════════════════════════

def _strand_order_journal(po_id):
    """Make the PO name an order journal this computer does not hold (what a
    sibling's PO looks like before its journal has synced in)."""
    conn = get_retail_conn()
    conn.execute("UPDATE purchase_orders SET order_journal_uid=? WHERE id=?", (str(uuid.uuid4()), po_id))
    conn.commit(); conn.close()


def _snapshot(po_id, pid):
    return (_q("SELECT status FROM purchase_orders WHERE id=?", po_id)[0]['status'],
            len(_q("SELECT 1 FROM inventory_movements WHERE product_id=?", pid)),
            len(_q("SELECT 1 FROM gl_journals")),
            len(_q("SELECT 1 FROM sync_outbox")))


def test_r5_receive_and_cancel_refuse_while_the_order_journal_has_not_arrived(client):
    cid = client.test_company_id
    sid = _seed_supplier(client)
    pid = _seed_product(client)
    po_id = _make_po(client, sid, [(pid, 10, 4.0)])
    _strand_order_journal(po_id)
    before = _snapshot(po_id, pid)

    r = client.post(f'{API}/purchase-orders/{po_id}/receive')
    assert r.status_code == 409, r.get_json()
    assert r.get_json()['code'] == 'PO_BOOKS_NOT_ARRIVED'
    assert _snapshot(po_id, pid) == before and _on_hand(cid, pid) == 0

    r = client.post(f'{API}/purchase-orders/{po_id}/cancel')
    assert r.status_code == 409, r.get_json()
    assert r.get_json()['code'] == 'PO_BOOKS_NOT_ARRIVED'
    assert _snapshot(po_id, pid) == before

    # ALLOW half: once the journal the order names is here, both go through.
    jr = _q("SELECT uid FROM gl_journals WHERE company_id=? AND source_type='purchase_order' "
            "AND source_id=?", cid, po_id)[0]['uid']
    conn = get_retail_conn()
    conn.execute("UPDATE purchase_orders SET order_journal_uid=? WHERE id=?", (jr, po_id))
    conn.commit(); conn.close()
    assert client.post(f'{API}/purchase-orders/{po_id}/receive').status_code == 200


def test_r5b_a_legacy_po_with_no_order_journal_uid_is_not_blocked(client):
    sid = _seed_supplier(client)
    pid = _seed_product(client)
    po_id = _make_po(client, sid, [(pid, 3, 2.0)])
    conn = get_retail_conn()
    conn.execute("UPDATE purchase_orders SET order_journal_uid=NULL WHERE id=?", (po_id,))
    conn.commit(); conn.close()
    assert client.post(f'{API}/purchase-orders/{po_id}/cancel').status_code == 200


# ═════════════════════════════════════════════════════════════════════════════
# R6 -- a PO payment names its PO by uid
# ═════════════════════════════════════════════════════════════════════════════

def test_r6_po_payment_event_carries_po_uid_other_payments_carry_none(client):
    sid = _seed_supplier(client)
    pid = _seed_product(client)
    po_id = _make_po(client, sid, [(pid, 10, 10.0)], paid=30)
    po = _po(po_id)
    assert client.post(f'{API}/purchase-orders/{po_id}/pay', json={'amount': 20}).status_code == 200
    pays = [e for e in _events('payment', 'create') if e['payload'].get('related_type') == 'po'
            and e['payload'].get('related_id') == po_id]
    assert len(pays) == 2
    assert all(e['payload']['po_uid'] == po['uid'] for e in pays)

    # A plain supplier payment is not a PO payment: the key is there, empty.
    r = client.post(f'{API}/suppliers/{sid}/payments', json={'amount': 1})
    if r.status_code == 200:
        plain = [e for e in _events('payment', 'create') if e['payload'].get('related_type') is None
                 and e['payload'].get('party_id') == sid]
        assert plain and all('po_uid' in e['payload'] and e['payload']['po_uid'] is None for e in plain)


# ═════════════════════════════════════════════════════════════════════════════
# R7 -- header updates the receiver merges
# ═════════════════════════════════════════════════════════════════════════════

def test_r7_receive_and_cancel_queue_the_header_update(client):
    sid = _seed_supplier(client)
    pid = _seed_product(client)
    a = _make_po(client, sid, [(pid, 4, 2.0)])
    b = _make_po(client, sid, [(pid, 4, 2.0)])
    assert client.post(f'{API}/purchase-orders/{a}/receive').status_code == 200
    assert client.post(f'{API}/purchase-orders/{b}/cancel').status_code == 200

    ev = _events('purchase_order', 'update', _po(a)['uid'])
    assert len(ev) == 1
    assert set(ev[0]['payload']) == {'uid', 'status', 'received_at'}
    assert ev[0]['payload']['status'] == 'received' and ev[0]['payload']['received_at']

    ev = _events('purchase_order', 'update', _po(b)['uid'])
    assert len(ev) == 1
    assert ev[0]['payload'] == {'uid': _po(b)['uid'], 'status': 'cancelled', 'received_at': None}


# ═════════════════════════════════════════════════════════════════════════════
# R8 -- boot backfill of legacy POs
# ═════════════════════════════════════════════════════════════════════════════

def _make_legacy(po_id):
    """Shape a PO the way v45's migration leaves a pre-existing one: uid there,
    pending flag set, nothing ever queued."""
    conn = get_retail_conn()
    po = conn.execute("SELECT uid FROM purchase_orders WHERE id=?", (po_id,)).fetchone()
    item_uids = [r[0] for r in conn.execute("SELECT uid FROM purchase_order_items WHERE po_id=?", (po_id,))]
    marks = ','.join('?' * (1 + len(item_uids)))
    conn.execute(f"DELETE FROM sync_outbox WHERE entity_id IN ({marks})", (po['uid'], *item_uids))
    conn.execute("DELETE FROM sync_outbox WHERE entity_type='payment' AND payload LIKE ?",
                 (f'%"related_id": {po_id},%',))
    conn.execute("UPDATE purchase_orders SET sync_backfill_pending=1 WHERE id=?", (po_id,))
    conn.commit(); conn.close()


def test_r8_boot_backfill_emits_once_and_clears_the_flag(client):
    sid = _seed_supplier(client)
    pid = _seed_product(client)
    other_sid = _seed_supplier(client)
    po_id = _make_po(client, sid, [(pid, 10, 10.0)], paid=30)
    untouched = _make_po(client, sid, [(pid, 1, 1.0)])      # never legacy: must emit nothing more
    po = _po(po_id)
    pay_uid = _q("SELECT uid FROM payments WHERE related_type='po' AND related_id=?", po_id)[0]['uid']
    # A payment with the same integer related_id but ANOTHER supplier must not be linked.
    conn = get_retail_conn()
    conn.execute("INSERT INTO payments (company_id,reference,party_type,party_id,direction,amount,currency,"
                 "method,related_type,related_id,status,uid) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                 (client.test_company_id, f'X-{uuid.uuid4().hex[:6]}', 'supplier', other_sid, 'out', 5, 'JOD',
                  'cash', 'po', po_id, 'active', str(uuid.uuid4())))
    conn.commit(); conn.close()
    _make_legacy(po_id)
    n_untouched = len(_events('purchase_order', 'create', _po(untouched)['uid']))

    conn = get_retail_conn()
    try:
        retail_api.backfill_legacy_purchase_orders(conn)
        conn.commit()
    finally:
        conn.close()

    assert len(_events('purchase_order', 'create', po['uid'])) == 1
    items = _q("SELECT uid FROM purchase_order_items WHERE po_id=?", po_id)
    assert len([e for e in _events('purchase_order_item', 'create')
                if e['payload']['po_uid'] == po['uid']]) == len(items) == 1
    assert _events('purchase_order', 'update', po['uid']) == []         # still pending
    links = [e for e in _events('payment', 'po_link') if e['payload']['po_uid'] == po['uid']]
    assert [e['payload'] for e in links] == [{'uid': pay_uid, 'po_uid': po['uid']}]
    assert _po(po_id)['sync_backfill_pending'] == 0
    assert len(_events('purchase_order', 'create', _po(untouched)['uid'])) == n_untouched

    total = len(_q("SELECT 1 FROM sync_outbox"))
    conn = get_retail_conn()
    try:
        retail_api.backfill_legacy_purchase_orders(conn)
        conn.commit()
    finally:
        conn.close()
    assert len(_q("SELECT 1 FROM sync_outbox")) == total, 'second boot must emit nothing'


def test_r8b_backfill_of_a_received_po_also_queues_its_status(client):
    sid = _seed_supplier(client)
    pid = _seed_product(client)
    po_id = _make_po(client, sid, [(pid, 2, 5.0)])
    assert client.post(f'{API}/purchase-orders/{po_id}/receive').status_code == 200
    po = _po(po_id)
    _make_legacy(po_id)
    conn = get_retail_conn()
    try:
        retail_api.backfill_legacy_purchase_orders(conn)
        conn.commit()
    finally:
        conn.close()
    assert len(_events('purchase_order', 'create', po['uid'])) == 1
    upd = _events('purchase_order', 'update', po['uid'])
    assert len(upd) == 1 and upd[0]['payload']['status'] == 'received'


# ═════════════════════════════════════════════════════════════════════════════
# R9 -- the reorder-accept path
# ═════════════════════════════════════════════════════════════════════════════

def test_r9_reorder_accept_stamps_uids_and_queues_the_same_events(client):
    cid = client.test_company_id
    sid = _seed_supplier(client)
    pid = _seed_product(client, cost_price=2.0, reorder_level=5, supplier_id=sid)
    rid = str(uuid.uuid4())
    conn = get_retail_conn()
    conn.execute("INSERT INTO reorder_requests (id,company_id,branch_id,product_id,status) "
                 "VALUES (?,?,?,?, 'pending')", (rid, cid, None, pid))
    conn.commit(); conn.close()
    r = client.post(f'{API}/reorder-requests/{rid}/accept', json={})
    assert r.status_code == 200, r.get_json()
    po = _po(r.get_json()['data']['purchase_order_id'])
    assert po['uid'] and po['order_journal_uid']
    assert (po['amount_paid'], po['payment_status']) == (0.0, 'unpaid')
    evs = _events('purchase_order', 'create', po['uid'])
    assert len(evs) == 1 and set(evs[0]['payload']) == PO_KEYS
    assert evs[0]['payload']['order_journal_uid'] == po['order_journal_uid']
    items = _q("SELECT uid FROM purchase_order_items WHERE po_id=?", po['id'])
    assert len(items) == 1 and items[0]['uid']
    iev = _events('purchase_order_item', 'create', items[0]['uid'])
    assert len(iev) == 1 and set(iev[0]['payload']) == ITEM_KEYS
