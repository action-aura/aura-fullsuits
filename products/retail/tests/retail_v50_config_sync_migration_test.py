"""Aura Retail -- schema v50 migration: promotions and extras configuration shared
between computers (W2b/W3a; claimed in ROADMAP.md "retail schema v50").

THE RULE BEING PINNED. v50 gives `promotions` a wire identity (`uid`, deterministic
for pre-existing rows so the same promotion typed on two computers collapses into one),
a `row_version` and `updated_at_utc` (last-writer-wins needs them), and gives the four
configuration tables a `sync_backfill_pending` flag armed on exactly the rows that
exist at upgrade. `sale_item_promotions` gains a `uid` (history rows, never announced
on their own; they travel inside their sale line).

The v49 shape is manufactured the way retail_v48_loyalty_sync_migration_test.py does
it: a real freshly built install, the v50 columns dropped, `PRAGMA user_version = 49`
stamped; the upgrade then runs through the REAL `ensure_schema_version` chain (backup +
integrity check before and after).

WHAT EACH TEST GUARDS (and the mutation that must turn it RED):
  * deterministic uid: a random uid on legacy rows leaves two computers with the same
    promotion twice after first sync.
  * identical copies on ONE computer: without the ordinal the unique index refuses and
    the upgrade wedges.
  * flags armed once: arming on every replay re-announces rows the boot backfill cleared.
  * replay byte-identical: any non-idempotent step changes a row on the second pass.
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

CID = 50
_TMP_DIRS = []

_PROMO_NEW = ('uid', 'row_version', 'updated_at_utc', 'sync_backfill_pending')
_FLAGGED = ('promotions', 'modifier_groups', 'modifier_options', 'product_modifier_groups')


def teardown_module(module):
    for d in _TMP_DIRS:
        shutil.rmtree(d, ignore_errors=True)
    _TMP_DIRS.clear()


def _install():
    tmp = tempfile.mkdtemp(prefix='aura-retail-v50-')
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


def _drop_index_and_col(conn, table, col):
    for r in conn.execute(f'PRAGMA index_list("{table}")').fetchall():
        info = [c[2] for c in conn.execute(f'PRAGMA index_info("{r[1]}")').fetchall()]
        if col in info and not r[1].startswith('sqlite_autoindex'):
            conn.execute(f'DROP INDEX "{r[1]}"')
    if col in {r[1] for r in conn.execute(f'PRAGMA table_info("{table}")').fetchall()}:
        conn.execute(f'ALTER TABLE "{table}" DROP COLUMN "{col}"')


def _make_v49(db):
    """A v49-shaped database: every v50 column dropped, user_version 49, and rows:
    two promotions (one all-branch, one branch-scoped), a THIRD field-for-field copy
    of the first, a group, two options, a link and a sale line with a promotion."""
    conn = _open(db)
    try:
        for t in _PROMO_NEW:
            _drop_index_and_col(conn, 'promotions', t)
        for t in ('modifier_groups', 'modifier_options', 'product_modifier_groups'):
            _drop_index_and_col(conn, t, 'sync_backfill_pending')
        _drop_index_and_col(conn, 'sale_item_promotions', 'uid')
        branch_uid = str(uuid.uuid4())
        conn.execute("INSERT INTO branches (id, company_id, name, uid) VALUES (91, ?, 'Main', ?)",
                     (CID, branch_uid))
        ins = ("INSERT INTO promotions (company_id, name, discount_pct, product_id, category_id, "
               "branch_id, starts_at, ends_at, status) VALUES (?,?,?,?,?,?,?,?,?)")
        conn.execute(ins, (CID, 'Eid', 10, None, 3, None, '2026-10-01T00:00', '2026-10-31T23:59', 'active'))
        conn.execute(ins, (CID, 'Eid', 10.0, None, 3, None, '2026-10-01T00:00', '2026-10-31T23:59', 'active'))
        conn.execute(ins, (CID, 'Main only', 5, 7, None, 91, None, None, 'active'))
        g = str(uuid.uuid4())
        conn.execute("INSERT INTO modifier_groups (id, company_id, name) VALUES (?,?,'Extras')", (g, CID))
        for n in ('Cheese', 'Onion'):
            conn.execute("INSERT INTO modifier_options (id, company_id, group_id, name) VALUES (?,?,?,?)",
                         (str(uuid.uuid4()), CID, g, n))
        conn.execute("INSERT INTO product_modifier_groups (id, company_id, product_id, group_id) "
                     "VALUES (?,?,'p1',?)", (str(uuid.uuid4()), CID, g))
        conn.execute("INSERT INTO sale_item_promotions (company_id, sale_item_id, promotion_id, name_snapshot, "
                     "discount_pct_snapshot, discount_amount_snapshot) VALUES (?,1,1,'Eid',10,1)", (CID,))
        conn.execute("INSERT INTO sale_item_promotions (company_id, sale_item_id, promotion_id, name_snapshot, "
                     "discount_pct_snapshot, discount_amount_snapshot) VALUES (?,2,1,'Eid',10,2)", (CID,))
        conn.execute('PRAGMA user_version = 49')
        conn.commit()
    finally:
        conn.close()
    return branch_uid


def _migrate(sch, db):
    from commercial_runtime.security.migration_safety import ensure_schema_version
    backup_dir = tempfile.mkdtemp(prefix='aura-retail-v50-backup-')
    _TMP_DIRS.append(backup_dir)
    conn = _open(db)
    try:
        ensure_schema_version(conn, db, sch.RETAIL_SCHEMA_VERSION, sch._migrate_retail_schema, backup_dir)
    finally:
        conn.close()
    return backup_dir


def _promos(db):
    return _rows(db, "SELECT * FROM promotions ORDER BY id")


def test_upgrade_adds_columns_arms_every_existing_row_and_stamps_uids():
    from core.retail import config_sync as cs
    sch, db = _install()
    branch_uid = _make_v49(db)
    backup_dir = _migrate(sch, db)

    assert sch.RETAIL_SCHEMA_VERSION == 50
    assert _rows(db, 'PRAGMA user_version')[0]['user_version'] == 50
    assert _rows(db, 'PRAGMA integrity_check')[0]['integrity_check'] == 'ok'
    assert os.listdir(backup_dir), 'ensure_schema_version must have written its pre-migration backup'
    assert set(_PROMO_NEW) <= _cols(db, 'promotions')
    assert 'uid' in _cols(db, 'sale_item_promotions')
    for t in _FLAGGED:
        rows = _rows(db, f'SELECT sync_backfill_pending AS f FROM "{t}"')
        assert rows and all(r['f'] == 1 for r in rows), f'{t}: every pre-existing row is announced once'

    p = _promos(db)
    assert [r['row_version'] for r in p] == [1, 1, 1]
    assert p[0]['uid'] == cs.promotion_legacy_uid('Eid', 10, None, 3, '2026-10-01T00:00', '2026-10-31T23:59', '')
    assert p[1]['uid'] == cs.promotion_legacy_uid('Eid', 10, None, 3, '2026-10-01T00:00', '2026-10-31T23:59', '',
                                                  ordinal=1), 'a second identical copy gets its own uid'
    assert p[2]['uid'] == cs.promotion_legacy_uid('Main only', 5, 7, None, None, None, branch_uid), \
        'the branch enters the key by its UID, never by the local id'
    assert len({r['uid'] for r in p}) == 3

    sip = _rows(db, "SELECT uid FROM sale_item_promotions")
    assert len(sip) == 2 and all(r['uid'] for r in sip) and len({r['uid'] for r in sip}) == 2


def test_two_computers_derive_the_same_uids_for_the_same_promotions():
    schA, dbA = _install()
    _make_v49(dbA)
    _migrate(schA, dbA)
    schB, dbB = _install()
    _make_v49(dbB)
    _migrate(schB, dbB)
    a = [r['uid'] for r in _promos(dbA) if r['name'] == 'Eid']
    b = [r['uid'] for r in _promos(dbB) if r['name'] == 'Eid']
    assert a == b and len(a) == 2


def test_unique_uid_indexes_exist_and_are_partial():
    sch, db = _install()
    _make_v49(db)
    _migrate(sch, db)
    for table in ('promotions', 'sale_item_promotions'):
        idx = [r for r in _rows(db, f'PRAGMA index_list("{table}")') if r['unique'] == 1 and r['partial'] == 1]
        assert any([c['name'] for c in _rows(db, f'PRAGMA index_info("{r["name"]}")')] == ['uid'] for r in idx), table


def test_replay_is_byte_identical_and_never_rearms_a_cleared_flag():
    sch, db = _install()
    _make_v49(db)
    _migrate(sch, db)
    conn = _open(db)
    try:
        conn.execute("UPDATE promotions SET sync_backfill_pending=0 WHERE name='Eid'")
        conn.execute("UPDATE modifier_groups SET sync_backfill_pending=0")
        conn.execute("INSERT INTO promotions (company_id, name, discount_pct, product_id, uid, sync_backfill_pending) "
                     "VALUES (?,?,?,?,?,0)", (CID, 'New', 3, 9, str(uuid.uuid4())))
        conn.commit()
    finally:
        conn.close()
    tables = list(_FLAGGED) + ['sale_item_promotions']
    before = {t: _rows(db, f'SELECT * FROM "{t}" ORDER BY rowid') for t in tables}
    conn = _open(db)
    try:
        sch._migrate_retail_schema(conn)
        sch._migrate_retail_schema(conn)
        conn.commit()
    finally:
        conn.close()
    after = {t: _rows(db, f'SELECT * FROM "{t}" ORDER BY rowid') for t in tables}
    assert after == before, 'replaying the whole chain changes no configuration row'
    assert [r['sync_backfill_pending'] for r in after['promotions']] == [0, 0, 1, 0]


def test_a_fresh_install_has_the_v50_shape_and_defaults_flags_and_version():
    sch, db = _install()
    assert _rows(db, 'PRAGMA user_version')[0]['user_version'] == sch.RETAIL_SCHEMA_VERSION == 50
    assert set(_PROMO_NEW) <= _cols(db, 'promotions')
    for t in ('modifier_groups', 'modifier_options', 'product_modifier_groups'):
        assert 'sync_backfill_pending' in _cols(db, t)
    conn = _open(db)
    try:
        conn.execute("INSERT INTO promotions (company_id, name, discount_pct, product_id) VALUES (?,?,?,?)",
                     (CID, 'x', 1, 1))
        conn.commit()
    finally:
        conn.close()
    row = _promos(db)[0]
    assert row['row_version'] == 1 and row['sync_backfill_pending'] == 0 and row['uid'] is None
