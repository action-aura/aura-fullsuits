"""
Aura Retail -- the behavioural "additive only" guard for schema v13 and v14.
See database/schema.py::_migrate_add_identity_and_attribution_columns and
::rebind_company_id.

WHY THIS FILE REPLACES A SOURCE-TEXT TRIPWIRE.

`retail_v13_identity_columns_migration_test.py` carried a guard called
`test_v13_and_v14_contain_no_destructive_ddl`. It read the SOURCE of five
named functions with `inspect.getsource` and searched it with a regex for
`DROP TABLE|COLUMN|INDEX`, `ALTER ... RENAME`, `RENAME TO` and
`CREATE TABLE \\w+_new`. It was careful about docstrings, it self-checked its
own stripper, and it was still worth almost nothing, because four separate
destructive mutations were injected into the migrations and the suite stayed
green every time:

  1. `DELETE FROM audit_log` + `DELETE FROM held_sales` inside v13   -> 11 passed
  2. `_verb = "DROP"` then `conn.execute(f"{_verb} TABLE ...")`      -> 11 passed
     (the table really was dropped)
  3. `DELETE FROM sale_items` inside `rebind_company_id`             -> 14 passed
     (the shop's line items were wiped)
  4. `UPDATE sales SET total = total * 1.0001, tax_amount = 0`       -> 11 passed
     (every recorded total silently rewritten)

Three independent reasons it could not see any of them. It only knew DDL
verbs, so DML data destruction (1, 3, 4) was invisible. It matched literal
text, so a statement assembled from a variable (2) was invisible. And it
scanned five hand-listed function bodies, so anything moved one call deeper
was invisible.

So the guarantee is taken behaviourally instead. Snapshot EVERY row of EVERY
table -- values, not counts -- run the migration, and assert that nothing
differs except the exact change the migration is supposed to make. That
catches all four above and every variant of them, including ones nobody has
thought of, because it does not model the attack at all: it models the
promise.

TWO THINGS THAT MAKE THIS GUARD NON-VACUOUS, both of which cost real effort
and are the whole reason it works:

  - Every table is filled first. `_seed_retail` leaves 26 of this database's
    35 tables completely empty, and `DELETE FROM audit_log` against an empty
    `audit_log` changes nothing at all -- a guard built on the stock fixture
    would have gone green on mutation 1 for the same reason the regex did.
    `_fill_every_empty_table` synthesises a row for each, and the fixture
    ASSERTS afterwards that no table is left empty, so the coverage cannot
    quietly rot when a later migration adds a table.

  - Values are compared, not counts. Mutation 4 changes no row count, no
    table, no column and no schema version. Only the numbers move, which is
    the mutation a shop would notice last and care about most.

Run:
    pytest products/retail/tests/retail_v13_additive_only_behavioural_test.py -v
"""
import os
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

PRODUCT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = PRODUCT_DIR / 'backend'
SUITE_ROOT = PRODUCT_DIR.parent.parent
for _p in (str(SUITE_ROOT), str(BACKEND_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

LEGACY_COMPANY_ID = 'd41d8cd98f00b204e9800998ecf8427e'
OWNER_COMPANY_ID = '7c9e6679-7425-40de-944b-e07fc1f90ae7'

# The intended additive change, restated here rather than imported from
# schema.py. Importing RETAIL_UID_TABLES et al. would make this guard agree
# with the migration by construction -- an edit that widened a constant would
# widen the "allowed" set in the very same breath.
EXPECTED_NEW_COLUMNS = {}
for _t in ('branches', 'sales', 'sale_items', 'returns', 'return_items',
           'inventory_movements', 'payments'):
    EXPECTED_NEW_COLUMNS.setdefault(_t, set()).add('uid')
for _t in ('sales', 'returns', 'inventory_movements', 'cash_sessions', 'cash_movements'):
    EXPECTED_NEW_COLUMNS.setdefault(_t, set()).update(
        {'actor_user_uid', 'terminal_id', 'created_at_utc'})
for _t in ('categories', 'products', 'customers', 'suppliers', 'reorder_requests'):
    EXPECTED_NEW_COLUMNS.setdefault(_t, set()).update(
        {'row_version', 'updated_at_utc', 'deleted_at_utc'})

_TMP_DIRS = []


def teardown_module(module):
    for d in _TMP_DIRS:
        shutil.rmtree(d, ignore_errors=True)
    _TMP_DIRS.clear()


# ── the snapshot ────────────────────────────────────────────────────────────

def _user_tables(conn):
    return tuple(r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall())


def _snapshot(conn):
    """{table: {'columns': (...), 'rows': {key: {column: value}}}} for every
    user table in the database.

    Keyed by rowid, which every table in this schema has (none is WITHOUT
    ROWID), because a rowid survives an ALTER TABLE ADD COLUMN unchanged --
    that is exactly the property being asserted, and it means the comparison
    can tell "this row was modified" apart from "this row was deleted and a
    different one added", which a set-of-tuples comparison cannot.
    """
    snap = {}
    for table in _user_tables(conn):
        columns = tuple(r[1] for r in conn.execute(f'PRAGMA table_info("{table}")'))
        rows = {}
        for row in conn.execute(f'SELECT rowid, * FROM "{table}"').fetchall():
            rows[row[0]] = dict(zip(columns, row[1:]))
        snap[table] = {'columns': columns, 'rows': rows}
    return snap


def _assert_same_tables(before, after):
    assert set(after) == set(before), (
        f'tables appeared or disappeared: added={sorted(set(after) - set(before))}, '
        f'removed={sorted(set(before) - set(after))}'
    )


def _assert_nothing_destroyed(before, after, *, exempt_columns=frozenset()):
    """The core comparison. Every failure message names the table, the row and
    the column, because "something changed" is not an actionable report when
    the thing that changed is one number in a shop's sales history."""
    _assert_same_tables(before, after)
    for table in sorted(before):
        old, new = before[table], after[table]
        assert set(new['rows']) == set(old['rows']), (
            f'{table}: rows were added or destroyed -- '
            f'gone={sorted(set(old["rows"]) - set(new["rows"]))[:10]}, '
            f'new={sorted(set(new["rows"]) - set(old["rows"]))[:10]}'
        )
        for column in old['columns']:
            if column in exempt_columns:
                continue
            assert column in new['columns'], f'{table}.{column} was removed'
            for key, old_row in old['rows'].items():
                assert new['rows'][key][column] == old_row[column], (
                    f'{table}.{column} was rewritten on rowid {key}: '
                    f'{old_row[column]!r} -> {new["rows"][key][column]!r}'
                )


# ── the fixture ─────────────────────────────────────────────────────────────

def _synthetic_value(table, column, decl, seq):
    d = (decl or '').upper()
    if 'INT' in d:
        return seq
    if any(k in d for k in ('REAL', 'FLOA', 'DOUB', 'NUMER', 'DEC')):
        return float(seq)
    if 'TIME' in d or 'DATE' in d:
        return '2026-01-02 03:04:05'
    return f'fixture-{table}-{column}-{seq}'


def _fill_every_empty_table(conn):
    """Give every empty table exactly one row, so a DELETE against it is
    visible to the snapshot comparison.

    Foreign keys are switched off for the duration. These rows exist to be
    COUNTED and COMPARED, not to be meaningful business records, and a
    referentially perfect synthetic row for all 26 empty tables would be a
    second, drifting copy of the schema living in a test file. What matters
    is that the row is there and that the migration leaves it exactly as it
    found it.

    A single-column INTEGER PRIMARY KEY is skipped so SQLite assigns the
    rowid; a composite primary key is NOT skipped, because those columns
    (einvoice_settings.company_id and friends) are NOT NULL and the insert
    fails without them.
    """
    conn.execute('PRAGMA foreign_keys=OFF')
    filled, unfillable = [], []
    for table in _user_tables(conn):
        if conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]:
            continue
        info = conn.execute(f'PRAGMA table_info("{table}")').fetchall()
        pk_count = sum(1 for r in info if r[5])
        names, values = [], []
        for i, (_cid, name, decl, _notnull, _dflt, pk) in enumerate(info):
            if pk and pk_count == 1 and (decl or '').upper().strip() == 'INTEGER':
                continue
            names.append(f'"{name}"')
            values.append(_synthetic_value(table, name, decl, i + 1))
        sql = (f'INSERT INTO "{table}" ({",".join(names)}) '
               f'VALUES ({",".join("?" * len(names))})')
        try:
            conn.execute(sql, values)
            filled.append(table)
        except sqlite3.Error as exc:            # pragma: no cover - fixture health
            unfillable.append((table, str(exc)))
    conn.commit()
    conn.execute('PRAGMA foreign_keys=ON')

    still_empty = [t for t in _user_tables(conn)
                   if conn.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0] == 0]
    assert not still_empty, (
        f'the fixture could not populate {still_empty}; a DELETE against an empty table '
        f'changes nothing, so this guard would silently stop covering them. '
        f'Insert failures: {unfillable}'
    )
    return filled


def _v12_install_with_every_table_populated():
    """A REAL schema-v12 retail.db (genuine `init_retail()` with the v13/v14
    steps stubbed and the version pinned back to 12), then every empty table
    filled. Returns (module, connection)."""
    tmp = tempfile.mkdtemp(prefix='aura-retail-additive-')
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

    conn = sqlite3.connect(os.path.join(sch.SUBSYS_DIR, 'retail.db'))
    assert conn.execute('PRAGMA user_version').fetchone()[0] == 12, \
        'fixture did not actually produce a v12 database'
    _fill_every_empty_table(conn)
    return sch, conn


def _current_install_with_every_table_populated():
    """The same thing at the CURRENT schema version -- the state the v14
    rebind actually runs against, since v13 has already applied by then."""
    tmp = tempfile.mkdtemp(prefix='aura-retail-additive-v14-')
    _TMP_DIRS.append(tmp)
    os.environ['AURA_APP_DATA'] = tmp

    import database.schema as sch
    sch.BASE_DIR = os.path.join(tmp, 'database')
    sch.SUBSYS_DIR = os.path.join(sch.BASE_DIR, 'subsystems')
    sch.init_retail()

    conn = sqlite3.connect(os.path.join(sch.SUBSYS_DIR, 'retail.db'))
    _fill_every_empty_table(conn)
    return sch, conn


# ── 1. v13 ──────────────────────────────────────────────────────────────────

def test_v13_destroys_no_row_and_rewrites_no_pre_existing_value_anywhere():
    sch, conn = _v12_install_with_every_table_populated()
    before = _snapshot(conn)
    assert len(before) >= 30, f'only {len(before)} tables snapshotted -- fixture is too thin'
    assert all(s['rows'] for s in before.values()), 'a table was snapshotted empty'

    sch._migrate_add_identity_and_attribution_columns(conn)
    conn.commit()

    after = _snapshot(conn)
    _assert_nothing_destroyed(before, after)
    assert conn.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
    conn.close()


def test_v13_adds_exactly_the_columns_it_is_supposed_to_and_no_others():
    """The other half of "additive only": the change must not merely be
    non-destructive, it must be the change that was reserved. A migration
    quietly adding a column nobody agreed to is how two branches' schema
    versions collide -- the exact failure ROADMAP.md's reservation ledger
    exists to prevent."""
    sch, conn = _v12_install_with_every_table_populated()
    before = _snapshot(conn)

    sch._migrate_add_identity_and_attribution_columns(conn)
    conn.commit()
    after = _snapshot(conn)
    _assert_same_tables(before, after)

    for table in sorted(before):
        added = set(after[table]['columns']) - set(before[table]['columns'])
        expected = EXPECTED_NEW_COLUMNS.get(table, set())
        assert added == expected, (
            f'{table}: v13 added {sorted(added)}, expected {sorted(expected)}'
        )
    conn.close()


def test_v13_populates_only_uid_and_row_version_and_fabricates_nothing_else():
    """Behavioural restatement of the migration's own rule about history:
    `uid` is minted (it is this device's own wire identity, not a claim about
    the past), `row_version` is defaulted to 1 by the ADD COLUMN itself, and
    everything else stays NULL. A backfilled `created_at_utc` would be a
    fabricated instant, and a backfilled `terminal_id` or `actor_user_uid`
    would be this device claiming it rang a sale it cannot prove it rang."""
    sch, conn = _v12_install_with_every_table_populated()
    before = _snapshot(conn)

    sch._migrate_add_identity_and_attribution_columns(conn)
    conn.commit()
    after = _snapshot(conn)
    _assert_same_tables(before, after)

    for table in sorted(before):
        added = set(after[table]['columns']) - set(before[table]['columns'])
        for column in sorted(added):
            values = [row[column] for row in after[table]['rows'].values()]
            assert values, f'{table} lost its rows'
            if column == 'uid':
                assert all(v for v in values), f'{table}.uid was left blank on some row'
            elif column == 'row_version':
                assert all(v == 1 for v in values), f'{table}.row_version is not 1 everywhere'
            else:
                assert all(v is None for v in values), (
                    f'{table}.{column} was backfilled with a guessed value: '
                    f'{[v for v in values if v is not None][:3]}'
                )
    conn.close()


# ── 2. v14 ──────────────────────────────────────────────────────────────────

def test_the_v14_rebind_touches_company_id_and_absolutely_nothing_else():
    """`rebind_company_id` verifies its own row counts on the scoped tables it
    moves. That check is real, and it is blind in two directions this one is
    not: it never looks at the UNSCOPED tables at all (`sale_items`,
    `return_items`, `purchase_order_items`, `cash_movements`, `sync_outbox`),
    and within a scoped table it counts rows rather than reading them, so
    every column other than `company_id` could be rewritten underneath it
    without the count ever moving."""
    from database.schema import company_scoped_tables, rebind_company_id

    _sch, conn = _current_install_with_every_table_populated()
    scoped = company_scoped_tables(conn)
    for table in scoped:
        conn.execute(f'UPDATE "{table}" SET company_id=?', (LEGACY_COMPANY_ID,))
    conn.commit()

    before = _snapshot(conn)
    unscoped = [t for t in before if t not in scoped]
    assert unscoped, 'no unscoped tables in the snapshot -- the blind spot is not covered'

    result = rebind_company_id(conn, OWNER_COMPANY_ID)
    conn.commit()
    assert result['status'] == 'rebound', result

    after = _snapshot(conn)
    _assert_nothing_destroyed(before, after, exempt_columns={'company_id'})

    # ...and the one exempted column moved exactly as promised: every LEGACY
    # value is now the OWNER value, and nothing else about it changed.
    for table in sorted(before):
        if 'company_id' not in before[table]['columns']:
            continue
        for key, old_row in before[table]['rows'].items():
            expected = (OWNER_COMPANY_ID if old_row['company_id'] == LEGACY_COMPANY_ID
                        else old_row['company_id'])
            assert after[table]['rows'][key]['company_id'] == expected, (
                f'{table} rowid {key}: company_id went {old_row["company_id"]!r} -> '
                f'{after[table]["rows"][key]["company_id"]!r}, expected {expected!r}'
            )
    assert conn.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
    conn.close()


def test_a_refused_rebind_leaves_every_row_of_every_table_byte_identical():
    """The multi-tenant refusal has to be a genuine no-op, not a partial
    write that happened to raise. Same whole-database comparison, applied to
    the failure path -- because "it raised" and "it changed nothing" are
    different claims and only one of them was ever checked."""
    from database.schema import CompanyRebindError, company_scoped_tables, rebind_company_id

    _sch, conn = _current_install_with_every_table_populated()
    scoped = company_scoped_tables(conn)
    for table in scoped:
        conn.execute(f'UPDATE "{table}" SET company_id=?', (LEGACY_COMPANY_ID,))
    conn.execute('UPDATE sales SET company_id=?', ('ffffffffffffffffffffffffffffffff',))
    conn.commit()

    before = _snapshot(conn)
    raised = False
    try:
        rebind_company_id(conn, OWNER_COMPANY_ID)
    except CompanyRebindError:
        raised = True
    assert raised, 'the two-tenant refusal did not fire -- this test proves nothing'

    _assert_nothing_destroyed(before, _snapshot(conn))
    conn.close()
