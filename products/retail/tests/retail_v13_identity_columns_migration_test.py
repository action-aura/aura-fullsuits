"""
Aura Retail -- schema v13 migration regression coverage (identity and
attribution columns; ROADMAP.md's 2026-08-21 reservation, Phase 2 of
docs/launch-readiness/multi-device-design.md). See
database/schema.py::_migrate_add_identity_and_attribution_columns.

This is the most dangerous migration shipped to this database so far: unlike
v7/v8/v9/v11/v12 (which only CREATE new tables) it ALTERs the tables holding
a real shop's entire sales history. It is therefore additive only -- ALTER
TABLE ADD COLUMN, CREATE [UNIQUE] INDEX IF NOT EXISTS and guarded UPDATEs,
never the DROP+RENAME table rebuild `_migrate_products_to_uuid` uses. These
tests pin all three properties that make that safe:

  1. Every pre-existing row survives with a distinct, RFC-4122-shaped `uid`
     and an unchanged row count.
  2. A second run is a clean no-op and leaves integrity_check == 'ok'.
  3. The legacy free-text attribution columns (`cashier`, `created_by`,
     `opened_by`) come out byte-identical -- v13 adds a second, structured
     attribution channel beside them, it never guesses at or rewrites the
     first one.

Plus a source-level guard (test_v13_and_v14_contain_no_destructive_ddl)
that fails if anyone later adds a DROP/RENAME/table-rebuild to either of
the two functions this phase owns.

Run:
    pytest products/retail/tests/retail_v13_identity_columns_migration_test.py -v
"""
import os
import re
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

# Every table that gains `uid` + a unique index at v13.
UID_TABLES = (
    'branches', 'sales', 'sale_items', 'returns', 'return_items',
    'inventory_movements', 'payments',
)
# Every table that gains the actor/terminal/UTC-clock triple at v13.
ACTOR_TABLES = ('sales', 'returns', 'inventory_movements', 'cash_sessions', 'cash_movements')
# The four catalogue tables plus reorder_requests, which gain the
# row_version / updated_at_utc / deleted_at_utc soft-delete triple.
ROW_VERSION_TABLES = ('categories', 'products', 'customers', 'suppliers', 'reorder_requests')

_TMP_DIRS = []


def teardown_module(module):
    for d in _TMP_DIRS:
        shutil.rmtree(d, ignore_errors=True)
    _TMP_DIRS.clear()


def _build_v12_install():
    """Produce a REAL schema-v12 retail.db, with real seeded shop data, in a
    fresh AURA_APP_DATA temp dir.

    Built by running the genuine `init_retail()` with the v13/v14 steps
    stubbed out and `RETAIL_SCHEMA_VERSION` pinned back to 12, rather than by
    hand-writing a v12 DDL snapshot into this file. A hand-written snapshot
    is a copy that silently rots the moment schema.py's base
    `executescript` changes -- and it is exactly the pre-v13 shape of the
    LIVE table set (40 seeded sales, their sale_items and
    inventory_movements, 12 products, 5 customers, 3 branches) that this
    migration has to survive, not an approximation of it.

    `_migrate_retail_schema` looks its steps up as module globals at call
    time, so replacing the attributes on the module object is enough to
    remove them from the chain for this one build.
    """
    tmp = tempfile.mkdtemp(prefix='aura-retail-v13-')
    _TMP_DIRS.append(tmp)
    os.environ['AURA_APP_DATA'] = tmp

    import database.schema as sch

    # schema.py resolves BASE_DIR/SUBSYS_DIR from AURA_APP_DATA once, at
    # import time -- deliberately, because a real process imports it exactly
    # once (see products/run_all_tests.py's module docstring). Repointing the
    # two module globals is how a test file that builds SEVERAL temp installs
    # in one process stays honest about which one it is talking to.
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
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys=ON')
    assert conn.execute('PRAGMA user_version').fetchone()[0] == 12, \
        'fixture did not actually produce a v12 database'
    _add_returns_and_payments(conn)
    return conn, db_path


def _add_returns_and_payments(conn):
    """`_seed_retail` writes sales/sale_items/inventory_movements but no
    returns/return_items/payments, and v13 has to give those three a `uid`
    too -- so the fixture adds a few real rows rather than leaving three of
    the seven uid tables empty and the assertions vacuously true."""
    sale_ids = [r[0] for r in conn.execute('SELECT id FROM sales ORDER BY id LIMIT 3').fetchall()]
    product_ids = [r[0] for r in conn.execute('SELECT id FROM products ORDER BY sku LIMIT 3').fetchall()]
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


def _table_columns(conn, table):
    return {r[1] for r in conn.execute(f'PRAGMA table_info({table})').fetchall()}


def _row_counts(conn, tables):
    return {t: conn.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0] for t in tables}


def _run_v13(conn):
    from database.schema import _migrate_add_identity_and_attribution_columns
    _migrate_add_identity_and_attribution_columns(conn)
    conn.commit()


# ── 1. a v12 database with real data takes the migration cleanly ────────────

def test_v13_gives_every_existing_row_a_distinct_rfc4122_uid_without_losing_a_row():
    conn, _db_path = _build_v12_install()

    # Nothing may exist yet -- if it does, the fixture is not really v12 and
    # every assertion below would be testing the wrong starting state.
    for table in UID_TABLES:
        assert 'uid' not in _table_columns(conn, table), f'{table}.uid already exists pre-v13'

    before = _row_counts(conn, UID_TABLES)
    assert before['sales'] >= 40, 'fixture lost the seeded sales history'
    assert before['sale_items'] > 0 and before['inventory_movements'] > 0
    assert before['returns'] == 3 and before['return_items'] == 3 and before['payments'] == 3

    _run_v13(conn)

    after = _row_counts(conn, UID_TABLES)
    assert after == before, f'v13 changed row counts: {before} -> {after}'

    all_uids = []
    for table in UID_TABLES:
        rows = conn.execute(f'SELECT uid FROM {table}').fetchall()
        assert len(rows) == before[table]
        for (value,) in rows:
            assert value, f'{table} has a row with no uid after v13'
            # RFC-4122 shape, asserted by parsing rather than by regex alone:
            # this is the exact gate Owner's sync ingest applies
            # (`uuid.UUID(entity_id)`), and `lower(hex(randomblob(16)))` --
            # the tempting pure-SQL shortcut -- passes a "32 hex chars" eye
            # test while failing this one. See account_schema.py's
            # `_backfill_uids` for where this bit the registry v3 migration.
            parsed = uuid.UUID(value)
            assert parsed.version == 4, f'{table}.uid {value!r} is not a uuid4'
            assert str(parsed) == value, f'{table}.uid {value!r} is not canonical 8-4-4-4-12 form'
            all_uids.append(value)

    assert len(all_uids) == len(set(all_uids)), 'uid collision across tables -- uuid4 was not per-row'
    conn.close()


def test_v13_uid_unique_index_exists_and_actually_rejects_a_duplicate():
    conn, _db_path = _build_v12_install()
    _run_v13(conn)

    indexes = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index'"
    ).fetchall()}
    for table in UID_TABLES:
        assert f'idx_{table}_uid' in indexes, f'missing unique index on {table}.uid'

    # The index is the whole reason the column can be added without a table
    # rebuild (ALTER TABLE ADD COLUMN cannot carry UNIQUE), so prove it is a
    # real constraint rather than a plain index that merely got named "uid".
    taken = conn.execute('SELECT uid FROM sales LIMIT 1').fetchone()[0]
    other_id = conn.execute('SELECT id FROM sales ORDER BY id DESC LIMIT 1').fetchone()[0]
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute('UPDATE sales SET uid=? WHERE id=?', (taken, other_id))
    conn.rollback()
    conn.close()


def test_v13_adds_actor_terminal_and_utc_clock_columns_to_the_transactional_tables():
    conn, _db_path = _build_v12_install()
    _run_v13(conn)

    for table in ACTOR_TABLES:
        cols = _table_columns(conn, table)
        for expected in ('actor_user_uid', 'terminal_id', 'created_at_utc'):
            assert expected in cols, f'{table} is missing {expected} after v13'
    conn.close()


def test_v13_adds_row_version_triple_to_the_catalogue_tables_and_reorder_requests():
    conn, _db_path = _build_v12_install()
    _run_v13(conn)

    for table in ROW_VERSION_TABLES:
        cols = _table_columns(conn, table)
        for expected in ('row_version', 'updated_at_utc', 'deleted_at_utc'):
            assert expected in cols, f'{table} is missing {expected} after v13'
        # ADD COLUMN ... NOT NULL DEFAULT 1 backfills every existing row in
        # place; nothing may be left NULL or the reject-stale comparison the
        # column exists for has nothing to compare.
        nulls = conn.execute(f'SELECT COUNT(*) FROM {table} WHERE row_version IS NULL').fetchone()[0]
        assert nulls == 0, f'{table}.row_version left NULLs behind'
        rows = conn.execute(f'SELECT COUNT(*) FROM {table}').fetchall()[0][0]
        if rows:
            ones = conn.execute(f'SELECT COUNT(*) FROM {table} WHERE row_version=1').fetchone()[0]
            assert ones == rows, f'{table}.row_version did not default every existing row to 1'
        # A tombstone stamped by a migration would be a lie about when --
        # nobody has been deleted yet.
        stamped = conn.execute(f'SELECT COUNT(*) FROM {table} WHERE deleted_at_utc IS NOT NULL').fetchone()[0]
        assert stamped == 0, f'{table} arrived with fabricated tombstones'
    conn.close()


def test_v13_leaves_created_at_utc_null_on_history_rather_than_fabricating_an_instant():
    """`sales.created_at` / `returns.created_at` are written as LOCAL wall
    clock on purpose (api/retail_api.py::create_sale, create_return -- see the
    "Write LOCAL time, not the UTC CURRENT_TIMESTAMP default" comment there),
    while `inventory_movements.created_at` takes SQLite's DEFAULT
    CURRENT_TIMESTAMP, which is UTC. The same column name therefore means two
    different things across these tables today.

    A backfill would have to pick an offset to convert the local values with,
    and the only offset available at migration time is THIS machine's CURRENT
    one -- wrong by an hour for half of every year, and wrong by whole hours
    for any row rung on a device in another timezone. That is a fabricated
    instant, and this migration's rule is that unmatched history stays
    unattributed rather than guessed at.

    The consequence is load-bearing for whoever moves the report predicates
    onto this column: they must read
    `COALESCE(created_at_utc, created_at)`, never `created_at_utc` alone, or
    every historical row silently drops out of every daily total.
    """
    conn, _db_path = _build_v12_install()
    _run_v13(conn)

    for table in ACTOR_TABLES:
        total = conn.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]
        if not total:
            continue
        stamped = conn.execute(
            f'SELECT COUNT(*) FROM {table} WHERE created_at_utc IS NOT NULL'
        ).fetchone()[0]
        assert stamped == 0, (
            f'{table}.created_at_utc was backfilled on {stamped} historical row(s); '
            'a converted local timestamp is a fabricated UTC instant'
        )
        # Same reasoning for terminal_id: this device cannot prove it is the
        # terminal that rang a historical sale.
        attributed = conn.execute(
            f'SELECT COUNT(*) FROM {table} WHERE terminal_id IS NOT NULL '
            'OR actor_user_uid IS NOT NULL'
        ).fetchone()[0]
        assert attributed == 0, f'{table} gained guessed actor/terminal attribution on history'
    conn.close()


# ── 2. idempotency ──────────────────────────────────────────────────────────

def test_v13_run_twice_is_a_no_op_and_integrity_check_still_passes():
    conn, db_path = _build_v12_install()
    _run_v13(conn)

    snapshot = {}
    for table in UID_TABLES:
        snapshot[table] = conn.execute(
            f'SELECT rowid, uid FROM {table} ORDER BY rowid'
        ).fetchall()
    counts_before = _row_counts(conn, UID_TABLES + ROW_VERSION_TABLES)

    # Second run -- the real-world trigger is a migration that failed AFTER
    # this step (ensure_schema_version leaves user_version un-advanced, so
    # the whole chain re-runs on the next launch).
    _run_v13(conn)

    for table in UID_TABLES:
        again = conn.execute(f'SELECT rowid, uid FROM {table} ORDER BY rowid').fetchall()
        assert [tuple(r) for r in again] == [tuple(r) for r in snapshot[table]], \
            f'{table} uids were regenerated on the second run'
    assert _row_counts(conn, UID_TABLES + ROW_VERSION_TABLES) == counts_before

    assert conn.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
    conn.close()


def test_v13_backfills_only_the_rows_that_still_need_a_uid():
    """A row inserted by an older code path between the ALTER and the
    backfill (or by a partially-applied earlier attempt) must be filled in,
    while a row that already carries a uid must be left exactly as it is."""
    conn, _db_path = _build_v12_install()
    _run_v13(conn)

    keeper_id, keeper_uid = conn.execute('SELECT id, uid FROM sales ORDER BY id LIMIT 1').fetchone()
    blanked_id = conn.execute('SELECT id FROM sales ORDER BY id DESC LIMIT 1').fetchone()[0]
    conn.execute('UPDATE sales SET uid=NULL WHERE id=?', (blanked_id,))
    conn.commit()

    _run_v13(conn)

    assert conn.execute('SELECT uid FROM sales WHERE id=?', (keeper_id,)).fetchone()[0] == keeper_uid
    refilled = conn.execute('SELECT uid FROM sales WHERE id=?', (blanked_id,)).fetchone()[0]
    assert refilled and refilled != keeper_uid
    uuid.UUID(refilled)
    conn.close()


# ── 3. the legacy free-text attribution columns are untouched ───────────────

def test_v13_leaves_the_legacy_free_text_attribution_columns_byte_identical():
    """`cashier`, `created_by` and `opened_by` are KEPT, never rewritten and
    never used to guess at `actor_user_uid`. A wrong name on a sale is worse
    than no name -- and these columns are the only surviving evidence of who
    the shop believed rang a historical transaction."""
    conn, _db_path = _build_v12_install()

    before = {
        'sales': conn.execute('SELECT id, cashier FROM sales ORDER BY id').fetchall(),
        'returns': conn.execute('SELECT id, cashier FROM returns ORDER BY id').fetchall(),
        'inventory_movements': conn.execute(
            'SELECT id, created_by FROM inventory_movements ORDER BY id'
        ).fetchall(),
    }
    assert any(r[1] for r in before['sales']), 'fixture has no cashier values to protect'

    _run_v13(conn)

    for table, sql in (
        ('sales', 'SELECT id, cashier FROM sales ORDER BY id'),
        ('returns', 'SELECT id, cashier FROM returns ORDER BY id'),
        ('inventory_movements', 'SELECT id, created_by FROM inventory_movements ORDER BY id'),
    ):
        after = conn.execute(sql).fetchall()
        assert [tuple(r) for r in after] == [tuple(r) for r in before[table]], \
            f'{table} legacy attribution column was modified by v13'
    conn.close()


# ── 4. the full chain lands on the reserved version number ──────────────────

def test_fresh_install_lands_on_the_reserved_schema_version_with_every_v13_column():
    """A brand-new install must reach the reserved version through the whole
    v0 -> v14 chain in one pass, not just via the isolated function above."""
    tmp = tempfile.mkdtemp(prefix='aura-retail-fresh-')
    _TMP_DIRS.append(tmp)
    os.environ['AURA_APP_DATA'] = tmp

    import database.schema as sch
    sch.BASE_DIR = os.path.join(tmp, 'database')
    sch.SUBSYS_DIR = os.path.join(sch.BASE_DIR, 'subsystems')
    sch.init_retail()

    conn = sqlite3.connect(os.path.join(sch.SUBSYS_DIR, 'retail.db'))
    conn.row_factory = sqlite3.Row
    assert conn.execute('PRAGMA user_version').fetchone()[0] == sch.RETAIL_SCHEMA_VERSION
    assert sch.RETAIL_SCHEMA_VERSION >= 14, 'v13/v14 were not claimed'

    for table in UID_TABLES:
        assert 'uid' in _table_columns(conn, table)
    for table in ACTOR_TABLES:
        assert 'terminal_id' in _table_columns(conn, table)
    for table in ROW_VERSION_TABLES:
        assert 'row_version' in _table_columns(conn, table)
    assert conn.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
    conn.close()


def test_v13_tolerates_a_minimal_schema_that_lacks_most_of_these_tables():
    """`retail_category_delete_fk_sync_test.py` hand-builds a THREE-table
    database (categories/products/inventory_movements) and calls
    `_migrate_retail_schema` against it directly. An unconditional
    `ALTER TABLE sales ...` here would crash that fixture with "no such
    table: sales" -- the exact hazard `_migrate_add_shift_cash_drawer`
    already documents and guards for `sales`/`returns`."""
    from database.schema import _migrate_add_identity_and_attribution_columns

    with tempfile.TemporaryDirectory() as tmp:
        conn = sqlite3.connect(os.path.join(tmp, 'minimal.db'))
        conn.row_factory = sqlite3.Row
        conn.executescript("""
            CREATE TABLE categories (id TEXT PRIMARY KEY, company_id INTEGER DEFAULT 1, name TEXT);
            CREATE TABLE products (id TEXT PRIMARY KEY, company_id INTEGER DEFAULT 1, sku TEXT);
            CREATE TABLE inventory_movements (
                id INTEGER PRIMARY KEY AUTOINCREMENT, company_id INTEGER DEFAULT 1,
                product_id TEXT, quantity REAL, created_by TEXT
            );
        """)
        conn.execute("INSERT INTO categories (id,name) VALUES (?, 'Cat')", (str(uuid.uuid4()),))
        conn.execute("INSERT INTO products (id,sku) VALUES (?, 'SKU-1')", (str(uuid.uuid4()),))
        conn.execute("INSERT INTO inventory_movements (product_id,quantity,created_by) VALUES ('p',1,'System')")
        conn.commit()

        _migrate_add_identity_and_attribution_columns(conn)
        conn.commit()

        assert 'uid' in _table_columns(conn, 'inventory_movements')
        assert 'row_version' in _table_columns(conn, 'categories')
        assert conn.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
        conn.close()


# ── 5. the destructive-DDL tripwire ─────────────────────────────────────────

def test_v13_and_v14_contain_no_destructive_ddl():
    """Fails if anyone later adds a DROP, a RENAME, or a `_new`-table rebuild
    to either function this phase owns.

    `_migrate_products_to_uuid` (schema.py) legitimately does all three --
    it had to, because it retyped a primary key. Its risk profile is
    deliberately NOT repeated on live sales data: a rebuild of `sales` /
    `sale_items` copies every historical transaction through a fresh table
    with `PRAGMA foreign_keys` temporarily OFF, and any mistake in the
    column list silently drops real financial data (see
    `_legacy_supplier_link`'s docstring for a case where exactly that
    happened, unnoticed, with no error and a passing integrity_check).

    Source-level rather than behavioural because the failure this guards
    against is a future edit, not a current bug -- there is no runtime
    signal for "somebody added a DROP TABLE here" until it has already run
    against a customer's database.
    """
    import ast
    import inspect
    import textwrap

    from database import schema as sch

    banned = re.compile(
        r'\bDROP\s+(TABLE|COLUMN|INDEX)\b'
        r'|\bALTER\s+TABLE\s+\S+\s+RENAME\b'
        r'|\bRENAME\s+TO\b'
        r'|CREATE\s+TABLE\s+\w+_new\b',
        re.IGNORECASE,
    )

    def _executable_source(fn):
        """The function's real statements, docstring excluded.

        Excluded via `ast`, not a string replace: these functions explain at
        length WHY they never rebuild a table, and that explanation names the
        exact statements the regex hunts for. `inspect.getdoc()` dedents,
        so it does not match the raw indented source and a naive
        `source.replace(...)` silently strips nothing at all -- a tripwire
        that always fires is as useless as one that never does.
        """
        src = textwrap.dedent(inspect.getsource(fn))
        node = ast.parse(src).body[0]
        body = node.body
        if (body and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)):
            body = body[1:]
        return '\n'.join(ast.get_source_segment(src, stmt) or '' for stmt in body)

    functions = (
        sch._migrate_add_identity_and_attribution_columns,
        sch._migrate_rebind_company_id_to_owner_issued,
        sch.rebind_company_id,
        sch._rebind_to_owner_issued,
        sch.company_scoped_tables,
    )
    # Self-check, so this cannot pass by accident. A tripwire built on source
    # inspection has two silent failure modes -- a stripper that removes the
    # whole body, and a regex that matches nothing -- and both look exactly
    # like "all clear". The probe below proves the real thing: prose in a
    # docstring is ignored, an actual statement is caught.
    def _probe_clean():
        """Mentions DROP TABLE sales and RENAME TO sales_old in prose only."""
        conn.execute('ALTER TABLE sales ADD COLUMN x TEXT')  # noqa: F821

    def _probe_destructive():
        """Additive only, honestly."""
        conn.execute('DROP TABLE sales')  # noqa: F821

    assert banned.search(_executable_source(_probe_clean)) is None, \
        'the docstring stripper regressed -- prose is being scanned as code'
    assert banned.search(_executable_source(_probe_destructive)) is not None, \
        'the regex no longer detects a real DROP -- this tripwire is inert'

    for fn in functions:
        hit = banned.search(_executable_source(fn))
        assert hit is None, (
            f'{fn.__name__} contains destructive DDL ({hit.group(0)!r}). v13/v14 are '
            'additive-only by design -- see ROADMAP.md 2026-08-21 rule 3.'
        )
