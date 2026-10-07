"""Aura Retail -- schema v45 migration: purchase orders gain a wire identity
(W4a; claimed in ROADMAP.md "retail schema v45 CLAIMED").

THE RULE BEING PINNED. A purchase order used to be a per-computer integer row.
v45 gives every order and every order line a `uid` so two computers can name
the same order, plus `order_journal_uid` (which books journal the order posted,
so a sibling can reverse/receive it without a local integer id) and
`sync_backfill_pending` (a legacy order that must announce itself to the
outbox once, at boot). Rows that existed before the upgrade are stamped
`sync_backfill_pending=1`; rows written by v45 code are stamped by the writer
and stay 0.

The v44 shape is manufactured the way retail_v44_party_balance_migration_test.py
does it: a real freshly built install, the v45 objects dropped, and
`PRAGMA user_version = 44` stamped; the upgrade then runs through the REAL
`ensure_schema_version` chain (backup + integrity check before and after).

WHAT EACH TEST GUARDS (and the mutation that must turn it RED):
  * uid backfill + idempotency: backfilling ALL rows instead of only
    `uid IS NULL` ones rewrites every uid on a replay.
  * pending only on legacy rows: stamping pending on every row.
  * order_journal_uid: not found for a PO with no journal (stays NULL).

Run:
    pytest products/retail/tests/retail_v45_po_sync_migration_test.py -q
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

CID = 45
_TMP_DIRS = []


def teardown_module(module):
    for d in _TMP_DIRS:
        shutil.rmtree(d, ignore_errors=True)
    _TMP_DIRS.clear()


def _install():
    tmp = tempfile.mkdtemp(prefix='aura-retail-v45-')
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


def _make_v44(db, lazy_columns_present=False):
    """A v44-shaped database for company CID holding two LEGACY purchase orders
    (with two lines each): `po_with_journal` has an order journal posted the
    way the old code posted it (source_type='purchase_order', source_id=po id),
    `po_no_journal` has none. Returns {'with': id, 'without': id, 'journal_uid': ...}.

    `lazy_columns_present` models a database whose `amount_paid`/`payment_status`/
    `due_date` were already added lazily by retail_api._ensure_credit_schema --
    the usual state of any install that has ever served a request."""
    import api.retail_api as ra
    from core.retail import general_ledger as gl
    conn = _open(db)
    try:
        # drop the v45 objects (index first: SQLite refuses to drop an indexed column)
        conn.execute('DROP INDEX IF EXISTS idx_purchase_orders_uid')
        conn.execute('DROP INDEX IF EXISTS idx_purchase_order_items_uid')
        conn.execute('DROP INDEX IF EXISTS idx_poi_po')
        for table, cols in (('purchase_orders', ('uid', 'order_journal_uid', 'sync_backfill_pending',
                                                  'item_count')),
                            ('purchase_order_items', ('uid',))):
            have = {r[1] for r in conn.execute(f'PRAGMA table_info("{table}")').fetchall()}
            for c in cols:
                if c in have:
                    conn.execute(f'ALTER TABLE {table} DROP COLUMN {c}')
        have = {r[1] for r in conn.execute('PRAGMA table_info(purchase_orders)').fetchall()}
        for c in ('amount_paid', 'payment_status', 'due_date'):
            if c in have:
                conn.execute(f'ALTER TABLE purchase_orders DROP COLUMN {c}')
        if lazy_columns_present:
            previous = ra._CREDIT_SCHEMA_READY
            ra._CREDIT_SCHEMA_READY = False
            try:
                ra._ensure_credit_schema(conn)
            finally:
                ra._CREDIT_SCHEMA_READY = previous
        ids = {}
        for key, number in (('with', 'PO-V45-1'), ('without', 'PO-V45-2')):
            cur = conn.execute(
                "INSERT INTO purchase_orders (company_id, po_number, branch_id, status, total) "
                "VALUES (?,?,1,'pending',10)", (CID, number))
            ids[key] = cur.lastrowid
            pid = str(uuid.uuid4())
            conn.execute("INSERT OR IGNORE INTO products (id, company_id, name, sku, status) "
                         "VALUES (?,?,?,?, 'active')", (pid, CID, 'W', uuid.uuid4().hex[:8]))
            for _ in range(2):
                conn.execute("INSERT INTO purchase_order_items (po_id, product_id, quantity, unit_cost, total) "
                             "VALUES (?,?,1,5,5)", (cur.lastrowid, pid))
        gl.ensure_default_chart(conn, CID)
        jid = gl.post_purchase_order(conn, CID, 1, ids['with'], 'PO-V45-1', 'JOD', 0.0, 10.0)
        ids['journal_uid'] = conn.execute("SELECT uid FROM gl_journals WHERE id=?", (jid,)).fetchone()['uid']
        conn.execute('PRAGMA user_version = 44')
        conn.commit()
    finally:
        conn.close()
    return ids


def _migrate(sch, db):
    from commercial_runtime.security.migration_safety import ensure_schema_version
    backup_dir = tempfile.mkdtemp(prefix='aura-retail-v45-backup-')
    _TMP_DIRS.append(backup_dir)
    conn = _open(db)
    try:
        ensure_schema_version(conn, db, sch.RETAIL_SCHEMA_VERSION, sch._migrate_retail_schema, backup_dir)
    finally:
        conn.close()
    return backup_dir


def _po_rows(db):
    return {r['po_number']: r for r in _rows(db, "SELECT * FROM purchase_orders WHERE company_id=?", (CID,))}


@pytest.mark.parametrize('lazy', [False, True], ids=['columns-absent', 'lazy-columns-already-there'])
def test_upgrade_adds_columns_stamps_legacy_rows_and_keeps_integrity(lazy):
    sch, db = _install()
    ids = _make_v44(db, lazy_columns_present=lazy)
    backup_dir = _migrate(sch, db)

    # v46 UPDATE: head moved to 46 (W4a-2 partial receipts); this file still
    # proves v45's own columns/backfill through the real chain, and can no
    # longer catch the head drifting past 46 (that tripwire lives with
    # retail_v46_po_receipts_migration_test.py).
    assert sch.RETAIL_SCHEMA_VERSION == 50  # v50 UPDATE: head moved 49 -> 50 (W2b/W3a); v50's own shape is pinned by retail_v50_config_sync_migration_test.py
    assert _rows(db, 'PRAGMA user_version')[0]['user_version'] == 50  # v50 UPDATE: head moved 49 -> 50 (W2b/W3a); v50's own shape is pinned by retail_v50_config_sync_migration_test.py
    assert _rows(db, 'PRAGMA integrity_check')[0]['integrity_check'] == 'ok'
    assert os.listdir(backup_dir), 'ensure_schema_version must have written its pre-migration backup'

    assert {'amount_paid', 'payment_status', 'due_date', 'uid', 'order_journal_uid',
            'sync_backfill_pending'} <= _cols(db, 'purchase_orders')
    assert 'uid' in _cols(db, 'purchase_order_items')

    pos = _po_rows(db)
    uids = [p['uid'] for p in pos.values()]
    assert all(uids) and len(set(uids)) == len(uids)
    assert all(p['sync_backfill_pending'] == 1 for p in pos.values()), 'legacy rows must announce themselves once'
    assert all((p['amount_paid'] or 0) == 0 and p['payment_status'] == 'unpaid' for p in pos.values())

    items = _rows(db, "SELECT uid FROM purchase_order_items")
    assert len(items) == 4 and all(i['uid'] for i in items) and len({i['uid'] for i in items}) == 4

    # W4a verify F1: every legacy order gets the count of its (all-local) lines
    assert 'item_count' in _cols(db, 'purchase_orders')
    assert all(p['item_count'] == 2 for p in pos.values()), [p['item_count'] for p in pos.values()]

    # the order journal is found for the PO that had one and ONLY that one
    assert pos['PO-V45-1']['order_journal_uid'] == ids['journal_uid']
    assert pos['PO-V45-2']['order_journal_uid'] is None


def test_uid_indexes_are_unique_and_partial():
    sch, db = _install()
    _make_v44(db)
    _migrate(sch, db)
    conn = _open(db)
    try:
        for table in ('purchase_orders', 'purchase_order_items'):
            shape = sch._uid_index_shape(conn, table, f'idx_{table}_uid')
            assert sch._uid_index_is_correct(shape), (table, shape)
        assert conn.execute("SELECT 1 FROM sqlite_master WHERE name='idx_poi_po'").fetchone()
        # partial: two NULL uids may coexist, two equal non-NULL uids may not
        conn.execute("INSERT INTO purchase_orders (company_id, po_number) VALUES (?, 'N1')", (CID,))
        conn.execute("INSERT INTO purchase_orders (company_id, po_number) VALUES (?, 'N2')", (CID,))
        conn.execute("UPDATE purchase_orders SET uid='dup' WHERE po_number='N1'")
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("UPDATE purchase_orders SET uid='dup' WHERE po_number='N2'")
    finally:
        conn.close()


def test_replaying_the_chain_changes_nothing_and_a_new_row_is_not_pending():
    """The mutation this must catch: backfilling every row instead of only
    `uid IS NULL` ones reissues every uid (and re-arms every pending flag) on
    each launch -- a sibling would then see a different identity each boot."""
    sch, db = _install()
    _make_v44(db)
    _migrate(sch, db)
    before = _po_rows(db)
    before_items = _rows(db, "SELECT id, uid FROM purchase_order_items ORDER BY id")

    # A row written AFTER the upgrade (the writer stamps its own uid; pending stays 0),
    # and a legacy flag already cleared by the boot backfill must survive a replay.
    conn = _open(db)
    try:
        conn.execute("INSERT INTO purchase_orders (company_id, po_number, uid) VALUES (?, 'PO-NEW', ?)",
                     (CID, str(uuid.uuid4())))
        conn.execute("UPDATE purchase_orders SET sync_backfill_pending=0 WHERE po_number='PO-V45-1'")
        conn.commit()
        sch._migrate_retail_schema(conn)
        sch._migrate_retail_schema(conn)
        conn.commit()
    finally:
        conn.close()

    after = _po_rows(db)
    for number, row in before.items():
        assert after[number]['uid'] == row['uid'], 'a replay must never reissue a uid'
        assert after[number]['order_journal_uid'] == row['order_journal_uid']
    assert after['PO-V45-1']['sync_backfill_pending'] == 0, 'a cleared flag must not be re-armed by a replay'
    assert after['PO-V45-2']['sync_backfill_pending'] == 1
    assert after['PO-NEW']['sync_backfill_pending'] == 0, 'only rows that existed at upgrade are pending'
    assert _rows(db, "SELECT id, uid FROM purchase_order_items ORDER BY id")[:len(before_items)] == before_items


def test_a_fresh_install_has_the_v45_shape_and_no_pending_rows():
    sch, db = _install()
    # v46 UPDATE: head is 46 now (see the note above); the v45 shape check stays.
    assert _rows(db, 'PRAGMA user_version')[0]['user_version'] == sch.RETAIL_SCHEMA_VERSION == 50  # v50 UPDATE: head moved 49 -> 50 (W2b/W3a); v50's own shape is pinned by retail_v50_config_sync_migration_test.py
    assert {'uid', 'order_journal_uid', 'sync_backfill_pending'} <= _cols(db, 'purchase_orders')
    assert 'uid' in _cols(db, 'purchase_order_items')
    assert _rows(db, "SELECT COUNT(*) AS n FROM purchase_orders WHERE sync_backfill_pending=1")[0]['n'] == 0


def test_f1_replay_never_stamps_a_count_on_a_siblings_order_or_rewrites_one():
    """W4a verify F1. A sibling's order that an older build sent without
    `item_count` has pending=0 and a NULL count; counting the lines that have
    arrived so far would stamp a PARTIAL number. A stamped count must also
    survive a replay untouched."""
    sch, db = _install()
    _make_v44(db)
    _migrate(sch, db)
    conn = _open(db)
    try:
        conn.execute("INSERT INTO purchase_orders (company_id, po_number, uid) VALUES (?, 'PO-SIB', ?)",
                     (CID, str(uuid.uuid4())))
        conn.execute("INSERT INTO purchase_orders (company_id, po_number, uid, item_count) "
                     "VALUES (?, 'PO-STAMPED', ?, 5)", (CID, str(uuid.uuid4())))
        conn.commit()
        sch._migrate_retail_schema(conn)
        conn.commit()
    finally:
        conn.close()
    after = _po_rows(db)
    assert after['PO-SIB']['item_count'] is None
    assert after['PO-STAMPED']['item_count'] == 5
    assert after['PO-V45-1']['item_count'] == 2
