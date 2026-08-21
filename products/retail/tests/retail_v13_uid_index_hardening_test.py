"""
Aura Retail -- schema v13, the two ways the uid unique index can brick an
install. See database/schema.py::_migrate_add_identity_and_attribution_columns.

Both defects here share one consequence, and it is the worst one this
codebase has: `app.py::init_app()` calls `init_retail()` UNCONDITIONALLY at
import, and `ensure_schema_version` advances `PRAGMA user_version` ONLY on
full success. So anything that makes the v13 step raise does not merely fail
one migration -- the app never starts again, and because the version marker
never moves, every future migration is blocked behind it too. There is no
in-app recovery path from that state; the customer's till is a brick until
somebody edits their database by hand.

  F1  `CREATE UNIQUE INDEX` was issued with nothing checked first. A single
      duplicate `uid` anywhere in the seven uid tables therefore raised
      sqlite3.IntegrityError out of init_retail() forever. No shipped writer
      produces one today (every one of them uses uuid4, and the `sale_items`
      uid is correctly generated inside the per-line loop), but a restore, a
      two-database merge, support SQL, or any future row-copy would -- and
      those are exactly the situations where the customer is least able to
      absorb "the app will not open".

      Note the asymmetry this closes: v14 (`rebind_company_id`) verifies its
      own postcondition and rolls back on mismatch. v13 verified nothing.
      The repair is safe precisely because `uid` is a WIRE identity, not user
      data: it was minted by this migration, nothing external references it
      yet, and regenerating a duplicate loses nothing a human ever typed.

  F4  `CREATE UNIQUE INDEX IF NOT EXISTS idx_<t>_uid` is satisfied by ANY
      index of that name -- including a NON-unique one left by an
      interrupted earlier attempt or by a hand-written support fix. v13 then
      reported success, `user_version` advanced to 14, and uid uniqueness
      was absent forever with no signal anywhere. IF NOT EXISTS is a
      statement about the NAME, never about the SHAPE.

Plus the connection-leak sub-finding: init_retail() had no try/finally, so a
migration failure left its connection alive inside the raised traceback,
holding the WAL write lock. The next launch then reported "database is
locked" -- masking the real cause with a symptom that points at the wrong
thing entirely.

Run:
    pytest products/retail/tests/retail_v13_uid_index_hardening_test.py -v
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

UID_TABLES = (
    'branches', 'sales', 'sale_items', 'returns', 'return_items',
    'inventory_movements', 'payments',
)

_TMP_DIRS = []


def teardown_module(module):
    for d in _TMP_DIRS:
        shutil.rmtree(d, ignore_errors=True)
    _TMP_DIRS.clear()


# ── fixtures ────────────────────────────────────────────────────────────────

def _build_v12_install():
    """A REAL schema-v12 retail.db with real seeded shop data, in a fresh
    AURA_APP_DATA temp dir.

    Built by running the genuine `init_retail()` with the v13/v14 steps
    stubbed out and RETAIL_SCHEMA_VERSION pinned back to 12 -- not from a
    hand-written v12 DDL snapshot, which is a copy that rots the moment
    schema.py's base `executescript` changes. `_migrate_retail_schema` looks
    its steps up as module globals at call time, so replacing the attributes
    on the module object is enough to take them out of the chain.

    Returns (app_data_dir, module, db_path). The caller opens its own
    connections, because several tests here need to prove something about a
    COLD start -- the connection state left behind by the fixture is exactly
    what a cold start does not have.
    """
    tmp = tempfile.mkdtemp(prefix='aura-retail-v13-idx-')
    _TMP_DIRS.append(tmp)
    os.environ['AURA_APP_DATA'] = tmp

    import database.schema as sch
    sch.BASE_DIR = os.path.join(tmp, 'database')
    sch.SUBSYS_DIR = os.path.join(sch.BASE_DIR, 'subsystems')

    real_version = sch.RETAIL_SCHEMA_VERSION
    real_v13 = sch._migrate_add_identity_and_attribution_columns
    real_v14 = sch._migrate_rebind_company_id_to_owner_issued
    sch.RETAIL_SCHEMA_VERSION = 12
    sch._migrate_add_identity_and_attribution_columns = lambda conn: None
    sch._migrate_rebind_company_id_to_owner_issued = lambda conn: None
    try:
        sch.init_retail()
    finally:
        sch.RETAIL_SCHEMA_VERSION = real_version
        sch._migrate_add_identity_and_attribution_columns = real_v13
        sch._migrate_rebind_company_id_to_owner_issued = real_v14

    db_path = os.path.join(sch.SUBSYS_DIR, 'retail.db')
    probe = sqlite3.connect(db_path)
    try:
        assert probe.execute('PRAGMA user_version').fetchone()[0] == 12, \
            'fixture did not actually produce a v12 database'
        assert probe.execute('SELECT COUNT(*) FROM sales').fetchone()[0] >= 40, \
            'fixture lost the seeded sales history'
    finally:
        probe.close()
    return tmp, sch, db_path


def _seed_returns_and_payments(conn):
    """`_seed_retail` writes sales/sale_items/inventory_movements but leaves
    returns/return_items/payments empty, and three of the seven uid tables
    being empty would make every per-table assertion below vacuously true --
    a duplicate cannot be planted in a table with no rows, and a non-unique
    index on an empty table rejects nothing either way."""
    sale_ids = [r[0] for r in conn.execute('SELECT id FROM sales ORDER BY id LIMIT 3')]
    product_ids = [r[0] for r in conn.execute('SELECT id FROM products ORDER BY sku LIMIT 3')]
    for i, sale_id in enumerate(sale_ids):
        conn.execute(
            "INSERT INTO returns (company_id,return_number,sale_id,branch_id,cashier,reason,"
            "refund_method,refund_amount,status) VALUES (?,?,?,?,?,?,?,?,'completed')",
            (1, f'RET-{i}', sale_id, 1, f'Cashier {i}', 'damaged', 'cash', 10.0 + i),
        )
        return_id = conn.execute('SELECT last_insert_rowid()').fetchone()[0]
        conn.execute(
            'INSERT INTO return_items (return_id,product_id,quantity,unit_price,line_total) '
            'VALUES (?,?,?,?,?)',
            (return_id, product_ids[i], 1, 10.0 + i, 10.0 + i),
        )
        conn.execute(
            "INSERT INTO payments (company_id,sale_id,method,amount,reference,status,idempotency_key) "
            "VALUES (?,?,?,?,?,'success',?)",
            (1, sale_id, 'cash', 25.0 + i, f'PAY-{i}', f'idem-{i}'),
        )
    conn.commit()


def _half_applied_v13(conn, table, *, duplicate=False, stale_plain_index=False):
    """Reproduce the two states a HALF-FINISHED v13 leaves behind.

    This is the honest starting point for both defects, and it is reachable
    without any exotic assumption: `ensure_schema_version` re-runs the whole
    chain on the next launch after any failure, so "the column exists and
    the index does not" is the NORMAL intermediate state of this migration,
    not a contrived one. What happens to the database between those two
    launches -- a restore, a merge, a support fix -- is outside v13's
    control, and that is the entire point.

    `duplicate=True`  -- two rows share one uid (a restored/merged row).
    `stale_plain_index=True` -- a same-named index exists but is NOT unique.
    """
    conn.execute(f'ALTER TABLE "{table}" ADD COLUMN uid TEXT')
    rowids = [r[0] for r in conn.execute(f'SELECT rowid FROM "{table}" ORDER BY rowid')]
    assert len(rowids) >= 2, f'{table} needs at least two rows to be worth testing'
    for rid in rowids:
        conn.execute(f'UPDATE "{table}" SET uid=? WHERE rowid=?', (str(uuid.uuid4()), rid))
    if duplicate:
        shared = conn.execute(
            f'SELECT uid FROM "{table}" WHERE rowid=?', (rowids[0],)
        ).fetchone()[0]
        conn.execute(f'UPDATE "{table}" SET uid=? WHERE rowid=?', (shared, rowids[-1]))
    if stale_plain_index:
        conn.execute(f'CREATE INDEX idx_{table}_uid ON "{table}"(uid)')
    conn.commit()


def _index_row(conn, table, name):
    for seq, iname, unique, origin, partial in conn.execute(
        f'PRAGMA index_list("{table}")'
    ).fetchall():
        if iname == name:
            return {'unique': unique, 'origin': origin, 'partial': partial}
    return None


def _duplicate_uids(conn, table):
    return conn.execute(
        f'SELECT COUNT(*) FROM (SELECT uid FROM "{table}" WHERE uid IS NOT NULL '
        f'GROUP BY uid HAVING COUNT(*) > 1)'
    ).fetchone()[0]


# ── F1: a duplicate uid must be repaired, never raised ──────────────────────

def test_v13_repairs_a_duplicate_uid_instead_of_raising_out_of_the_migration():
    """The whole defect in one assertion: this call must not raise.

    Everything after it is about the repair being a REPAIR -- no row lost,
    no uid churned that did not have to be, and the surviving values still
    the RFC-4122 shape Owner's sync ingest gates on (`uuid.UUID(entity_id)`).
    """
    _tmp, sch, db_path = _build_v12_install()
    conn = sqlite3.connect(db_path)
    conn.execute('PRAGMA foreign_keys=ON')
    _seed_returns_and_payments(conn)
    _half_applied_v13(conn, 'sales', duplicate=True)

    assert _duplicate_uids(conn, 'sales') == 1, 'fixture did not actually plant a duplicate'
    before_rows = conn.execute('SELECT COUNT(*) FROM sales').fetchone()[0]
    survivors = dict(conn.execute(
        'SELECT rowid, uid FROM sales WHERE rowid NOT IN '
        '(SELECT MAX(rowid) FROM sales GROUP BY uid HAVING COUNT(*) > 1)'
    ).fetchall())

    sch._migrate_add_identity_and_attribution_columns(conn)
    conn.commit()

    assert _duplicate_uids(conn, 'sales') == 0, 'the duplicate uid survived the migration'
    assert conn.execute('SELECT COUNT(*) FROM sales').fetchone()[0] == before_rows, \
        'the repair deleted a sale -- a duplicate uid is a wire identity, not a reason to drop a row'
    assert conn.execute('SELECT COUNT(*) FROM sales WHERE uid IS NULL').fetchone()[0] == 0

    # Only the duplicate is reissued. A repair that regenerated every uid in
    # the table would be indistinguishable from a correct one here while
    # silently breaking every uid another device had already seen.
    for rowid, uid in conn.execute('SELECT rowid, uid FROM sales').fetchall():
        if rowid in survivors:
            assert uid == survivors[rowid], \
                f'sales rowid {rowid} had a distinct uid already and was needlessly reissued'
        parsed = uuid.UUID(uid)
        assert parsed.version == 4 and str(parsed) == uid, f'{uid!r} is not a canonical uuid4'

    assert conn.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
    conn.close()


@pytest.mark.parametrize('table', UID_TABLES)
def test_v13_repairs_a_duplicate_uid_in_every_uid_table_not_only_sales(table):
    """Seven tables get this index; the repair has to cover all seven.

    A fix written against `sales` alone passes the test above and still
    bricks any install whose duplicate happens to be in `sale_items` -- by
    far the largest of these tables, and the one a partial restore is most
    likely to land in.
    """
    _tmp, sch, db_path = _build_v12_install()
    conn = sqlite3.connect(db_path)
    conn.execute('PRAGMA foreign_keys=ON')
    _seed_returns_and_payments(conn)
    _half_applied_v13(conn, table, duplicate=True)
    assert _duplicate_uids(conn, table) == 1

    sch._migrate_add_identity_and_attribution_columns(conn)
    conn.commit()

    assert _duplicate_uids(conn, table) == 0
    assert _index_row(conn, table, f'idx_{table}_uid')['unique'] == 1
    conn.close()


def test_a_duplicate_uid_does_not_stop_the_app_from_starting_or_freeze_user_version():
    """The end-to-end version: not "the function returns", but "the app comes
    up and the schema version moves".

    `app.py::init_app()` calls `init_retail()` unconditionally, so this is
    the actual boot path. `user_version` reaching RETAIL_SCHEMA_VERSION is
    the part that matters most -- a version marker frozen at 12 blocks every
    migration this product will ever ship, not just this one.
    """
    _tmp, sch, db_path = _build_v12_install()
    conn = sqlite3.connect(db_path)
    conn.execute('PRAGMA foreign_keys=ON')
    _seed_returns_and_payments(conn)
    _half_applied_v13(conn, 'sale_items', duplicate=True)
    conn.close()

    sch.init_retail()

    conn = sqlite3.connect(db_path)
    assert conn.execute('PRAGMA user_version').fetchone()[0] == sch.RETAIL_SCHEMA_VERSION, \
        'user_version did not advance -- this install can never take another migration'
    assert _duplicate_uids(conn, 'sale_items') == 0
    assert conn.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
    conn.close()


# ── F4: IF NOT EXISTS is about the NAME, not the SHAPE ──────────────────────

@pytest.mark.parametrize('table', UID_TABLES)
def test_v13_replaces_a_same_named_non_unique_index_left_by_an_earlier_attempt(table):
    """`CREATE UNIQUE INDEX IF NOT EXISTS idx_<t>_uid` is fully satisfied by a
    plain, non-unique index of that name. v13 then advanced `user_version`
    and reported success while enforcing nothing -- the failure mode with no
    signal at all, which is strictly worse than the crash F1 describes.

    Asserted BOTH ways on purpose: `PRAGMA index_list` proves the stored
    shape, and a real duplicate write proves the constraint is live. The
    first alone would pass on an index that is flagged unique in the catalogue
    but never consulted; the second alone would pass on an index created
    under some other name.
    """
    _tmp, sch, db_path = _build_v12_install()
    conn = sqlite3.connect(db_path)
    conn.execute('PRAGMA foreign_keys=ON')
    _seed_returns_and_payments(conn)
    _half_applied_v13(conn, table, stale_plain_index=True)

    stale = _index_row(conn, table, f'idx_{table}_uid')
    assert stale is not None and stale['unique'] == 0, \
        'fixture did not actually plant a NON-unique index'

    sch._migrate_add_identity_and_attribution_columns(conn)
    conn.commit()

    shape = _index_row(conn, table, f'idx_{table}_uid')
    assert shape is not None, f'idx_{table}_uid disappeared'
    assert shape['unique'] == 1, (
        f'idx_{table}_uid is still a plain index -- IF NOT EXISTS matched the name and '
        'v13 reported success while uid uniqueness stayed absent forever'
    )

    rowids = [r[0] for r in conn.execute(f'SELECT rowid FROM "{table}" ORDER BY rowid')]
    taken = conn.execute(f'SELECT uid FROM "{table}" WHERE rowid=?', (rowids[0],)).fetchone()[0]
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(f'UPDATE "{table}" SET uid=? WHERE rowid=?', (taken, rowids[-1]))
    conn.rollback()
    conn.close()


@pytest.mark.parametrize('table', UID_TABLES)
def test_the_uid_index_actually_rejects_a_duplicate_on_every_uid_table(table):
    """The pre-existing behavioural uniqueness check exercised `sales` only;
    the other six were name-checked against `sqlite_master` and nothing more,
    so a non-unique index anywhere else passed. This is that check, on all
    seven, on a normally-migrated install."""
    _tmp, sch, db_path = _build_v12_install()
    conn = sqlite3.connect(db_path)
    conn.execute('PRAGMA foreign_keys=ON')
    _seed_returns_and_payments(conn)

    sch._migrate_add_identity_and_attribution_columns(conn)
    conn.commit()

    shape = _index_row(conn, table, f'idx_{table}_uid')
    assert shape is not None, f'missing idx_{table}_uid'
    assert shape['unique'] == 1, f'idx_{table}_uid is not a UNIQUE index'
    assert shape['partial'] == 1, (
        f'idx_{table}_uid lost its "WHERE uid IS NOT NULL" predicate; a full index would '
        'make every NULL-uid row collide on a database where the backfill has not run yet'
    )

    rowids = [r[0] for r in conn.execute(f'SELECT rowid FROM "{table}" ORDER BY rowid')]
    assert len(rowids) >= 2, f'{table} has too few rows for this assertion to mean anything'
    taken = conn.execute(f'SELECT uid FROM "{table}" WHERE rowid=?', (rowids[0],)).fetchone()[0]
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(f'UPDATE "{table}" SET uid=? WHERE rowid=?', (taken, rowids[-1]))
    conn.rollback()
    conn.close()


# ── the connection-leak sub-finding ─────────────────────────────────────────

def test_init_retail_releases_its_connection_when_a_migration_step_fails():
    """A failing migration used to leave init_retail()'s connection alive --
    the raised traceback holds the frame, the frame holds the connection, and
    the connection holds the WAL write lock it took when the migration's
    first UPDATE opened an implicit transaction.

    The next launch then failed with "database is locked", which points at
    concurrency and says nothing about the migration that actually broke. A
    support engineer reading that message looks for a second copy of the app.

    The probe below uses a 250 ms busy_timeout rather than schema.py's own
    30 s: this asserts the lock is GONE, and waiting half a minute to find
    out would make the failure look like a hang instead of a failure.
    """
    _tmp, sch, db_path = _build_v12_install()

    def _boom(conn):
        # Take the write lock exactly the way a real migration step does --
        # any DML opens Python's implicit transaction -- and only then fail.
        conn.execute("UPDATE sales SET cashier = cashier")
        raise RuntimeError('simulated migration failure')

    real_v13 = sch._migrate_add_identity_and_attribution_columns
    sch._migrate_add_identity_and_attribution_columns = _boom
    try:
        with pytest.raises(RuntimeError, match='simulated migration failure') as excinfo:
            sch.init_retail()
        # Hold the traceback for the duration of the probe, which is what the
        # real caller does too (app.py lets it propagate, and the logging
        # machinery keeps the traceback alive). Releasing it here would make
        # this test pass for the wrong reason -- garbage collection, not the
        # try/finally being present.
        assert excinfo.value is not None
    finally:
        sch._migrate_add_identity_and_attribution_columns = real_v13

    probe = sqlite3.connect(db_path, timeout=0.25)
    try:
        probe.execute('BEGIN IMMEDIATE')
        probe.rollback()
    except sqlite3.OperationalError as exc:
        pytest.fail(
            f'init_retail() leaked its connection mid-transaction: {exc}. The next '
            'launch reports "database is locked" and the real migration failure is '
            'invisible.'
        )
    finally:
        probe.close()
