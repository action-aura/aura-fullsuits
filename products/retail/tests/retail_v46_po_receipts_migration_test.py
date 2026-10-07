"""Aura Retail -- schema v46 migration: partial receipts and supplier debt at
receipt (W4a-2; claimed in ROADMAP.md "retail schema v46 CLAIMED").

THE RULE BEING PINNED. v46 adds `purchase_orders.ap_basis` / `closed_at` /
`cancelled_at` and the two receipt tables. Every order that exists at upgrade is
'order' basis (history is never re-booked); an order that was already received
gets ONE synthesised LEGACY receipt whose uids are pure functions of uids it
already has (so two computers upgrading independently make identical rows, which
is why nothing is queued); a cancelled order's cancellation time becomes the
set-once `cancelled_at`.

The v45 shape is manufactured the way retail_v45_po_sync_migration_test.py does
it: a real freshly built install, the v46 objects dropped, `PRAGMA user_version
= 45` stamped; the upgrade then runs through the REAL `ensure_schema_version`
chain (backup + integrity check before and after).

WHAT EACH TEST GUARDS (and the mutation that must turn it RED):
  * legacy receipt: a uuid4 uid instead of the core function's (a replay, or a
    second computer, would mint a second receipt and the stock would count twice
    in the derived received quantities).
  * replay: dropping the NOT EXISTS gate re-inserts a legacy receipt next to a
    real one on every launch.
  * cancelled_at: backfilling every row, not only those still NULL.

Run:
    pytest products/retail/tests/retail_v46_po_receipts_migration_test.py -q
"""
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

CID = 46
_TMP_DIRS = []


def teardown_module(module):
    for d in _TMP_DIRS:
        shutil.rmtree(d, ignore_errors=True)
    _TMP_DIRS.clear()


def _install():
    tmp = tempfile.mkdtemp(prefix='aura-retail-v46-')
    _TMP_DIRS.append(tmp)
    os.environ.update({'AURA_APP_DATA': tmp, 'AURA_STANDALONE': '1', 'AURA_SITE_RELAY_ENABLED': '0'})
    import database.schema as sch
    sch.BASE_DIR = os.path.join(tmp, 'database')
    sch.SUBSYS_DIR = os.path.join(sch.BASE_DIR, 'subsystems')
    sch.init_retail()
    return sch, os.path.join(sch.SUBSYS_DIR, 'retail.db')


def _open(db):
    conn = sqlite3.connect(db, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def _rows(db, sql, params=()):
    conn = _open(db)
    try:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()


def _cols(db, table):
    return {r['name'] for r in _rows(db, f'PRAGMA table_info("{table}")')}


def _make_v45(db):
    """A v45-shaped database for company CID holding: `done` (received: two
    lines, 5 and 3 received), `open` (pending, nothing received), `dead`
    (cancelled), `partial_old` (one line of two received, status still pending --
    the shape a v45 sibling could never produce but a fixture can). Returns
    {'done': {...}, ...} with po id/uid and item (id, uid) lists."""
    conn = _open(db)
    try:
        conn.execute('DROP TABLE IF EXISTS purchase_order_receipt_lines')
        conn.execute('DROP TABLE IF EXISTS purchase_order_receipts')
        for c in ('ap_basis', 'closed_at', 'cancelled_at'):
            if c in {r[1] for r in conn.execute('PRAGMA table_info(purchase_orders)').fetchall()}:
                conn.execute(f'ALTER TABLE purchase_orders DROP COLUMN {c}')
        out = {}
        spec = (
            ('done', 'received', '2026-09-30 12:00:00', ((5, 5), (3, 3))),
            ('open', 'pending', None, ((4, 0), (2, 0))),
            ('dead', 'cancelled', None, ((1, 0),)),
            ('partial_old', 'pending', None, ((6, 2), (2, 0))),
        )
        for key, status, received_at, lines in spec:
            po_uid = str(uuid.uuid4())
            cur = conn.execute(
                "INSERT INTO purchase_orders (company_id, po_number, branch_id, status, total, uid, "
                "received_at, created_at) VALUES (?,?,1,?,?,?,?, '2026-09-01 08:00:00')",
                (CID, 'PO-V46-' + key, status, 20.0, po_uid, received_at))
            items = []
            for qty, got in lines:
                i_uid = str(uuid.uuid4())
                icur = conn.execute(
                    "INSERT INTO purchase_order_items (po_id, product_id, quantity, unit_cost, total, "
                    "received_qty, uid) VALUES (?,?,?,2,?,?,?)",
                    (cur.lastrowid, 'prod-' + i_uid[:6], qty, qty * 2, got, i_uid))
                items.append((icur.lastrowid, i_uid, got))
            out[key] = {'id': cur.lastrowid, 'uid': po_uid, 'items': items}
        conn.execute('PRAGMA user_version = 45')
        conn.commit()
    finally:
        conn.close()
    return out


def _migrate(sch, db):
    from commercial_runtime.security.migration_safety import ensure_schema_version
    backup_dir = tempfile.mkdtemp(prefix='aura-retail-v46-backup-')
    _TMP_DIRS.append(backup_dir)
    conn = _open(db)
    try:
        ensure_schema_version(conn, db, sch.RETAIL_SCHEMA_VERSION, sch._migrate_retail_schema, backup_dir)
    finally:
        conn.close()
    return backup_dir


def _po(db, number):
    return _rows(db, "SELECT * FROM purchase_orders WHERE po_number=?", ('PO-V46-' + number,))[0]


def _dump(db):
    return (_rows(db, "SELECT * FROM purchase_order_receipts ORDER BY id"),
            _rows(db, "SELECT * FROM purchase_order_receipt_lines ORDER BY id"))


def test_upgrade_adds_shape_basis_and_one_legacy_receipt_with_exact_uids():
    from core.retail import purchase_orders as po_core
    sch, db = _install()
    pos = _make_v45(db)
    backup_dir = _migrate(sch, db)

    assert sch.RETAIL_SCHEMA_VERSION == 49  # v49 UPDATE: head moved 48 -> 49 (B6b); v49's own shape is pinned by retail_v49_total_spent_baseline_migration_test.py
    assert _rows(db, 'PRAGMA user_version')[0]['user_version'] == 49  # v49 UPDATE: head moved 48 -> 49 (B6b); v49's own shape is pinned by retail_v49_total_spent_baseline_migration_test.py
    assert _rows(db, 'PRAGMA integrity_check')[0]['integrity_check'] == 'ok'
    assert os.listdir(backup_dir), 'ensure_schema_version must have written its pre-migration backup'
    assert {'ap_basis', 'closed_at', 'cancelled_at'} <= _cols(db, 'purchase_orders')
    assert {'id', 'company_id', 'uid', 'po_id', 'branch_id', 'received_at', 'value', 'line_count', 'legacy',
            'created_by', 'actor_user_uid', 'terminal_id', 'created_at_utc'} == _cols(db, 'purchase_order_receipts')
    assert {'id', 'receipt_id', 'uid', 'po_item_id', 'quantity', 'movement_uid'} == \
        _cols(db, 'purchase_order_receipt_lines')

    assert all(_po(db, k)['ap_basis'] == 'order' for k in pos), 'history is never re-booked'
    assert all(_po(db, k)['closed_at'] is None for k in pos)

    receipts = _rows(db, "SELECT * FROM purchase_order_receipts ORDER BY po_id")
    assert {r['po_id'] for r in receipts} == {pos['done']['id'], pos['partial_old']['id']}, \
        'a legacy receipt only where something was received; none for pending or cancelled'
    done = next(r for r in receipts if r['po_id'] == pos['done']['id'])
    r_uid = po_core.legacy_receipt_uid(pos['done']['uid'])
    assert (done['uid'], done['legacy'], done['value'], done['company_id'], done['line_count']) == \
        (r_uid, 1, 20.0, CID, 2)
    assert done['received_at'] == '2026-09-30 12:00:00'
    lines = _rows(db, "SELECT * FROM purchase_order_receipt_lines WHERE receipt_id=? ORDER BY id", (done['id'],))
    assert [(l['po_item_id'], l['quantity'], l['uid'], l['movement_uid']) for l in lines] == [
        (iid, float(got), po_core.receipt_line_uid(r_uid, iuid),
         po_core.receipt_movement_uid(pos['done']['uid'], iuid))
        for iid, iuid, got in pos['done']['items']]

    part = next(r for r in receipts if r['po_id'] == pos['partial_old']['id'])
    assert part['line_count'] == 1 and part['received_at'] == '2026-09-01 08:00:00', \
        'received_at falls back to the order creation time; only the received line is carried'


def test_cancelled_at_is_backfilled_from_the_order_time_only_where_null():
    sch, db = _install()
    _make_v45(db)
    _migrate(sch, db)
    assert _po(db, 'dead')['cancelled_at'] == '2026-09-01 08:00:00'
    assert _po(db, 'open')['cancelled_at'] is None
    assert _po(db, 'done')['cancelled_at'] is None
    conn = _open(db)
    try:
        conn.execute("UPDATE purchase_orders SET cancelled_at='2026-10-02 00:00:00' WHERE po_number='PO-V46-dead'")
        conn.commit()
        sch._migrate_retail_schema(conn)
        conn.commit()
    finally:
        conn.close()
    assert _po(db, 'dead')['cancelled_at'] == '2026-10-02 00:00:00', 'a replay never rewrites a set cancelled_at'


def test_replaying_the_chain_is_byte_identical_and_adds_no_second_legacy_receipt():
    """The mutation this must catch: without the NOT EXISTS gate a replay inserts
    a legacy receipt next to a REAL one (and, with a uuid4 uid, next to the
    legacy one) on every launch."""
    sch, db = _install()
    pos = _make_v45(db)
    _migrate(sch, db)
    before = _dump(db)
    po_before = _rows(db, "SELECT * FROM purchase_orders ORDER BY id")

    conn = _open(db)
    try:
        # a REAL partial receipt on a post-upgrade order whose lines carry received_qty
        from core.retail import purchase_orders as po_core
        po_uid = str(uuid.uuid4())
        cur = conn.execute(
            "INSERT INTO purchase_orders (company_id, po_number, uid, ap_basis, total) "
            "VALUES (?, 'PO-V46-new', ?, 'receipt', 10)", (CID, po_uid))
        i_uid = str(uuid.uuid4())
        icur = conn.execute(
            "INSERT INTO purchase_order_items (po_id, product_id, quantity, unit_cost, total, received_qty, uid) "
            "VALUES (?, 'p', 10, 1, 10, 4, ?)", (cur.lastrowid, i_uid))
        r_uid = po_core.keyed_receipt_uid(po_uid, 'migrate-key-0001')
        rcur = conn.execute(
            "INSERT INTO purchase_order_receipts (company_id, uid, po_id, value, line_count, legacy) "
            "VALUES (?,?,?,4,1,0)", (CID, r_uid, cur.lastrowid))
        conn.execute(
            "INSERT INTO purchase_order_receipt_lines (receipt_id, uid, po_item_id, quantity) VALUES (?,?,?,4)",
            (rcur.lastrowid, po_core.receipt_line_uid(r_uid, i_uid), icur.lastrowid))
        conn.commit()
        sch._migrate_retail_schema(conn)
        sch._migrate_retail_schema(conn)
        conn.commit()
    finally:
        conn.close()

    after = _dump(db)
    assert after[0][:len(before[0])] == before[0] and after[1][:len(before[1])] == before[1]
    assert len(after[0]) == len(before[0]) + 1 and len(after[1]) == len(before[1]) + 1, \
        'only the one real receipt was added; no legacy receipt was synthesised beside it'
    for row in po_before:
        now = next(r for r in _rows(db, "SELECT * FROM purchase_orders WHERE id=?", (row['id'],)))
        assert now == row, 'a replay changes no order row'
    assert pos


def test_a_fresh_install_has_the_v46_shape_and_no_receipts():
    sch, db = _install()
    assert _rows(db, 'PRAGMA user_version')[0]['user_version'] == sch.RETAIL_SCHEMA_VERSION == 49  # v49 UPDATE: head moved 48 -> 49 (B6b); v49's own shape is pinned by retail_v49_total_spent_baseline_migration_test.py
    assert {'ap_basis', 'closed_at', 'cancelled_at'} <= _cols(db, 'purchase_orders')
    assert _rows(db, "SELECT COUNT(*) AS n FROM purchase_order_receipts")[0]['n'] == 0
    names = {r['name'] for r in _rows(db, "SELECT name FROM sqlite_master WHERE type='index'")}
    assert {'idx_po_receipts_uid', 'idx_po_receipts_po', 'idx_po_receipt_lines_uid',
            'idx_po_receipt_lines_receipt', 'idx_po_receipt_lines_item'} <= names


def test_receipt_uids_are_unique_by_index():
    sch, db = _install()
    conn = _open(db)
    try:
        conn.execute("INSERT INTO purchase_order_receipts (company_id, uid, po_id) VALUES (1,'dup',1)")
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO purchase_order_receipts (company_id, uid, po_id) VALUES (1,'dup',2)")
        conn.execute("INSERT INTO purchase_order_receipt_lines (receipt_id, uid, po_item_id, quantity) "
                     "VALUES (1,'ldup',1,1)")
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO purchase_order_receipt_lines (receipt_id, uid, po_item_id, quantity) "
                         "VALUES (1,'ldup',2,1)")
    finally:
        conn.close()


def test_schema_derives_legacy_uids_from_the_core_module_not_a_copy():
    """The migration must import the namespace-owning functions, never carry a
    second copy of the literal that could drift from the one the receive route
    and the sync apply use."""
    src = (BACKEND_DIR / 'database' / 'schema.py').read_text(encoding='utf-8')
    body = src[src.index('def _migrate_add_po_receipts'):src.index('def _migrate_add_purchase_order_sync')]
    assert 'a674e3be-c149-497a-b0a7-f235e22c5ae0' not in body
    assert '_po.legacy_receipt_uid' in body and '_po.receipt_line_uid' in body and '_po.receipt_movement_uid' in body
