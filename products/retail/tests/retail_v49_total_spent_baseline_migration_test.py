"""Aura Retail -- schema v49 migration: customers.total_spent_baseline (REHEARSAL-1007 B6b;
claimed in ROADMAP.md "retail schema v49").

THE RULE BEING PINNED. v49 adds `customers.total_spent_baseline` = the part of a customer's
`total_spent` that THIS system's sales and returns do not explain (history brought in from a
previous system). For every customer that exists at upgrade it is set to
MAX(0, total_spent - derived_now), derived_now being the exact figure
`core.retail.customer_spend` derives (sales less completed-return refunds, floored at zero).
A customer whose column already equals the derived figure gets 0; an imported customer with
no sales gets their whole figure. It is set ONLY in the step that adds the column, so replaying
the chain can never re-arm it over a baseline later changed by an import.

The v48 shape is manufactured the way retail_v48_loyalty_sync_migration_test.py does it: a
real freshly built install, the v49 column dropped, `PRAGMA user_version = 48` stamped; the
upgrade then runs through the REAL `ensure_schema_version` chain (backup + integrity check).

WHAT EACH TEST GUARDS (and the mutation that must turn it RED):
  * baseline value: hard-coding 0, or copying total_spent whole, breaks the three customers.
  * derived part: dropping the "- derived_now" makes a customer with sales claim their own
    sales as history (and the cache would double count them).
  * replay: arming the baseline outside the "column was just added" branch rewrites the
    changed baseline on the second pass.
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

CID = 49
_TMP_DIRS = []


def teardown_module(module):
    for d in _TMP_DIRS:
        shutil.rmtree(d, ignore_errors=True)
    _TMP_DIRS.clear()


def _install():
    tmp = tempfile.mkdtemp(prefix='aura-retail-v49-')
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


def _make_v48(db):
    """A v48-shaped database with three customers:
       imported  -- total_spent 500, no sales  (history only)             -> baseline 500
       mixed     -- total_spent 750 = a 250 sale here + 500 of history    -> baseline 500
       plain     -- total_spent 250 = exactly its one sale here           -> baseline 0
    Returns the ids."""
    conn = _open(db)
    try:
        if 'total_spent_baseline' in {r[1] for r in conn.execute('PRAGMA table_info(customers)').fetchall()}:
            conn.execute('ALTER TABLE customers DROP COLUMN total_spent_baseline')
        ids = {k: str(uuid.uuid4()) for k in ('imported', 'mixed', 'plain')}
        for key, spent in (('imported', 500), ('mixed', 750), ('plain', 250)):
            conn.execute("INSERT INTO customers (id, company_id, name, total_spent) VALUES (?,?,?,?)",
                         (ids[key], CID, key, spent))
        for key in ('mixed', 'plain'):
            conn.execute(
                "INSERT INTO sales (uid, company_id, sale_number, customer_id, subtotal, total, status) "
                "VALUES (?,?,?,?,250,250,'completed')",
                (str(uuid.uuid4()), CID, f'S-{key}-{uuid.uuid4().hex[:4]}', ids[key]))
        conn.execute('PRAGMA user_version = 48')
        conn.commit()
    finally:
        conn.close()
    return ids


def _migrate(sch, db):
    from commercial_runtime.security.migration_safety import ensure_schema_version
    backup_dir = tempfile.mkdtemp(prefix='aura-retail-v49-backup-')
    _TMP_DIRS.append(backup_dir)
    conn = _open(db)
    try:
        ensure_schema_version(conn, db, sch.RETAIL_SCHEMA_VERSION, sch._migrate_retail_schema, backup_dir)
    finally:
        conn.close()
    return backup_dir


def _baseline(db, cust):
    return _rows(db, "SELECT total_spent, total_spent_baseline FROM customers WHERE id=?", (cust,))[0]


def test_upgrade_adds_the_column_and_sets_each_baseline_from_history():
    sch, db = _install()
    ids = _make_v48(db)
    backup_dir = _migrate(sch, db)

    assert sch.RETAIL_SCHEMA_VERSION == 57  # v56 UPDATE: head moved 54 -> 56 (SYNC-BOOT Phase 2; v55 is MONEY-5's and is not on this line of history); v56's own shape is pinned by retail_site_stream_cursors_migration_test. Same exact-number pin as before.  # v54 UPDATE: head moved 53 -> 54 (UI-3a); v54's own shape is pinned by retail_ui3a_sync_migration_test. [earlier: v53 UPDATE: head moved 52 -> 53 (MONEY-4c); v53's own shape is pinned by retail_money4c_credit_inherit_test.py. (was:  head moved 51 -> 52 (MONEY-4b); v52's own shape is pinned by retail_money4b_migration_test.py | prior: v51 UPDATE: head moved 50 -> 51 (MONEY-3); v51's own shape is pinned by retail_money3_cheque_books_test.py / retail_money3_supplier_drawer_test.py | prior: v50 UPDATE: head moved 49 -> 50 (W2b/W3a); v50's own shape is pinned by retail_v50_config_sync_migration_test.py)]  # v57 UPDATE: head moved 56 -> 57 (STOCK-TAKE, ROADMAP v57 claim); same exact pin, not loosened.
    assert _rows(db, 'PRAGMA user_version')[0]['user_version'] == 57  # v56 UPDATE: head moved 54 -> 56 (SYNC-BOOT Phase 2; v55 is MONEY-5's and is not on this line of history); v56's own shape is pinned by retail_site_stream_cursors_migration_test. Same exact-number pin as before.  # v54 UPDATE: head moved 53 -> 54 (UI-3a); v54's own shape is pinned by retail_ui3a_sync_migration_test. [earlier: v53 UPDATE: head moved 52 -> 53 (MONEY-4c); v53's own shape is pinned by retail_money4c_credit_inherit_test.py. (was:  head moved 51 -> 52 (MONEY-4b); v52's own shape is pinned by retail_money4b_migration_test.py | prior: v51 UPDATE: head moved 50 -> 51 (MONEY-3); v51's own shape is pinned by retail_money3_cheque_books_test.py / retail_money3_supplier_drawer_test.py | prior: v50 UPDATE: head moved 49 -> 50 (W2b/W3a); v50's own shape is pinned by retail_v50_config_sync_migration_test.py)]  # v57 UPDATE: head moved 56 -> 57 (STOCK-TAKE, ROADMAP v57 claim); same exact pin, not loosened.
    assert _rows(db, 'PRAGMA integrity_check')[0]['integrity_check'] == 'ok'
    assert os.listdir(backup_dir), 'ensure_schema_version must have written its pre-migration backup'
    assert 'total_spent_baseline' in _cols(db, 'customers')

    assert _baseline(db, ids['imported'])['total_spent_baseline'] == 500.0
    assert _baseline(db, ids['mixed'])['total_spent_baseline'] == 500.0, \
        'history = total_spent (750) less what this system explains (the 250 sale)'
    assert _baseline(db, ids['plain'])['total_spent_baseline'] == 0.0
    # the visible figure is unchanged by the upgrade for every one of them
    assert [_baseline(db, ids[k])['total_spent'] for k in ('imported', 'mixed', 'plain')] == [500, 750, 250]


def test_replay_is_byte_identical_and_never_rearms_a_changed_baseline():
    sch, db = _install()
    ids = _make_v48(db)
    _migrate(sch, db)
    conn = _open(db)
    try:
        # a later re-import replaced the baseline (the cache, 750, is deliberately left
        # stale: a re-derivation on replay would then give 750 - 250 = 500, not 640 -- the
        # replay test must be able to tell "left alone" from "re-derived to the same value")
        conn.execute("UPDATE customers SET total_spent_baseline=640 WHERE id=?", (ids['mixed'],))
        conn.commit()
    finally:
        conn.close()
    before = _rows(db, "SELECT * FROM customers ORDER BY id")
    conn = _open(db)
    try:
        sch._migrate_retail_schema(conn)
        sch._migrate_retail_schema(conn)
        conn.commit()
    finally:
        conn.close()
    assert _rows(db, "SELECT * FROM customers ORDER BY id") == before, 'replaying the chain changes no customer row'
    assert _baseline(db, ids['mixed'])['total_spent_baseline'] == 640.0


def test_a_fresh_install_has_the_column_defaulting_to_zero():
    sch, db = _install()
    assert _rows(db, 'PRAGMA user_version')[0]['user_version'] == sch.RETAIL_SCHEMA_VERSION == 57  # v56 UPDATE: head moved 54 -> 56 (SYNC-BOOT Phase 2; v55 is MONEY-5's and is not on this line of history); v56's own shape is pinned by retail_site_stream_cursors_migration_test. Same exact-number pin as before.  # v54 UPDATE: head moved 53 -> 54 (UI-3a); v54's own shape is pinned by retail_ui3a_sync_migration_test. [earlier: v53 UPDATE: head moved 52 -> 53 (MONEY-4c); v53's own shape is pinned by retail_money4c_credit_inherit_test.py. (was:  head moved 51 -> 52 (MONEY-4b); v52's own shape is pinned by retail_money4b_migration_test.py | prior: v51 UPDATE: head moved 50 -> 51 (MONEY-3); v51's own shape is pinned by retail_money3_cheque_books_test.py / retail_money3_supplier_drawer_test.py | prior: v50 UPDATE: head moved 49 -> 50 (W2b/W3a); v50's own shape is pinned by retail_v50_config_sync_migration_test.py)]  # v57 UPDATE: head moved 56 -> 57 (STOCK-TAKE, ROADMAP v57 claim); same exact pin, not loosened.
    assert 'total_spent_baseline' in _cols(db, 'customers')
    conn = _open(db)
    try:
        cust = str(uuid.uuid4())
        conn.execute("INSERT INTO customers (id, company_id, name) VALUES (?,?,?)", (cust, CID, 'c'))
        conn.commit()
    finally:
        conn.close()
    assert _baseline(db, cust)['total_spent_baseline'] == 0
