"""Aura Retail -- schema v47 migration: stock transfers shared between computers
(W3b; claimed in ROADMAP.md "retail schema v47 CLAIMED").

THE RULE BEING PINNED. v47 adds `stock_transfers.item_count` / `cancelled_at` /
`recalled_at` / `sync_backfill_pending`. Every transfer that exists at upgrade is
announced to the outbox once by the boot backfill (so `sync_backfill_pending=1`
on exactly those rows), gets its line count stamped (a sibling must not receive
or recall until all lines arrived), and has its cancel time inferred: a
cancelled transfer that had been SENT was a recall (`recalled_at` = the same
instant), one cancelled before it was sent was a plain cancel (`recalled_at`
stays NULL) -- the two rank differently when computers merge.

The v46 shape is manufactured the way retail_v46_po_receipts_migration_test.py
does it: a real freshly built install, the v47 columns dropped, `PRAGMA
user_version = 46` stamped; the upgrade then runs through the REAL
`ensure_schema_version` chain (backup + integrity check before and after).

WHAT EACH TEST GUARDS (and the mutation that must turn it RED):
  * pending flag: arming every row on every replay (drop the `item_count IS NULL`
    gate) would re-announce transfers the boot backfill already cleared.
  * recall inference: treating every cancelled row as a recall (drop the
    `sent_at IS NOT NULL` gate) ranks a never-sent cancel above a send.
  * set-once: backfilling every row (drop the `IS NULL` gates) would overwrite a
    real cancel/recall time on replay.

Run:
    pytest products/retail/tests/retail_v47_transfer_sync_migration_test.py -q
"""
import os
import shutil
import sqlite3
import sys
import tempfile
import uuid
from pathlib import Path

PRODUCT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = PRODUCT_DIR / 'backend'
SUITE_ROOT = PRODUCT_DIR.parent.parent
for _p in (str(SUITE_ROOT), str(BACKEND_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

CID = 47
_TMP_DIRS = []
_V47_COLUMNS = ('item_count', 'cancelled_at', 'recalled_at', 'sync_backfill_pending', 'pre_v47')


def teardown_module(module):
    for d in _TMP_DIRS:
        shutil.rmtree(d, ignore_errors=True)
    _TMP_DIRS.clear()


def _install():
    tmp = tempfile.mkdtemp(prefix='aura-retail-v47-')
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


def _make_v46(db):
    """A v46-shaped database holding five transfers (company CID):
    pending (2 lines), sent (in_transit, 1 line), got (received, 2 lines),
    early (cancelled, never sent, 1 line) and recalled (cancelled after send, 1 line).
    Returns {key: id}."""
    conn = _open(db)
    try:
        for c in _V47_COLUMNS:
            if c in {r[1] for r in conn.execute('PRAGMA table_info(stock_transfers)').fetchall()}:
                conn.execute(f'ALTER TABLE stock_transfers DROP COLUMN {c}')
        spec = (
            ('pending', 'pending', None, None, 2),
            ('sent', 'in_transit', '2026-10-01 09:00:00', None, 1),
            ('got', 'received', '2026-10-01 09:00:00', '2026-10-02 09:00:00', 2),
            ('early', 'cancelled', None, None, 1),
            ('recalled', 'cancelled', '2026-10-03 09:00:00', None, 1),
        )
        out = {}
        for key, status, sent_at, received_at, n in spec:
            tid = str(uuid.uuid4())
            conn.execute(
                "INSERT INTO stock_transfers (id, company_id, source_branch_id, destination_branch_id, status, "
                "created_at, sent_at, received_at) VALUES (?,?,1,2,?, '2026-09-30 08:00:00', ?, ?)",
                (tid, CID, status, sent_at, received_at))
            for _ in range(n):
                conn.execute(
                    "INSERT INTO stock_transfer_items (id, transfer_id, product_id, quantity_sent) "
                    "VALUES (?,?,?,3)", (str(uuid.uuid4()), tid, str(uuid.uuid4())))
            out[key] = tid
        conn.execute('PRAGMA user_version = 46')
        conn.commit()
    finally:
        conn.close()
    return out


def _migrate(sch, db):
    from commercial_runtime.security.migration_safety import ensure_schema_version
    backup_dir = tempfile.mkdtemp(prefix='aura-retail-v47-backup-')
    _TMP_DIRS.append(backup_dir)
    conn = _open(db)
    try:
        ensure_schema_version(conn, db, sch.RETAIL_SCHEMA_VERSION, sch._migrate_retail_schema, backup_dir)
    finally:
        conn.close()
    return backup_dir


def _t(db, tid):
    return _rows(db, "SELECT * FROM stock_transfers WHERE id=?", (tid,))[0]


def test_upgrade_adds_columns_stamps_counts_arms_backfill_and_infers_recalls():
    sch, db = _install()
    ids = _make_v46(db)
    backup_dir = _migrate(sch, db)

    assert sch.RETAIL_SCHEMA_VERSION == 53  # v53 UPDATE: head moved 52 -> 53 (MONEY-4c); v53's own shape is pinned by retail_money4c_credit_inherit_test.py. (was:  head moved 51 -> 52 (MONEY-4b); v52's own shape is pinned by retail_money4b_migration_test.py | prior: v51 UPDATE: head moved 50 -> 51 (MONEY-3); v51's own shape is pinned by retail_money3_cheque_books_test.py / retail_money3_supplier_drawer_test.py | prior: v50 UPDATE: head moved 49 -> 50 (W2b/W3a); v50's own shape is pinned by retail_v50_config_sync_migration_test.py)
    assert _rows(db, 'PRAGMA user_version')[0]['user_version'] == 53  # v53 UPDATE: head moved 52 -> 53 (MONEY-4c); v53's own shape is pinned by retail_money4c_credit_inherit_test.py. (was:  head moved 51 -> 52 (MONEY-4b); v52's own shape is pinned by retail_money4b_migration_test.py | prior: v51 UPDATE: head moved 50 -> 51 (MONEY-3); v51's own shape is pinned by retail_money3_cheque_books_test.py / retail_money3_supplier_drawer_test.py | prior: v50 UPDATE: head moved 49 -> 50 (W2b/W3a); v50's own shape is pinned by retail_v50_config_sync_migration_test.py)
    assert _rows(db, 'PRAGMA integrity_check')[0]['integrity_check'] == 'ok'
    assert os.listdir(backup_dir), 'ensure_schema_version must have written its pre-migration backup'
    assert set(_V47_COLUMNS) <= _cols(db, 'stock_transfers')

    counts = {k: _t(db, tid)['item_count'] for k, tid in ids.items()}
    assert counts == {'pending': 2, 'sent': 1, 'got': 2, 'early': 1, 'recalled': 1}
    assert all(_t(db, tid)['sync_backfill_pending'] == 1 for tid in ids.values()), \
        'every pre-existing transfer is announced once by the boot backfill'
    # W3b fix (F3): the same rows are marked as predating v47 (the books guard
    # must not apply to a transfer that may never have had a send journal).
    assert all(_t(db, tid)['pre_v47'] == 1 for tid in ids.values())

    assert _t(db, ids['pending'])['cancelled_at'] is None and _t(db, ids['pending'])['recalled_at'] is None
    assert _t(db, ids['sent'])['cancelled_at'] is None and _t(db, ids['sent'])['recalled_at'] is None
    assert _t(db, ids['got'])['cancelled_at'] is None and _t(db, ids['got'])['recalled_at'] is None
    early = _t(db, ids['early'])
    assert early['cancelled_at'] == '2026-09-30 08:00:00', 'a never-sent cancel falls back to the creation time'
    assert early['recalled_at'] is None, 'cancelled BEFORE send is a plain cancel, never a recall'
    rec = _t(db, ids['recalled'])
    assert rec['cancelled_at'] == '2026-10-03 09:00:00' and rec['recalled_at'] == '2026-10-03 09:00:00', \
        'a cancel after send was a recall, stamped with the send time as the best known instant'


def test_replay_is_byte_identical_and_never_rearms_a_cleared_flag_or_rewrites_a_set_time():
    sch, db = _install()
    ids = _make_v46(db)
    _migrate(sch, db)
    conn = _open(db)
    try:
        # the boot backfill cleared one flag; a real recall time was stamped by a v47 route
        conn.execute("UPDATE stock_transfers SET sync_backfill_pending=0 WHERE id=?", (ids['sent'],))
        conn.execute("UPDATE stock_transfers SET cancelled_at='2026-10-05 00:00:00', "
                     "recalled_at='2026-10-05 00:00:00' WHERE id=?", (ids['recalled'],))
        # a transfer written by v47 code (item_count stamped, flag 0)
        new = str(uuid.uuid4())
        conn.execute("INSERT INTO stock_transfers (id, company_id, source_branch_id, destination_branch_id, "
                     "item_count, sync_backfill_pending) VALUES (?,?,1,2,2,0)", (new, CID))
        conn.commit()
    finally:
        conn.close()
    before = _rows(db, "SELECT * FROM stock_transfers ORDER BY id")
    conn = _open(db)
    try:
        sch._migrate_retail_schema(conn)
        sch._migrate_retail_schema(conn)
        conn.commit()
    finally:
        conn.close()
    after = _rows(db, "SELECT * FROM stock_transfers ORDER BY id")
    assert after == before, 'replaying the whole chain changes no transfer row'
    assert _t(db, ids['sent'])['sync_backfill_pending'] == 0
    # the boot backfill clears sync_backfill_pending but NEVER pre_v47, and a v47-written row stays 0
    assert _t(db, ids['sent'])['pre_v47'] == 1 and _t(db, new)['pre_v47'] == 0
    assert _t(db, ids['recalled'])['recalled_at'] == '2026-10-05 00:00:00'
    assert _t(db, new)['sync_backfill_pending'] == 0 and _t(db, new)['item_count'] == 2


def test_a_fresh_install_has_the_v47_shape_and_defaults_the_flag_to_zero():
    sch, db = _install()
    assert _rows(db, 'PRAGMA user_version')[0]['user_version'] == sch.RETAIL_SCHEMA_VERSION == 53  # v53 UPDATE: head moved 52 -> 53 (MONEY-4c); v53's own shape is pinned by retail_money4c_credit_inherit_test.py. (was:  head moved 51 -> 52 (MONEY-4b); v52's own shape is pinned by retail_money4b_migration_test.py | prior: v51 UPDATE: head moved 50 -> 51 (MONEY-3); v51's own shape is pinned by retail_money3_cheque_books_test.py / retail_money3_supplier_drawer_test.py | prior: v50 UPDATE: head moved 49 -> 50 (W2b/W3a); v50's own shape is pinned by retail_v50_config_sync_migration_test.py)
    assert set(_V47_COLUMNS) <= _cols(db, 'stock_transfers')
    conn = _open(db)
    try:
        tid = str(uuid.uuid4())
        conn.execute("INSERT INTO stock_transfers (id, company_id, source_branch_id, destination_branch_id) "
                     "VALUES (?,?,1,2)", (tid, CID))
        conn.commit()
    finally:
        conn.close()
    row = _t(db, tid)
    assert row['sync_backfill_pending'] == 0 and row['item_count'] is None
    assert row['pre_v47'] == 0
    assert row['cancelled_at'] is None and row['recalled_at'] is None
