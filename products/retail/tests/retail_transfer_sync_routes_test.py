"""Aura Retail -- W3b (retail schema v47): stock transfers shared between
computers, as seen THROUGH THE REAL ROUTES on ONE computer.

The two-computer behaviour is proven elsewhere (retail_transfer_sync_twodevice_test.py);
this file pins what the route layer must do for that to be possible:

  X1  create queues 1 `stock_transfer` + N `stock_transfer_item` create events
      with EXACTLY the agreed keys, and stamps item_count.
  X2  send: the movement uid is the deterministic one, a header update and one
      item update per line are queued with exactly the agreed keys.
  X3  receive: deterministic `transfer_in` uid; a sibling's identical movement
      already here -> stock moves ONCE (200, nothing doubled, no second event).
  X4  receive queues the header update + one item update per line.
  X5  each 409 guard (LINES / SEND / BOOKS _NOT_ARRIVED) refuses and writes
      NOTHING, on receive and on recall (and LINES on send).
  X6  cancel / recall stamp cancelled_at / recalled_at and queue the header update.
  X7  boot backfill of legacy transfers: emits once, clears the flag, never
      queues a movement, never sends a pending line's cost.
  X8  send when a sibling's identical `transfer_out` is already here moves the
      stock once and takes the sibling's cost.

Self-contained bootstrap, same convention as retail_po_sync_routes_test.py.

Run (one file per process):
    pytest products/retail/tests/retail_transfer_sync_routes_test.py -q
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

DATA = Path(tempfile.mkdtemp(prefix="aura_xfer_sync_routes_"))
(DATA / "database" / "subsystems").mkdir(parents=True, exist_ok=True)
os.environ.update(AURA_STANDALONE="1", AURA_BUNDLE_DIR=str(BACKEND_DIR), AURA_APP_DATA=str(DATA))
os.environ.pop("AURA_DEV", None)
# This shell exports a relay / licensing URL; a test process must not talk to them.
os.environ.pop("AURA_SYNC_RELAY_URL", None)
os.environ.pop("AURA_OWNER_LICENSING_URL", None)

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
from core.retail import stock_transfers as xfer  # noqa: E402
from api import retail_api  # noqa: E402

API = '/api/sub/retail'

# The payload contract Builder Y's apply side and the coverage ratchet read.
HEADER_CREATE_KEYS = {'id', 'source_branch_uid', 'destination_branch_uid', 'created_by',
                      'created_at', 'item_count', 'pre_v47'}
HEADER_UPDATE_KEYS = {'id', 'status', 'sent_at', 'received_at', 'cancelled_at', 'recalled_at'}
ITEM_CREATE_KEYS = {'id', 'transfer_id', 'product_id', 'quantity_sent'}
ITEM_UPDATE_KEYS = {'id', 'unit_cost', 'quantity_received'}


def teardown_module(module):
    shutil.rmtree(DATA, ignore_errors=True)


# ── bootstrap ────────────────────────────────────────────────────────────────

def _login_user(role, company_id):
    email = f'xs-{role}-{uuid.uuid4().hex[:8]}@test.local'
    password = 'XferSyncPW1'  # pragma: allowlist secret -- throwaway fixture value
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
    """These tests READ the outbox; the real route's `_sync_nudge()` starts a
    background push whose ack deletes outbox rows (see retail_po_sync_routes_test)."""
    monkeypatch.setattr(retail_api, '_sync_nudge', lambda: None)


def _q(sql, *args):
    conn = get_retail_conn()
    try:
        return [dict(r) for r in conn.execute(sql, args).fetchall()]
    finally:
        conn.close()


def _x(sql, *args):
    conn = get_retail_conn()
    try:
        conn.execute(sql, args)
        conn.commit()
    finally:
        conn.close()


@pytest.fixture
def shop():
    """(client, cid, source_bid, dest_bid, product_id): two branches, one
    product at cost 5 stocked 100 at the SOURCE only."""
    cid = str(uuid.uuid4())
    client = _login_user('admin', cid)
    client.get(f'{API}/settings/tax')   # warms the lazy schema
    conn = get_retail_conn()
    conn.execute("INSERT INTO branches (company_id,name) VALUES (?,'Warehouse')", (cid,))
    conn.execute("INSERT INTO branches (company_id,name) VALUES (?,'Storefront')", (cid,))
    rows = conn.execute("SELECT id FROM branches WHERE company_id=? ORDER BY id", (cid,)).fetchall()
    src, dst = rows[0]['id'], rows[1]['id']
    pid = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO products (id,company_id,sku,name,cost_price,sell_price,tax_rate,status) "
        "VALUES (?,?,?,?,?,?,?,'active')",
        (pid, cid, f'XS-{uuid.uuid4().hex[:6]}', 'Sync Widget', 5, 10, 0))
    conn.execute("INSERT INTO inventory_balances (company_id,product_id,branch_id,quantity_on_hand) "
                 "VALUES (?,?,?,100)", (cid, pid, src))
    conn.commit(); conn.close()
    return client, cid, src, dst, pid


def _create(c, src, dst, pid, qty=10):
    r = c.post(f'{API}/stock-transfers', json={
        'source_branch_id': src, 'destination_branch_id': dst,
        'items': [{'product_id': pid, 'quantity': qty}]})
    assert r.status_code == 200, r.get_json()
    return r.get_json()['data']['id']


def _send(c, tid):
    return c.post(f'{API}/stock-transfers/{tid}/send')


def _receive(c, tid, item_id, qty):
    return c.post(f'{API}/stock-transfers/{tid}/receive',
                  json={'items': [{'id': item_id, 'quantity_received': qty}]})


def _item_id(tid):
    return _q("SELECT id FROM stock_transfer_items WHERE transfer_id=? ORDER BY id", tid)[0]['id']


def _on_hand(cid, pid, bid):
    rows = _q("SELECT quantity_on_hand FROM inventory_balances WHERE company_id=? AND product_id=? "
              "AND branch_id=?", cid, pid, bid)
    return float(rows[0]['quantity_on_hand']) if rows else 0.0


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


def _outbox_count():
    return _q("SELECT COUNT(*) AS n FROM sync_outbox")[0]['n']


def _snapshot(cid, tid):
    """Everything a refused request must leave untouched."""
    return {
        'transfer': _q("SELECT * FROM stock_transfers WHERE id=?", tid),
        'items': _q("SELECT * FROM stock_transfer_items WHERE transfer_id=? ORDER BY id", tid),
        'balances': _q("SELECT * FROM inventory_balances WHERE company_id=? ORDER BY branch_id, product_id", cid),
        'movements': _q("SELECT COUNT(*) AS n FROM inventory_movements WHERE company_id=?", cid),
        'journals': _q("SELECT COUNT(*) AS n FROM gl_journals WHERE company_id=?", cid),
        'outbox': _outbox_count(),
    }


# ═════════════════════════════════════════════════════════════════════════════
# X1 -- create queues the header and every line, with exactly the agreed keys
# ═════════════════════════════════════════════════════════════════════════════

def test_x1_create_queues_header_and_item_events_with_exact_keys(shop):
    c, cid, src, dst, pid = shop
    tid = _create(c, src, dst, pid, 10)

    t = _q("SELECT * FROM stock_transfers WHERE id=?", tid)[0]
    assert t['item_count'] == 1, 'create must stamp how many lines the transfer has'

    hdr = _events('stock_transfer', 'create', tid)
    assert len(hdr) == 1
    assert set(hdr[0]['payload']) == HEADER_CREATE_KEYS
    pl = hdr[0]['payload']
    assert pl['id'] == tid and pl['item_count'] == 1
    assert pl['pre_v47'] == 0, 'a transfer written by v47 code is never flagged pre-v47'
    # branches travel by uid, never by this computer's integer
    assert pl['source_branch_uid'] == _q("SELECT uid FROM branches WHERE id=?", src)[0]['uid']
    assert pl['destination_branch_uid'] == _q("SELECT uid FROM branches WHERE id=?", dst)[0]['uid']

    iid = _item_id(tid)
    its = _events('stock_transfer_item', 'create', iid)
    assert len(its) == 1
    assert set(its[0]['payload']) == ITEM_CREATE_KEYS
    assert its[0]['payload']['transfer_id'] == tid
    assert its[0]['payload']['product_id'] == pid and its[0]['payload']['quantity_sent'] == 10
    # creating moves no stock and queues no movement
    assert _on_hand(cid, pid, src) == 100.0


# ═════════════════════════════════════════════════════════════════════════════
# X2 -- send: deterministic uid + header update + item update
# ═════════════════════════════════════════════════════════════════════════════

def test_x2_send_uses_deterministic_out_uid_and_queues_updates(shop):
    c, cid, src, dst, pid = shop
    tid = _create(c, src, dst, pid, 10)
    iid = _item_id(tid)
    assert _send(c, tid).status_code == 200

    mv = _q("SELECT uid FROM inventory_movements WHERE company_id=? AND movement_type='transfer_out' "
            "AND reference=?", cid, f'XFER-{tid}')
    assert [m['uid'] for m in mv] == [xfer.out_movement_uid(tid, iid)]

    h = _events('stock_transfer', 'update', tid)
    assert len(h) == 1 and set(h[0]['payload']) == HEADER_UPDATE_KEYS
    assert h[0]['payload']['status'] == 'in_transit' and h[0]['payload']['sent_at']
    assert h[0]['payload']['received_at'] is None and h[0]['payload']['recalled_at'] is None

    i = _events('stock_transfer_item', 'update', iid)
    assert len(i) == 1 and set(i[0]['payload']) == ITEM_UPDATE_KEYS
    assert i[0]['payload']['unit_cost'] == 5.0 and i[0]['payload']['quantity_received'] is None


# ═════════════════════════════════════════════════════════════════════════════
# X3 -- receive when a sibling's identical movement is already here
# ═════════════════════════════════════════════════════════════════════════════

def test_x3_receive_with_sibling_movement_already_here_moves_stock_once(shop):
    c, cid, src, dst, pid = shop
    tid = _create(c, src, dst, pid, 10)
    iid = _item_id(tid)
    assert _send(c, tid).status_code == 200

    # What the sync apply of a sibling's identical receipt leaves behind: the
    # movement under the deterministic uid AND the quantity it added.
    sib_uid = xfer.in_movement_uid(tid, iid, 10)
    _x("INSERT INTO inventory_movements (company_id,product_id,branch_id,movement_type,quantity,"
       "unit_cost,reference,created_by,uid) VALUES (?,?,?,'transfer_in',10,5,?, 'sib', ?)",
       cid, pid, dst, f'XFER-{tid}', sib_uid)
    _x("INSERT OR IGNORE INTO inventory_balances (company_id,product_id,branch_id,quantity_on_hand) "
       "VALUES (?,?,?,0)", cid, pid, dst)
    _x("UPDATE inventory_balances SET quantity_on_hand=quantity_on_hand+10 "
       "WHERE company_id=? AND product_id=? AND branch_id=?", cid, pid, dst)
    in_events_before = len([e for e in _events('inventory_movement', 'create')
                            if e['payload']['movement_type'] == 'transfer_in'])

    r = _receive(c, tid, iid, 10)
    assert r.status_code == 200, r.get_json()
    assert _on_hand(cid, pid, dst) == 10.0, 'the same receipt twice must add the stock once'
    n_in = _q("SELECT COUNT(*) AS n FROM inventory_movements WHERE company_id=? "
              "AND movement_type='transfer_in'", cid)[0]['n']
    assert n_in == 1
    in_events_after = len([e for e in _events('inventory_movement', 'create')
                           if e['payload']['movement_type'] == 'transfer_in'])
    assert in_events_after == in_events_before, 'a movement that was not written must not be announced'
    assert _q("SELECT status FROM stock_transfers WHERE id=?", tid)[0]['status'] == 'received'


# ═════════════════════════════════════════════════════════════════════════════
# X4 -- receive: deterministic uid, header + item updates
# ═════════════════════════════════════════════════════════════════════════════

def test_x4_receive_queues_header_and_item_updates(shop):
    c, cid, src, dst, pid = shop
    tid = _create(c, src, dst, pid, 10)
    iid = _item_id(tid)
    _send(c, tid)
    assert _receive(c, tid, iid, 8).status_code == 200      # a shortage of 2

    ins = _q("SELECT uid, quantity FROM inventory_movements WHERE company_id=? "
             "AND movement_type='transfer_in'", cid)
    assert [(m['uid'], m['quantity']) for m in ins] == [(xfer.in_movement_uid(tid, iid, 8), 8.0)]
    assert _on_hand(cid, pid, dst) == 8.0

    h = _events('stock_transfer', 'update', tid)
    assert len(h) == 2                                     # send, then receive
    assert set(h[1]['payload']) == HEADER_UPDATE_KEYS
    assert h[1]['payload']['status'] == 'received' and h[1]['payload']['received_at']
    i = _events('stock_transfer_item', 'update', iid)
    assert len(i) == 2 and set(i[1]['payload']) == ITEM_UPDATE_KEYS
    assert i[1]['payload']['quantity_received'] == 8.0 and i[1]['payload']['unit_cost'] == 5.0


# ═════════════════════════════════════════════════════════════════════════════
# X5 -- the three 409 guards refuse and write NOTHING
# ═════════════════════════════════════════════════════════════════════════════

def _sent(shop):
    c, cid, src, dst, pid = shop
    tid = _create(c, src, dst, pid, 10)
    iid = _item_id(tid)
    assert _send(c, tid).status_code == 200
    return c, cid, src, dst, pid, tid, iid


def _act(c, tid, iid, action):
    if action == 'receive':
        return _receive(c, tid, iid, 10)
    return c.post(f'{API}/stock-transfers/{tid}/{action}')


@pytest.mark.parametrize('action', ['receive', 'recall'])
def test_x5_lines_not_arrived_refuses_and_writes_nothing(shop, action):
    c, cid, src, dst, pid, tid, iid = _sent(shop)
    _x("UPDATE stock_transfers SET item_count=2 WHERE id=?", tid)       # one line has not synced
    before = _snapshot(cid, tid)
    r = _act(c, tid, iid, action)
    assert r.status_code == 409 and r.get_json()['code'] == 'TRANSFER_LINES_NOT_ARRIVED', r.get_json()
    assert _snapshot(cid, tid) == before


def test_x5_send_lines_not_arrived_refuses_and_writes_nothing(shop):
    c, cid, src, dst, pid = shop
    tid = _create(c, src, dst, pid, 10)
    _x("UPDATE stock_transfers SET item_count=2 WHERE id=?", tid)
    before = _snapshot(cid, tid)
    r = _send(c, tid)
    assert r.status_code == 409 and r.get_json()['code'] == 'TRANSFER_LINES_NOT_ARRIVED', r.get_json()
    assert _snapshot(cid, tid) == before


@pytest.mark.parametrize('action', ['receive', 'recall'])
def test_x5_send_not_arrived_refuses_and_writes_nothing(shop, action):
    """in_transit here (the header update arrived) but the line's cost has not."""
    c, cid, src, dst, pid = shop
    tid = _create(c, src, dst, pid, 10)
    iid = _item_id(tid)
    _x("UPDATE stock_transfers SET status='in_transit', sent_at='2026-10-01 10:00:00' WHERE id=?", tid)
    _x("UPDATE stock_transfer_items SET unit_cost=NULL WHERE id=?", iid)
    before = _snapshot(cid, tid)
    r = _act(c, tid, iid, action)
    assert r.status_code == 409 and r.get_json()['code'] == 'TRANSFER_SEND_NOT_ARRIVED', r.get_json()
    assert _snapshot(cid, tid) == before


@pytest.mark.parametrize('action', ['receive', 'recall'])
def test_x5_books_not_arrived_refuses_and_writes_nothing(shop, action):
    """in_transit with a real cost but no send journal on this computer."""
    c, cid, src, dst, pid = shop
    tid = _create(c, src, dst, pid, 10)
    iid = _item_id(tid)
    _x("UPDATE stock_transfers SET status='in_transit', sent_at='2026-10-01 10:00:00' WHERE id=?", tid)
    _x("UPDATE stock_transfer_items SET unit_cost=5 WHERE id=?", iid)
    assert gl.send_journal(get_retail_conn(), cid, tid) is None
    before = _snapshot(cid, tid)
    r = _act(c, tid, iid, action)
    assert r.status_code == 409 and r.get_json()['code'] == 'TRANSFER_BOOKS_NOT_ARRIVED', r.get_json()
    assert _snapshot(cid, tid) == before


def test_x5_zero_cost_transfer_is_not_blocked_by_the_books_guard(shop):
    """The allow-half: a transfer worth nothing has no send journal by design,
    so 'no journal' must not be read as 'journal has not arrived'."""
    c, cid, src, dst, pid = shop
    tid = _create(c, src, dst, pid, 10)
    iid = _item_id(tid)
    _x("UPDATE products SET cost_price=0, avg_cost=0 WHERE id=?", pid)
    assert _send(c, tid).status_code == 200
    assert gl.send_journal(get_retail_conn(), cid, tid) is None
    assert _receive(c, tid, iid, 10).status_code == 200


# ═════════════════════════════════════════════════════════════════════════════
# X9 -- the receive JOURNAL uid names what was received (W3b fix, D6)
# ═════════════════════════════════════════════════════════════════════════════

def _receive_journal_uids(cid):
    return [r['uid'] for r in _q(
        "SELECT uid FROM gl_journals WHERE company_id=? AND source_type='stock_transfer' "
        "AND uid LIKE '%' ORDER BY id", cid)]


def test_x9_receive_journal_uid_is_keyed_by_line_and_received_quantity(shop):
    """Two computers receiving one transfer OFFLINE with different counts must
    post different journal uids (so both post, on every computer); identical
    counts must give the same uid (so they collapse).

    W3b verify F1/F2 UPDATE: the send journal is now keyed by the line costs
    (`send_key`) and the receive journal is ONE PER LINE (`receive_line_key`),
    not one per receipt. What this can no longer catch: a per-RECEIPT key (the
    old `receipt_key`) -- the two-computer overlap probe and
    retail_transfer_sync_core_test's two-line test now guard that shape."""
    c, cid, src, dst, pid, tid, iid = _sent(shop)
    assert _receive(c, tid, iid, 7).status_code == 200
    sent = gl.send_journal(get_retail_conn(), cid, tid)
    [cost_row] = _q("SELECT unit_cost FROM stock_transfer_items WHERE id=?", iid)
    assert sent is not None and sent['uid'] == gl.stock_journal_uid(
        xfer.send_key(tid, [(iid, cost_row['unit_cost'])]), 'transfer_send'), \
        'the send journal is keyed by the line cost'
    uids = [u for u in _receive_journal_uids(cid) if u != sent['uid']]
    assert uids == [gl.stock_journal_uid(xfer.receive_line_key(tid, iid, 7), 'transfer_receive_line')]
    assert gl.stock_journal_uid(tid, 'transfer_receive') not in uids, 'the legacy plain-tid uid'
    # what the OTHER computer would key for its own count: differs for a
    # different quantity, equal for the identical one (written two ways).
    assert (gl.stock_journal_uid(xfer.receive_line_key(tid, iid, 6), 'transfer_receive_line') != uids[0])
    assert (gl.stock_journal_uid(xfer.receive_line_key(tid, iid, 7.0), 'transfer_receive_line') == uids[0])


# ═════════════════════════════════════════════════════════════════════════════
# X6 -- cancel / recall stamps and queued header updates
# ═════════════════════════════════════════════════════════════════════════════

def test_x6_cancel_stamps_cancelled_at_and_queues_header_update(shop):
    c, cid, src, dst, pid = shop
    tid = _create(c, src, dst, pid, 10)
    assert c.post(f'{API}/stock-transfers/{tid}/cancel').status_code == 200
    t = _q("SELECT * FROM stock_transfers WHERE id=?", tid)[0]
    assert t['status'] == 'cancelled' and t['cancelled_at'] and t['recalled_at'] is None
    h = _events('stock_transfer', 'update', tid)
    assert len(h) == 1 and set(h[0]['payload']) == HEADER_UPDATE_KEYS
    assert h[0]['payload']['status'] == 'cancelled' and h[0]['payload']['cancelled_at']
    assert h[0]['payload']['recalled_at'] is None
    # it ranks as cancelled-BEFORE-send: recalled_at stays empty
    assert xfer.transfer_rank(t['status'], t['recalled_at']) == xfer.RANK_CANCELLED_BEFORE_SEND


def test_x6_recall_stamps_recalled_at_uses_deterministic_uid_and_queues_update(shop):
    c, cid, src, dst, pid, tid, iid = _sent(shop)
    assert c.post(f'{API}/stock-transfers/{tid}/recall').status_code == 200
    t = _q("SELECT * FROM stock_transfers WHERE id=?", tid)[0]
    assert t['status'] == 'cancelled' and t['cancelled_at'] and t['recalled_at']
    assert xfer.transfer_rank(t['status'], t['recalled_at']) == xfer.RANK_RECALLED
    back = _q("SELECT uid FROM inventory_movements WHERE company_id=? AND movement_type='transfer_in' "
              "AND reference=?", cid, f'XFER-{tid}')
    assert [m['uid'] for m in back] == [xfer.recall_movement_uid(tid, iid)]
    assert _on_hand(cid, pid, src) == 100.0
    h = _events('stock_transfer', 'update', tid)
    assert h[-1]['payload']['status'] == 'cancelled' and h[-1]['payload']['recalled_at']
    assert set(h[-1]['payload']) == HEADER_UPDATE_KEYS


def test_x6_recall_keeps_an_existing_cancelled_at(shop):
    """W3b verify F5: a transfer that is in transit but already carries a
    `cancelled_at` (a cancel from another computer that lost to the send --
    `transfer_sent_after_cancel`) keeps that earlier stamp when recalled; both
    stamps are set-once. Mutation: put `cancelled_at=?` back in the recall's
    UPDATE and the earlier instant is overwritten."""
    c, cid, src, dst, pid, tid, iid = _sent(shop)
    earlier = '2026-01-01 00:00:00'
    _x("UPDATE stock_transfers SET cancelled_at=? WHERE id=?", earlier, tid)
    assert c.post(f'{API}/stock-transfers/{tid}/recall').status_code == 200
    t = _q("SELECT * FROM stock_transfers WHERE id=?", tid)[0]
    assert t['cancelled_at'] == earlier, 'a recall must not overwrite an existing cancelled_at'
    assert t['recalled_at'] and t['recalled_at'] != earlier
    # the allow half: with no earlier stamp the recall still sets both
    c2 = _sent(shop)
    assert c2[0].post(f'{API}/stock-transfers/{c2[5]}/recall').status_code == 200
    t2 = _q("SELECT * FROM stock_transfers WHERE id=?", c2[5])[0]
    assert t2['cancelled_at'] and t2['recalled_at']


def test_x6_a_send_posts_its_journal_keyed_by_the_line_costs(shop):
    """W3b verify F1b: the send journal's uid names the line costs, so a second
    computer's send of the same transfer at a different cost is a DIFFERENT
    journal (it does not collide and vanish). Mutation: drop `costs_key=` from the
    route's post_transfer_send call and the uid reverts to the bare transfer id."""
    c, cid, src, dst, pid, tid, iid = _sent(shop)
    [cost_row] = _q("SELECT unit_cost FROM stock_transfer_items WHERE id=?", iid)
    key = xfer.send_key(tid, [(iid, cost_row['unit_cost'])])
    [j] = _q("SELECT uid FROM gl_journals WHERE company_id=? AND source_type='stock_transfer' "
             "AND source_uid=?", cid, tid)
    assert j['uid'] == gl.stock_journal_uid(key, 'transfer_send')
    assert j['uid'] != gl.stock_journal_uid(tid, 'transfer_send')


class _FakeSync:
    """The least a sync service needs for the post-pull wrappers: an
    `apply_pull_result(conn, result)` that does nothing (the events are already
    'applied' by the test)."""
    def __init__(self):
        self.calls = 0

    def apply_pull_result(self, conn, result, *a, **k):
        self.calls += 1
        return {'applied': 0}


def test_x6_the_post_pull_reconcile_reverses_the_dearer_duplicate_send(shop):
    """W3b verify F1c: the installed post-pull step reverses a send journal that is not
    the one keyed by the converged line costs. A sibling's DEARER send journal has
    'arrived' (posted here under its own cost key); the line holds the smaller cost.
    Mutations: skip the install (nothing reversed), or drop the `arrived` gate (a pull
    with no events must not walk the books)."""
    c, cid, src, dst, pid, tid, iid = _sent(shop)
    [line] = _q("SELECT unit_cost, quantity_sent FROM stock_transfer_items WHERE id=?", iid)
    dearer = float(line['unit_cost']) * 2 + 1
    conn = get_retail_conn()
    try:
        gl.post_transfer_send(conn, cid, src, tid, [(pid, line['quantity_sent'], dearer)], 'JOD',
                              costs_key=xfer.send_key(tid, [(iid, dearer)]))
        conn.commit()
    finally:
        conn.close()
    def reversals():      # scoped to THIS transfer: earlier tests in the file recall other transfers
        return _q("SELECT r.uid, o.uid AS orig FROM gl_journals r JOIN gl_journals o "
                  "ON o.id=r.reversed_journal_id WHERE r.source_type='reversal' AND o.source_uid=?", tid)
    assert reversals() == []

    svc = _FakeSync()
    retail_api.install_transfer_send_reconcile(svc)
    retail_api.install_transfer_send_reconcile(svc)            # installing twice is a no-op
    conn = get_retail_conn()
    try:
        svc.apply_pull_result(conn, {'events': []})
        conn.commit()
        assert reversals() == [], 'a pull that brought no events must not run the reconcile'
        svc.apply_pull_result(conn, {'events': [{'seq': 1}]})
        conn.commit()
    finally:
        conn.close()
    assert svc.calls == 2, 'the wrapped original ran exactly once per pull'
    winner = gl.send_journal(get_retail_conn(), cid, tid)
    assert winner['uid'] == gl.stock_journal_uid(xfer.send_key(tid, [(iid, line['unit_cost'])]), 'transfer_send')
    revs = reversals()
    assert len(revs) == 1 and revs[0]['orig'] != winner['uid'], revs      # the winner is never reversed
    # deny half: a second pull posts nothing more
    conn = get_retail_conn()
    try:
        svc.apply_pull_result(conn, {'events': [{'seq': 2}]})
        conn.commit()
    finally:
        conn.close()
    assert len(reversals()) == 1


def test_x6_recall_when_sibling_recall_movement_already_here_moves_stock_once(shop):
    c, cid, src, dst, pid, tid, iid = _sent(shop)
    # the sibling's identical recall already put the 10 back here
    _x("INSERT INTO inventory_movements (company_id,product_id,branch_id,movement_type,quantity,"
       "unit_cost,reference,created_by,uid) VALUES (?,?,?,'transfer_in',10,5,?, 'sib', ?)",
       cid, pid, src, f'XFER-{tid}', xfer.recall_movement_uid(tid, iid))
    _x("UPDATE inventory_balances SET quantity_on_hand=quantity_on_hand+10 "
       "WHERE company_id=? AND product_id=? AND branch_id=?", cid, pid, src)
    assert c.post(f'{API}/stock-transfers/{tid}/recall').status_code == 200
    assert _on_hand(cid, pid, src) == 100.0, 'the same recall twice must put the stock back once'


# ═════════════════════════════════════════════════════════════════════════════
# X8 -- send when the sibling's identical transfer_out is already here
# ═════════════════════════════════════════════════════════════════════════════

def test_x8_send_with_sibling_out_movement_already_here_moves_stock_once(shop):
    c, cid, src, dst, pid = shop
    tid = _create(c, src, dst, pid, 10)
    iid = _item_id(tid)
    _x("INSERT INTO inventory_movements (company_id,product_id,branch_id,movement_type,quantity,"
       "unit_cost,reference,created_by,uid) VALUES (?,?,?,'transfer_out',-10,4,?, 'sib', ?)",
       cid, pid, src, f'XFER-{tid}', xfer.out_movement_uid(tid, iid))
    _x("UPDATE inventory_balances SET quantity_on_hand=quantity_on_hand-10 "
       "WHERE company_id=? AND product_id=? AND branch_id=?", cid, pid, src)
    r = _send(c, tid)
    assert r.status_code == 200, r.get_json()
    assert _on_hand(cid, pid, src) == 90.0, 'the same send twice must take the stock once'
    # the line takes the SIBLING's cost, so both computers agree on it
    assert _q("SELECT unit_cost FROM stock_transfer_items WHERE id=?", iid)[0]['unit_cost'] == 4.0


def test_x8_send_is_not_refused_for_stock_the_sibling_already_took(shop):
    """The allow-half of the oversell guard: the sibling's send emptied the shelf
    (balance 0 after its movement was applied here). Refusing 'only 0 on hand'
    would strand a transfer that was in fact sent in full."""
    c, cid, src, dst, pid = shop
    _x("UPDATE inventory_balances SET quantity_on_hand=10 WHERE company_id=? AND product_id=? "
       "AND branch_id=?", cid, pid, src)
    tid = _create(c, src, dst, pid, 10)
    iid = _item_id(tid)
    _x("INSERT INTO inventory_movements (company_id,product_id,branch_id,movement_type,quantity,"
       "unit_cost,reference,created_by,uid) VALUES (?,?,?,'transfer_out',-10,4,?, 'sib', ?)",
       cid, pid, src, f'XFER-{tid}', xfer.out_movement_uid(tid, iid))
    _x("UPDATE inventory_balances SET quantity_on_hand=0 WHERE company_id=? AND product_id=? "
       "AND branch_id=?", cid, pid, src)
    r = _send(c, tid)
    assert r.status_code == 200, r.get_json()
    assert _on_hand(cid, pid, src) == 0.0, 'must not go negative: the sibling already took it'
    # ... and the deny-half still holds when NO sibling movement is here.
    c2tid = _create(c, src, dst, pid, 10)
    assert _send(c, c2tid).status_code == 400


# ═════════════════════════════════════════════════════════════════════════════
# X7 -- boot backfill of legacy transfers
# ═════════════════════════════════════════════════════════════════════════════

def _legacy(cid, src, dst, pid, status, *, sent=False, qty_received=None):
    """A transfer row exactly as the v47 migration leaves one: flagged pending."""
    tid = str(uuid.uuid4()); iid = str(uuid.uuid4())
    _x("INSERT INTO stock_transfers (id,company_id,source_branch_id,destination_branch_id,status,"
       "created_by,sent_at,received_at,sync_backfill_pending,item_count,pre_v47) VALUES (?,?,?,?,?,?,?,?,1,1,1)",
       tid, cid, src, dst, status, 'u1', '2026-09-30 09:00:00' if sent else None,
       '2026-09-30 12:00:00' if status == 'received' else None)
    _x("INSERT INTO stock_transfer_items (id,transfer_id,product_id,quantity_sent,unit_cost,quantity_received) "
       "VALUES (?,?,?,?,?,?)", iid, tid, pid, 10, 7 if sent else 0, qty_received)
    return tid, iid


def test_x7_backfill_emits_once_clears_flag_and_never_queues_a_movement(shop):
    c, cid, src, dst, pid = shop
    pend_t, pend_i = _legacy(cid, src, dst, pid, 'pending')
    recv_t, recv_i = _legacy(cid, src, dst, pid, 'received', sent=True, qty_received=9)
    movements_before = len(_events('inventory_movement'))

    conn = get_retail_conn()
    try:
        n = retail_api.backfill_legacy_transfers(conn)
        n_again = retail_api.backfill_legacy_transfers(conn)
    finally:
        conn.close()
    assert n >= 2 and n_again == 0
    assert _q("SELECT COUNT(*) AS n FROM stock_transfers WHERE company_id=? AND sync_backfill_pending=1",
              cid)[0]['n'] == 0

    for tid in (pend_t, recv_t):
        assert len(_events('stock_transfer', 'create', tid)) == 1
        assert set(_events('stock_transfer', 'create', tid)[0]['payload']) == HEADER_CREATE_KEYS
        # W3b fix (F3): a migrated row is pre-v47 and the payload must say so.
        assert _events('stock_transfer', 'create', tid)[0]['payload']['pre_v47'] == 1
        assert len(_events('stock_transfer', 'update', tid)) == 1
        assert set(_events('stock_transfer', 'update', tid)[0]['payload']) == HEADER_UPDATE_KEYS
    for iid in (pend_i, recv_i):
        assert len(_events('stock_transfer_item', 'create', iid)) == 1

    # the received one carries its facts ...
    up = _events('stock_transfer', 'update', recv_t)[0]['payload']
    assert up['status'] == 'received' and up['sent_at'] and up['received_at']
    iu = _events('stock_transfer_item', 'update', recv_i)
    assert len(iu) == 1 and iu[0]['payload']['unit_cost'] == 7.0 and iu[0]['payload']['quantity_received'] == 9.0
    # ... but a pending line never sends its (meaningless) default cost: a 0 would
    # be set-once on the receivers and then clash with the real cost at send.
    assert _events('stock_transfer_item', 'update', pend_i) == []
    # movements already synced under their old random uids; never re-announced.
    assert len(_events('inventory_movement')) == movements_before


def test_x7_backfill_rolls_back_just_the_transfer_that_fails(shop, monkeypatch):
    c, cid, src, dst, pid = shop
    bad_t, _ = _legacy(cid, src, dst, pid, 'pending')
    good_t, _ = _legacy(cid, src, dst, pid, 'pending')
    real = retail_api._xfer_queue_header_update

    def boom(cur, cid_, transfer_id, *a, **kw):
        if transfer_id == bad_t:
            raise RuntimeError('boom')
        return real(cur, cid_, transfer_id, *a, **kw)

    monkeypatch.setattr(retail_api, '_xfer_queue_header_update', boom)
    conn = get_retail_conn()
    try:
        retail_api.backfill_legacy_transfers(conn)
    finally:
        conn.close()
    flags = {r['id']: r['sync_backfill_pending'] for r in
             _q("SELECT id, sync_backfill_pending FROM stock_transfers WHERE id IN (?,?)", bad_t, good_t)}
    assert flags == {bad_t: 1, good_t: 0}, 'the failed one stays flagged for the next boot'
    assert _events('stock_transfer', 'create', bad_t) == [], 'its half-written events were rolled back'
    assert len(_events('stock_transfer', 'create', good_t)) == 1
