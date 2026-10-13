"""Aura Retail -- schema v48 migration: loyalty points shared between computers
(W2a; claimed in ROADMAP.md "retail schema v48 CLAIMED").

THE RULE BEING PINNED. v48 adds `loyalty_ledger.sync_backfill_pending` (1 on
exactly the rows that existed at upgrade -- the boot step announces each one to
the outbox once, then clears it) and `sales.points_earned` (NULL for old sales:
unknown, never "zero"). It also re-keys every customer's 'opening' row to the
deterministic uid `core.retail.loyalty.opening_uid(customer)`, so two computers'
own opening rows for one customer collide on the unique uid index instead of
doubling the balance. A customer who somehow has SEVERAL local opening rows gets
them merged into one row carrying their sum.

The v47 shape is manufactured the way retail_v47_transfer_sync_migration_test.py
does it: a real freshly built install, the v48 columns dropped, `PRAGMA
user_version = 47` stamped; the upgrade then runs through the REAL
`ensure_schema_version` chain (backup + integrity check before and after).

WHAT EACH TEST GUARDS (and the mutation that must turn it RED):
  * flag armed once: arming on every replay (drop the "column was just added"
    gate) would re-announce rows the boot backfill already cleared.
  * opening re-key: skipping it leaves a random uid that collides with nothing.
  * merge: not deleting the extra opening rows leaves the balance doubled.
  * replay: any non-idempotent step changes a row on the second pass.
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

CID = 48
_TMP_DIRS = []


def teardown_module(module):
    for d in _TMP_DIRS:
        shutil.rmtree(d, ignore_errors=True)
    _TMP_DIRS.clear()


def _install():
    tmp = tempfile.mkdtemp(prefix='aura-retail-v48-')
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


def _make_v47(db):
    """A v47-shaped database: the v48 columns dropped; customers solo (one
    opening row), duo (TWO opening rows, 30 and 12) and nohist (an earn only);
    plus one sale. Returns the customer ids."""
    conn = _open(db)
    try:
        if 'sync_backfill_pending' in {r[1] for r in conn.execute('PRAGMA table_info(loyalty_ledger)').fetchall()}:
            conn.execute('ALTER TABLE loyalty_ledger DROP COLUMN sync_backfill_pending')
        if 'points_earned' in {r[1] for r in conn.execute('PRAGMA table_info(sales)').fetchall()}:
            conn.execute('ALTER TABLE sales DROP COLUMN points_earned')
        ids = {k: str(uuid.uuid4()) for k in ('solo', 'duo', 'nohist')}
        for cust in ids.values():
            conn.execute("INSERT INTO customers (id, company_id, name, loyalty_points) VALUES (?,?,?,0)",
                         (cust, CID, 'c'))

        def add(cust, delta, kind):
            conn.execute("INSERT INTO loyalty_ledger (uid, company_id, customer_id, points_delta, entry_type) "
                         "VALUES (?,?,?,?,?)", (str(uuid.uuid4()), CID, cust, delta, kind))
        add(ids['solo'], 20, 'opening')
        add(ids['solo'], 5, 'earn')
        add(ids['duo'], 30, 'opening')
        add(ids['duo'], 12, 'opening')
        add(ids['nohist'], 7, 'earn')
        conn.execute('PRAGMA user_version = 47')
        conn.commit()
    finally:
        conn.close()
    return ids


def _migrate(sch, db):
    from commercial_runtime.security.migration_safety import ensure_schema_version
    backup_dir = tempfile.mkdtemp(prefix='aura-retail-v48-backup-')
    _TMP_DIRS.append(backup_dir)
    conn = _open(db)
    try:
        ensure_schema_version(conn, db, sch.RETAIL_SCHEMA_VERSION, sch._migrate_retail_schema, backup_dir)
    finally:
        conn.close()
    return backup_dir


def _ledger(db, cust):
    return _rows(db, "SELECT * FROM loyalty_ledger WHERE customer_id=? ORDER BY id", (cust,))


def test_upgrade_adds_columns_arms_every_existing_row_and_rekeys_opening():
    from core.retail import loyalty
    sch, db = _install()
    ids = _make_v47(db)
    backup_dir = _migrate(sch, db)

    assert sch.RETAIL_SCHEMA_VERSION == 56  # v56 UPDATE: head moved 54 -> 56 (SYNC-BOOT Phase 2; v55 is MONEY-5's and is not on this line of history); v56's own shape is pinned by retail_site_stream_cursors_migration_test. Same exact-number pin as before.  # v54 UPDATE: head moved 53 -> 54 (UI-3a); v54's own shape is pinned by retail_ui3a_sync_migration_test. [earlier: v53 UPDATE: head moved 52 -> 53 (MONEY-4c); v53's own shape is pinned by retail_money4c_credit_inherit_test.py. (was:  head moved 51 -> 52 (MONEY-4b); v52's own shape is pinned by retail_money4b_migration_test.py | prior: v51 UPDATE: head moved 50 -> 51 (MONEY-3); v51's own shape is pinned by retail_money3_cheque_books_test.py / retail_money3_supplier_drawer_test.py | prior: v50 UPDATE: head moved 49 -> 50 (W2b/W3a); v50's own shape is pinned by retail_v50_config_sync_migration_test.py)]
    assert _rows(db, 'PRAGMA user_version')[0]['user_version'] == 56  # v56 UPDATE: head moved 54 -> 56 (SYNC-BOOT Phase 2; v55 is MONEY-5's and is not on this line of history); v56's own shape is pinned by retail_site_stream_cursors_migration_test. Same exact-number pin as before.  # v54 UPDATE: head moved 53 -> 54 (UI-3a); v54's own shape is pinned by retail_ui3a_sync_migration_test. [earlier: v53 UPDATE: head moved 52 -> 53 (MONEY-4c); v53's own shape is pinned by retail_money4c_credit_inherit_test.py. (was:  head moved 51 -> 52 (MONEY-4b); v52's own shape is pinned by retail_money4b_migration_test.py | prior: v51 UPDATE: head moved 50 -> 51 (MONEY-3); v51's own shape is pinned by retail_money3_cheque_books_test.py / retail_money3_supplier_drawer_test.py | prior: v50 UPDATE: head moved 49 -> 50 (W2b/W3a); v50's own shape is pinned by retail_v50_config_sync_migration_test.py)]
    assert _rows(db, 'PRAGMA integrity_check')[0]['integrity_check'] == 'ok'
    assert os.listdir(backup_dir), 'ensure_schema_version must have written its pre-migration backup'
    assert 'sync_backfill_pending' in _cols(db, 'loyalty_ledger')
    assert 'return_id' in _cols(db, 'loyalty_ledger')
    assert 'points_earned' in _cols(db, 'sales')

    every = _rows(db, "SELECT sync_backfill_pending FROM loyalty_ledger")
    assert every and all(r['sync_backfill_pending'] == 1 for r in every), \
        'every pre-existing ledger row is announced once by the boot backfill'

    solo = _ledger(db, ids['solo'])
    opening = [r for r in solo if r['entry_type'] == 'opening']
    assert [r['uid'] for r in opening] == [loyalty.opening_uid(ids['solo'])]
    assert [r['points_delta'] for r in opening] == [20]
    earn = [r for r in solo if r['entry_type'] == 'earn']
    assert len(earn) == 1 and earn[0]['uid'] != loyalty.opening_uid(ids['solo']), 'only opening rows are re-keyed'

    nohist = _ledger(db, ids['nohist'])
    assert [r['entry_type'] for r in nohist] == ['earn'], 'a customer with no opening row gains none'


def test_several_local_opening_rows_merge_into_one_carrying_their_sum():
    from core.retail import loyalty
    sch, db = _install()
    ids = _make_v47(db)
    _migrate(sch, db)
    duo = _ledger(db, ids['duo'])
    assert len(duo) == 1, 'the extra opening rows are deleted'
    assert duo[0]['uid'] == loyalty.opening_uid(ids['duo'])
    assert duo[0]['points_delta'] == 42 and duo[0]['entry_type'] == 'opening'
    assert sum(r['points_delta'] for r in duo) == 42, 'the balance is unchanged by the merge'


def test_replay_is_byte_identical_and_never_rearms_a_cleared_flag():
    sch, db = _install()
    ids = _make_v47(db)
    _migrate(sch, db)
    conn = _open(db)
    try:
        # the boot backfill announced and cleared one row
        conn.execute("UPDATE loyalty_ledger SET sync_backfill_pending=0 WHERE customer_id=? AND entry_type='earn'",
                     (ids['solo'],))
        # a row written by v48 code is never pending
        conn.execute("INSERT INTO loyalty_ledger (uid, company_id, customer_id, points_delta, entry_type, "
                     "sync_backfill_pending) VALUES (?,?,?,3,'earn',0)", (str(uuid.uuid4()), CID, ids['solo']))
        conn.commit()
    finally:
        conn.close()
    before = _rows(db, "SELECT * FROM loyalty_ledger ORDER BY id")
    conn = _open(db)
    try:
        sch._migrate_retail_schema(conn)
        sch._migrate_retail_schema(conn)
        conn.commit()
    finally:
        conn.close()
    after = _rows(db, "SELECT * FROM loyalty_ledger ORDER BY id")
    assert after == before, 'replaying the whole chain changes no ledger row'
    cleared = [r for r in after if r['customer_id'] == ids['solo'] and r['entry_type'] == 'earn']
    assert cleared and all(r['sync_backfill_pending'] == 0 for r in cleared)


def test_a_fresh_install_has_the_v48_shape_and_defaults_the_flag_to_zero():
    sch, db = _install()
    assert _rows(db, 'PRAGMA user_version')[0]['user_version'] == sch.RETAIL_SCHEMA_VERSION == 56  # v56 UPDATE: head moved 54 -> 56 (SYNC-BOOT Phase 2; v55 is MONEY-5's and is not on this line of history); v56's own shape is pinned by retail_site_stream_cursors_migration_test. Same exact-number pin as before.  # v54 UPDATE: head moved 53 -> 54 (UI-3a); v54's own shape is pinned by retail_ui3a_sync_migration_test. [earlier: v53 UPDATE: head moved 52 -> 53 (MONEY-4c); v53's own shape is pinned by retail_money4c_credit_inherit_test.py. (was:  head moved 51 -> 52 (MONEY-4b); v52's own shape is pinned by retail_money4b_migration_test.py | prior: v51 UPDATE: head moved 50 -> 51 (MONEY-3); v51's own shape is pinned by retail_money3_cheque_books_test.py / retail_money3_supplier_drawer_test.py | prior: v50 UPDATE: head moved 49 -> 50 (W2b/W3a); v50's own shape is pinned by retail_v50_config_sync_migration_test.py)]
    assert {'sync_backfill_pending', 'return_id'} <= _cols(db, 'loyalty_ledger')
    assert 'points_earned' in _cols(db, 'sales')
    conn = _open(db)
    try:
        cust = str(uuid.uuid4())
        conn.execute("INSERT INTO customers (id, company_id, name) VALUES (?,?,?)", (cust, CID, 'c'))
        conn.execute("INSERT INTO loyalty_ledger (uid, company_id, customer_id, points_delta, entry_type) "
                     "VALUES (?,?,?,1,'earn')", (str(uuid.uuid4()), CID, cust))
        conn.commit()
    finally:
        conn.close()
    assert _ledger(db, cust)[0]['sync_backfill_pending'] == 0
    assert not _rows(db, "SELECT 1 FROM sales WHERE points_earned IS NOT NULL")
