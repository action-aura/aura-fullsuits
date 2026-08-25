"""
Aura Retail -- schema v14's half of Defect 3 (launch-readiness Phase 5
verification, MEDIUM): `database/schema.py::rebind_company_id` used to count
its before/after row snapshot BEFORE taking `BEGIN IMMEDIATE`'s write lock.

THIS FILE EXISTS BECAUSE THE RETAIL HALF SHIPPED WITHOUT A TEST. The identity
half of the same fix is covered by commercial_runtime/identity/tests/
test_registry_v4_concurrent_insert_race.py; `schema.py`'s sibling edit --
character-for-character the same defect, in the database that actually holds
the shop's money and history -- had none. A fix present in two places and
proven in one is proven in one.

THE DEFECT. `BEGIN IMMEDIATE` takes the write lock up front; that is the
entire reason it is used instead of a deferred `BEGIN`. Taking that lock and
then verifying against a snapshot counted BEFORE the lock existed throws the
guarantee away. A single concurrent INSERT landing in the gap -- a till
ringing up a sale, which on a real shop is not an edge case but the normal
state of the afternoon -- changes what the UPDATE actually moves, so the
in-transaction verification compares a stale "before" against a real "after"
and raises `CompanyRebindError` on a rebind that was completely fine.

THAT FALSE POSITIVE IS NOT HARMLESS. It is a SAFE failure in isolation (the
transaction rolls back, retail.db is untouched), but it is precisely how a
busy till reaches Defect 2's split state: identity's rebind has already
committed, retail's is refused by its own count check, and the process is now
serving a shop whose entire history is filtered on a tenant key that owns
zero rows. The activation seam's 503 (see
retail_registry_v4_activation_split_state_test.py) is the net under that
fall; this is the fix that keeps it from happening in the first place.

MUTATION PROOF. Moving the `before_old`/`before_new`/`before_total` block in
`database/schema.py::rebind_company_id` back OUT of the `try:` -- i.e. back
to before `BEGIN IMMEDIATE`, exactly what shipped before Defect 3 was fixed
-- makes `test_a_concurrent_insert_during_the_precount_cannot_trip_retails_
verification` fail with the false positive itself, verbatim:

    database.schema.CompanyRebindError: company_id rebind failed its own
    count check on 'products': 0 row(s) left on the old key, 14 on the new
    (expected 13), 14 rows total (expected 14). Rolled back -- the tenant
    key is unchanged.

The companion `test_the_precount_block_is_inside_the_transaction_not_merely_
present` fails under the same mutation with its own ordering message, so the
protection here does not rest on a single fixture continuing to be able to
win a race.

Run (ONE TEST FILE PER PYTEST PROCESS -- see products/run_all_tests.py):
    pytest products/retail/tests/retail_registry_v4_rebind_precount_race_test.py -v
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

LEGACY_COMPANY_ID = 'd41d8cd98f00b204e9800998ecf8427e'   # a real md5 shape
OWNER_COMPANY_ID = '7c9e6679-7425-40de-944b-e07fc1f90ae7'  # a real Owner licence uuid

_TMP_DIRS = []


def teardown_module(module):
    for d in _TMP_DIRS:
        shutil.rmtree(d, ignore_errors=True)
    _TMP_DIRS.clear()


def _fresh_install():
    """A real retail.db (full v0 -> current migration chain, real seed data),
    with every scoped row moved onto a legacy md5-shaped `company_id` -- the
    same `_fresh_install` shape retail_v14_company_rebind_migration_test.py
    uses, so this file exercises the identical starting state the shipped
    v14 migration does.

    Returns `(db_path, seeded_product_count)`.
    """
    tmp = tempfile.mkdtemp(prefix='aura-retail-v14-precount-race-')
    _TMP_DIRS.append(tmp)
    os.environ['AURA_APP_DATA'] = tmp

    import database.schema as sch
    sch.BASE_DIR = os.path.join(tmp, 'database')
    sch.SUBSYS_DIR = os.path.join(sch.BASE_DIR, 'subsystems')
    sch.init_retail()

    db_path = os.path.join(sch.SUBSYS_DIR, 'retail.db')
    conn = sqlite3.connect(db_path)
    for table in sch.company_scoped_tables(conn):
        conn.execute(f'UPDATE "{table}" SET company_id=?', (LEGACY_COMPANY_ID,))
    conn.execute(
        'INSERT INTO products (company_id, sku, name, sell_price) VALUES (?,?,?,?)',
        (LEGACY_COMPANY_ID, 'SKU-PRECOUNT-RACE', 'Precount Race Widget', 9.99),
    )
    conn.commit()
    seeded = conn.execute(
        'SELECT COUNT(*) FROM products WHERE company_id=?', (LEGACY_COMPANY_ID,)
    ).fetchone()[0]
    conn.close()
    return db_path, seeded


class _RaceConnection(sqlite3.Connection):
    """A `sqlite3.Connection` subclass -- not a monkeypatched attribute,
    since `sqlite3.Connection.execute` is read-only on the C type; the same
    technique commercial_runtime/identity/tests/test_registry_v4_concurrent_
    insert_race.py and retail_v14_company_rebind_migration_test.py's own
    failure injectors use -- that fires a SECOND, real, independent
    connection's INSERT at the EXACT instant `rebind_company_id`'s FIRST
    per-table `before_old` count runs.

    Fires only ONCE (`fired`): the verification loop later in
    `rebind_company_id` issues the identical SQL text again (the "stranded"
    re-read), and without the guard this hook would fire there too and
    attempt a second concurrent write mid-verification, which is not the
    scenario under test.

    Matches `products`' OWN count, deliberately, rather than "whichever
    count comes first". `company_scoped_tables` discovers its list from the
    live schema and sorts it alphabetically, so the first count is some
    other table -- and firing there lets `products`' own `before_old` be
    taken AFTER the raced-in row, which makes the snapshot self-consistent
    and the verification pass. The defect would still be real (the write
    landed while the lock was supposedly held) but the test would only be
    able to observe the lock gap, not the false-positive
    `CompanyRebindError` that gap actually causes. Firing on the count for
    the very table the concurrent write targets is what reproduces the
    consequence rather than only its precondition. Confirmed by mutation,
    not by reading: the first draft matched on shape and, with Defect 3
    reintroduced, `rebind_company_id` returned 'rebound' perfectly happily.
    """
    db_path = None
    watch_sql = 'SELECT COUNT(*) FROM "products" WHERE company_id=?'
    fired = False
    concurrent_insert_error = None
    concurrent_insert_succeeded = False

    def execute(self, sql, *args):
        # Run the REAL query FIRST. Python's sqlite3 steps a SELECT to
        # completion (or to its first-row buffer) inside `execute()` itself
        # rather than lazily at `.fetchone()`, so the count this returns is
        # already fixed at THIS point. The concurrent write is fired AFTER,
        # simulating it landing in the gap immediately following the count --
        # the exact gap Defect 3 is about. Firing it BEFORE would let the
        # very count under test already see it, proving nothing.
        cur = sqlite3.Connection.execute(self, sql, *args)
        if not _RaceConnection.fired and sql == _RaceConnection.watch_sql:
            _RaceConnection.fired = True
            second = sqlite3.connect(_RaceConnection.db_path, timeout=0.5)
            try:
                second.execute(
                    'INSERT INTO products (company_id, sku, name, sell_price) VALUES (?,?,?,?)',
                    (LEGACY_COMPANY_ID, 'SKU-RACED-IN', 'Sale Rung Up Mid-Rebind', 1.00),
                )
                second.commit()
                _RaceConnection.concurrent_insert_succeeded = True
            except sqlite3.OperationalError as exc:
                _RaceConnection.concurrent_insert_error = exc
            finally:
                second.close()
        return cur


def test_a_concurrent_insert_during_the_precount_cannot_trip_retails_verification():
    from database.schema import rebind_company_id

    db_path, seeded = _fresh_install()
    assert seeded >= 1, 'fixture bug: no products seeded on the legacy key'

    _RaceConnection.db_path = db_path
    _RaceConnection.fired = False
    _RaceConnection.concurrent_insert_error = None
    _RaceConnection.concurrent_insert_succeeded = False

    race_conn = sqlite3.connect(db_path, factory=_RaceConnection, timeout=5)
    try:
        result = rebind_company_id(race_conn, OWNER_COMPANY_ID)
    finally:
        race_conn.close()

    assert _RaceConnection.fired, (
        f'fixture bug: the hook never saw {_RaceConnection.watch_sql!r} -- this test '
        f'proved nothing about the race window'
    )

    # ── THE ASSERTION ────────────────────────────────────────────────────
    # With the pre-counts read AFTER BEGIN IMMEDIATE, the write lock is
    # already held by the time the first count runs, so the second
    # connection's write CANNOT land and cannot corrupt the snapshot the
    # in-transaction verification later checks against.
    assert not _RaceConnection.concurrent_insert_succeeded, (
        'a concurrent INSERT from a second, independent connection SUCCEEDED while the '
        "count that feeds rebind_company_id's verification was being read -- the write "
        'lock is not actually held at that point, which is Defect 3 reopened on the '
        "retail side, in the database holding the shop's money"
    )
    assert _RaceConnection.concurrent_insert_error is not None, (
        'the concurrent write neither succeeded nor raised -- something about this '
        'fixture is not exercising the race at all'
    )
    assert 'database is locked' in str(_RaceConnection.concurrent_insert_error).lower(), \
        _RaceConnection.concurrent_insert_error

    # And the rebind itself completed correctly -- not merely "did not
    # crash". A `CompanyRebindError` here IS the defect: the false positive
    # that lands a busy till in the split state.
    assert result['status'] == 'rebound', result
    assert result['old_company_id'] == LEGACY_COMPANY_ID, result

    conn = sqlite3.connect(db_path)
    assert conn.execute(
        'SELECT COUNT(*) FROM products WHERE company_id=?', (LEGACY_COMPANY_ID,)
    ).fetchone()[0] == 0, 'rows stranded on the old tenant key'
    assert conn.execute(
        'SELECT COUNT(*) FROM products WHERE company_id=?', (OWNER_COMPANY_ID,)
    ).fetchone()[0] == seeded
    # The raced-in row was rejected outright by SQLite's own locking, not
    # merely delayed past the end of this test -- it exists under NEITHER
    # tenant key.
    assert conn.execute(
        "SELECT COUNT(*) FROM products WHERE sku='SKU-RACED-IN'"
    ).fetchone()[0] == 0
    assert conn.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
    conn.close()


def test_the_precount_block_is_inside_the_transaction_not_merely_present():
    """A structural companion to the behavioural test above, guarding the
    one way that test could silently stop testing anything.

    The behavioural proof depends on SQLite refusing the concurrent write.
    If some future change made `rebind_company_id` take its lock later, or
    not at all, the concurrent INSERT would start succeeding -- and the
    behavioural test would catch that. But if a change instead moved the
    counts back OUT while leaving the lock where it is, the FIRST count the
    hook fires on would be the pre-lock one, and this file's protection
    would depend entirely on that one ordering still holding.

    So this asserts the ordering directly, from the source: inside
    `rebind_company_id`, the statement that TAKES the lock must appear
    BEFORE the first `before_old` assignment. Read from the real function's
    own source via `inspect.getsource`, not from a hardcoded line number, so
    it follows the function wherever it moves in the file.

    It matches the EXECUTABLE form `conn.execute('BEGIN IMMEDIATE')`, not
    the bare string `BEGIN IMMEDIATE`. The first draft of this test matched
    the bare string and was worthless: this function's own docstring
    explains at length why it "runs inside an explicit `BEGIN IMMEDIATE`",
    and that prose sits at the very top of the source, so the match always
    landed there and the ordering assertion could never fail no matter where
    the counts actually were. Verified by mutation rather than by reading --
    the first version passed with the defect reintroduced.
    """
    import inspect

    from database.schema import rebind_company_id

    src = inspect.getsource(rebind_company_id)
    begin_at = src.find("conn.execute('BEGIN IMMEDIATE')")
    precount_at = src.find('before_old = {')
    assert begin_at != -1, (
        "rebind_company_id no longer contains a literal conn.execute('BEGIN IMMEDIATE') "
        'statement -- this ordering guard can no longer see the lock it is guarding, so '
        'it must be re-pointed rather than left silently passing'
    )
    assert precount_at != -1, "rebind_company_id no longer has a `before_old = {` snapshot"
    assert begin_at < precount_at, (
        'rebind_company_id counts its before-snapshot BEFORE taking BEGIN IMMEDIATE\'s '
        'write lock. That is Defect 3: the snapshot the in-transaction verification '
        'checks against is read outside the lock, so an ordinary concurrent sale can '
        'trip a correct rebind into a spurious CompanyRebindError -- which is how a busy '
        'till reaches the split state the activation seam then has to serve 503s for.'
    )
