"""
Aura Retail -- schema v13: the duplicate-uid repair must TERMINATE, and must
move only the rows it is there to move.

See database/schema.py::_repair_duplicate_uids.

WHY THIS FILE EXISTS SEPARATELY FROM retail_v13_uid_index_hardening_test.py.
That file asserts the repair's OUTCOME -- no duplicate survives, no row is
lost, the surviving uids are canonical uuid4. Every one of those assertions is
correct and none of them can be reached if the repair never returns. A
non-terminating loop is not a failing test, it is a hanging one: pytest has no
timeout of its own, products/run_all_tests.py runs each file with
`subprocess.run(...)` and no `timeout=`, and CI reports it as a job that never
finished rather than as a suite that failed. The defect that shipped was
exactly that shape -- the repair's row-selection predicate matched EVERY row
with a non-null uid, so it reissued the whole table, re-queried, matched the
whole table again, and never emptied its work queue.

The consequence is strictly worse than the F1 defect the repair was written to
fix. F1 raised sqlite3.IntegrityError out of `init_retail()`: fatal, but loud
and with a stack trace naming the statement. A repair that spins forever makes
`app.py::init_app()` never return at all -- a till that shows a splash screen
and nothing else, no error, no log line, no traceback, while it rewrites every
`uid` in the database several times a second. And because `ALTER TABLE ... ADD
COLUMN uid` has already committed by then, that state is reached again on
every subsequent launch.

So the property under test here is TERMINATION AND BOUNDED WORK, not outcome.
It is enforced with a connection wrapper that counts write statements and
raises once the count passes a bound no correct implementation can reach, so
the defect surfaces as a named assertion failure in under a second instead of
as a hung job.

The second property, tested alongside, is the one that makes the bound
meaningful: the repair must reissue ONLY rows that actually collide, keeping
the lowest rowid's value. An implementation that terminates by regenerating
every uid in the table would satisfy "no duplicates survive" and would silently
invalidate every uid a peer device or Owner had already seen -- and on a table
that is entirely free of duplicates, which is every shipped install, it would
rewrite the whole wire identity space for nothing.

Run:
    pytest products/retail/tests/retail_v13_uid_repair_termination_test.py -v
"""
import sqlite3
import sys
import uuid
from pathlib import Path

import pytest

PRODUCT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = PRODUCT_DIR / 'backend'
SUITE_ROOT = PRODUCT_DIR.parent.parent
for _p in (str(SUITE_ROOT), str(BACKEND_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import database.schema as sch  # noqa: E402


class WriteBudgetExceeded(AssertionError):
    """Raised by BudgetedConnection when a migration step issues more write
    statements than any terminating implementation could need.

    An AssertionError subclass on purpose: this is a test-harness verdict about
    the code under test, not an error condition the production code should ever
    be given the chance to catch and interpret.
    """


class BudgetedConnection:
    """A real sqlite3 connection that refuses to execute more than `budget`
    write statements.

    Everything is delegated to the genuine connection -- same database, same
    SQL, same results -- so nothing about the behaviour under test is
    simulated. The wrapper only counts, and only counts statements that WRITE
    (INSERT/UPDATE/DELETE); reads are unlimited, because a repair that
    re-queries its own work each pass is correct and deliberate (see
    `_repair_duplicate_uids`' chunking rationale) and should not be penalised
    for it.

    `budget` is chosen per test to sit far above what a correct implementation
    needs and far below unbounded. The number is asserted against in the
    failure message so a future change that legitimately needs more writes
    fails with an explanation rather than a bare count.
    """

    _WRITE_VERBS = ('INSERT', 'UPDATE', 'DELETE', 'REPLACE')

    def __init__(self, conn, budget):
        self._conn = conn
        self._budget = budget
        self.writes = 0

    def _charge(self, sql, n=1):
        if sql.lstrip().upper().startswith(self._WRITE_VERBS):
            self.writes += n
            if self.writes > self._budget:
                raise WriteBudgetExceeded(
                    f'the migration issued more than {self._budget} write statements and '
                    f'showed no sign of stopping (last: {sql.strip()[:120]!r}). A repair '
                    f'that re-queries its own work each pass MUST shrink that work: if the '
                    f'rows it selects still match its own predicate after it has rewritten '
                    f'them, the loop never empties and init_retail() never returns.'
                )

    def execute(self, sql, *args, **kwargs):
        self._charge(sql)
        return self._conn.execute(sql, *args, **kwargs)

    def executemany(self, sql, seq):
        rows = list(seq)
        self._charge(sql, max(len(rows), 1))
        return self._conn.executemany(sql, rows)

    def __getattr__(self, name):
        return getattr(self._conn, name)


# ── fixtures ────────────────────────────────────────────────────────────────

def _table_with_uids(row_count, duplicate_groups=()):
    """An in-memory table already carrying a `uid` column and `row_count` rows.

    This is the state a HALF-APPLIED v13 leaves behind and the state every
    RETRY of v13 starts from: `ALTER TABLE ADD COLUMN uid` and the backfill
    have committed, the unique index has not been established yet. It is also
    the state the repair sees on the normal path, because the backfill above it
    in `_migrate_add_identity_and_attribution_columns` fills every uid in
    before `_ensure_unique_uid_index` is called. So "every row has a distinct
    uid already" is not an edge case here -- it is what the repair is handed on
    every launch of every shipped install.

    `duplicate_groups` is a list of rowid tuples that should be forced to share
    one uid, e.g. [(1, 4)] or [(1, 4, 7)].
    """
    conn = sqlite3.connect(':memory:')
    conn.execute('CREATE TABLE sales (id INTEGER PRIMARY KEY AUTOINCREMENT, uid TEXT)')
    conn.executemany(
        'INSERT INTO sales (uid) VALUES (?)',
        [(str(uuid.uuid4()),) for _ in range(row_count)],
    )
    for group in duplicate_groups:
        shared = conn.execute(
            'SELECT uid FROM sales WHERE rowid=?', (group[0],)
        ).fetchone()[0]
        for rid in group[1:]:
            conn.execute('UPDATE sales SET uid=? WHERE rowid=?', (shared, rid))
    conn.commit()
    return conn


def _uids_by_rowid(conn):
    return dict(conn.execute('SELECT rowid, uid FROM sales').fetchall())


def _duplicate_count(conn, table='sales'):
    return conn.execute(
        f'SELECT COUNT(*) FROM (SELECT uid FROM "{table}" WHERE uid IS NOT NULL '
        f'GROUP BY uid HAVING COUNT(*) > 1)'
    ).fetchone()[0]


# ── termination ─────────────────────────────────────────────────────────────

def test_repair_writes_nothing_at_all_when_there_are_no_duplicates():
    """The path every shipped install takes, and the one the defect broke worst.

    No writer in this product has ever produced a duplicate uid -- they all use
    uuid4, and the `sale_items` uid is generated inside the per-line loop
    rather than once outside it. So on every real install the repair is handed
    a table whose uids are already distinct and its correct output is: nothing.

    A budget of zero writes is the honest bound here, not a strict one. There
    is no row for a correct repair to touch, so any write at all is a row it
    should not have touched.
    """
    conn = _table_with_uids(50)
    before = _uids_by_rowid(conn)

    budgeted = BudgetedConnection(conn, budget=0)
    sch._repair_duplicate_uids(budgeted, 'sales')

    assert _uids_by_rowid(conn) == before, (
        'the repair rewrote uids on a table that had no duplicates -- every uid a peer '
        'device or Owner had already seen for these rows is now wrong'
    )


def test_repair_terminates_and_moves_only_the_colliding_row():
    """One duplicate pair among fifty rows: exactly one row may move.

    The budget (8 writes) is deliberately larger than the one write a correct
    implementation needs, so this fails on non-termination rather than on an
    off-by-one, and smaller than the fifty a "reissue everything" implementation
    would spend on its FIRST pass alone.
    """
    conn = _table_with_uids(50, duplicate_groups=[(3, 41)])
    before = _uids_by_rowid(conn)
    assert _duplicate_count(conn) == 1, 'fixture did not actually plant a duplicate'

    budgeted = BudgetedConnection(conn, budget=8)
    sch._repair_duplicate_uids(budgeted, 'sales')

    after = _uids_by_rowid(conn)
    assert _duplicate_count(conn) == 0, 'the duplicate survived the repair'
    moved = {rid for rid, uid in after.items() if before[rid] != uid}
    assert moved == {41}, (
        f'rows {sorted(moved)} were reissued; only rowid 41 collides with an earlier row. '
        'The lowest rowid keeps its value on purpose -- if part of this table has already '
        'been shared with a peer, the original row is the one likelier to have been seen '
        'under that uid.'
    )
    assert uuid.UUID(after[41]).version == 4


def test_repair_keeps_the_lowest_rowid_across_a_three_way_collision():
    """Three rows sharing one uid -- a restored range overlapping live rows,
    not just a single stray. Two must move, the first must not."""
    conn = _table_with_uids(20, duplicate_groups=[(2, 9, 17)])
    before = _uids_by_rowid(conn)

    budgeted = BudgetedConnection(conn, budget=8)
    sch._repair_duplicate_uids(budgeted, 'sales')

    after = _uids_by_rowid(conn)
    assert _duplicate_count(conn) == 0
    moved = {rid for rid, uid in after.items() if before[rid] != uid}
    assert moved == {9, 17}, f'expected rowids 9 and 17 to move, got {sorted(moved)}'
    assert after[2] == before[2]


def test_repair_terminates_when_every_single_row_collides():
    """The pathological input -- an entire table restored on top of itself, so
    every uid appears exactly twice.

    This is the case where "reissue only what collides" is still most of the
    table, and it is the one where a predicate that cannot shrink its own work
    queue is hardest to distinguish from a slow-but-correct one. 40 rows, 20
    duplicate pairs: a correct repair spends 20 writes.
    """
    pairs = [(i, i + 20) for i in range(1, 21)]
    conn = _table_with_uids(40, duplicate_groups=pairs)
    assert _duplicate_count(conn) == 20

    budgeted = BudgetedConnection(conn, budget=25)
    sch._repair_duplicate_uids(budgeted, 'sales')

    assert _duplicate_count(conn) == 0
    assert conn.execute('SELECT COUNT(*) FROM sales WHERE uid IS NULL').fetchone()[0] == 0
    assert conn.execute('SELECT COUNT(*) FROM sales').fetchone()[0] == 40


def test_repair_shrinks_its_queue_across_several_chunks_not_just_within_one():
    """The multi-pass path, which nothing else in this suite reaches.

    `_UID_BACKFILL_CHUNK` is 5000, and no fixture anywhere near this repo has
    5000 duplicate rows, so every other test in this file completes the repair
    in a SINGLE pass. A single pass proves the predicate selects the right
    rows; it does not prove the loop is a loop. The defect was specifically an
    inability to shrink the work queue BETWEEN passes -- the one property a
    one-pass test cannot see, and the one that decides whether `init_retail()`
    returns.

    So the chunk is turned down to 3 for the duration, which is the honest way
    to reach the branch: the constant exists to bound memory on a till machine,
    not to change behaviour, and a repair that is only correct at chunk=5000 is
    not correct.

    18 duplicate rows must move in 6 passes of 3. The budget allows 18 writes
    and not one more, so a loop that revisits rows it has already reissued
    fails here even though it would eventually terminate.
    """
    pairs = [(i, i + 18) for i in range(1, 19)]
    conn = _table_with_uids(36, duplicate_groups=pairs)
    before = _uids_by_rowid(conn)
    assert _duplicate_count(conn) == 18

    original_chunk = sch._UID_BACKFILL_CHUNK
    sch._UID_BACKFILL_CHUNK = 3
    try:
        budgeted = BudgetedConnection(conn, budget=18)
        sch._repair_duplicate_uids(budgeted, 'sales')
    finally:
        sch._UID_BACKFILL_CHUNK = original_chunk

    assert budgeted.writes == 18, (
        f'the repair spent {budgeted.writes} writes on 18 colliding rows; a pass that does '
        'not remove its rows from its own next query is the non-terminating shape'
    )
    after = _uids_by_rowid(conn)
    assert _duplicate_count(conn) == 0
    moved = {rid for rid, uid in after.items() if before[rid] != uid}
    assert moved == {i + 18 for i in range(1, 19)}, (
        f'the wrong rows moved: {sorted(moved)}'
    )


def test_repair_ignores_rows_that_have_no_uid_yet():
    """`WHERE uid IS NOT NULL` is load-bearing in both directions.

    NULL is a legal, expected state between the ADD COLUMN and the backfill,
    and NULLs never collide with each other -- SQL equality on NULL is unknown,
    and the partial unique index this repair precedes excludes them explicitly.
    A repair that treated two NULL uids as a collision would spend a write on
    every un-backfilled row, which on `sale_items` is the whole table.
    """
    conn = _table_with_uids(10)
    conn.execute('UPDATE sales SET uid=NULL WHERE rowid IN (2,4,6)')
    conn.commit()
    before = _uids_by_rowid(conn)

    budgeted = BudgetedConnection(conn, budget=0)
    sch._repair_duplicate_uids(budgeted, 'sales')

    assert _uids_by_rowid(conn) == before
    assert conn.execute(
        'SELECT COUNT(*) FROM sales WHERE uid IS NULL'
    ).fetchone()[0] == 3, 'the repair backfilled NULLs; that is the backfill loop\'s job, not this one'


# ── the whole v13 step, on the fixture shape that actually hung ─────────────

def test_v13_step_terminates_on_the_minimal_hand_built_fixture_shape():
    """End-to-end on the exact fixture that wedged the suite.

    `retail_category_delete_fk_sync_test.py` hand-builds a THREE-table schema
    (categories / products / inventory_movements) and calls
    `_migrate_retail_schema` against it directly -- a pattern
    `_migrate_add_identity_and_attribution_columns`' docstring names and
    supports. `inventory_movements` is one of the seven RETAIL_UID_TABLES, so
    that fixture reaches the repair with real rows in it, and the
    non-terminating predicate hung that file forever. Every other retail test
    file after it in alphabetical order therefore never ran at all.

    Asserted through the whole step, not the helper, because the helper is
    reachable seven times per migration and a bound that only holds in
    isolation is not a bound.
    """
    conn = sqlite3.connect(':memory:')
    conn.executescript(
        'CREATE TABLE categories (id TEXT PRIMARY KEY, company_id INTEGER, name TEXT);'
        'CREATE TABLE products (id TEXT PRIMARY KEY, company_id INTEGER, category_id TEXT);'
        'CREATE TABLE inventory_movements ('
        '  id INTEGER PRIMARY KEY AUTOINCREMENT, company_id INTEGER, product_id TEXT,'
        '  quantity INTEGER, created_at TEXT);'
    )
    conn.executemany(
        'INSERT INTO inventory_movements (company_id, product_id, quantity) VALUES (1,?,?)',
        [(f'p{i}', i) for i in range(30)],
    )
    conn.commit()

    # A generous ceiling: the backfill legitimately writes one uid per row
    # (30), and the repair must add nothing on top of that.
    budgeted = BudgetedConnection(conn, budget=40)
    sch._migrate_add_identity_and_attribution_columns(budgeted)
    conn.commit()

    assert conn.execute(
        'SELECT COUNT(*) FROM inventory_movements WHERE uid IS NULL'
    ).fetchone()[0] == 0
    assert _duplicate_count(conn, 'inventory_movements') == 0


def test_v13_step_is_a_no_op_on_a_second_run_over_the_same_database():
    """Idempotence, measured in writes rather than asserted in prose.

    `ensure_schema_version` re-runs the entire chain from the top after any
    failure, so a second run is the NORMAL case, not a rare one. The migration
    claims to be a clean no-op then. If the repair rewrote uids on a table with
    no duplicates, that claim was false in the most expensive possible way: the
    wire identity of every row changed on a retry that was supposed to change
    nothing.
    """
    conn = sqlite3.connect(':memory:')
    conn.executescript(
        'CREATE TABLE sales (id INTEGER PRIMARY KEY AUTOINCREMENT, company_id INTEGER,'
        ' cashier TEXT, created_at TEXT);'
    )
    conn.executemany(
        'INSERT INTO sales (company_id, cashier) VALUES (1,?)',
        [(f'c{i}',) for i in range(25)],
    )
    conn.commit()

    # The FIRST run is budgeted too. Handing this call a bare connection would
    # make a non-terminating repair hang the test instead of failing it, and a
    # hung file is exactly the failure mode this whole file exists to convert
    # into a readable assertion. 25 rows -> 25 backfill writes, nothing else.
    sch._migrate_add_identity_and_attribution_columns(BudgetedConnection(conn, budget=30))
    conn.commit()
    first = _uids_by_rowid(conn)

    budgeted = BudgetedConnection(conn, budget=0)
    sch._migrate_add_identity_and_attribution_columns(budgeted)
    conn.commit()

    assert _uids_by_rowid(conn) == first, (
        're-running v13 over an already-migrated database changed uids -- a retry after an '
        'unrelated failure would silently invalidate every identity already on the wire'
    )
