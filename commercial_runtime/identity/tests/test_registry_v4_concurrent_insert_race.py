"""Registry v4 -- Defect 3 (launch-readiness Phase 5 verification, MEDIUM):
`rebind_company_id` used to count its before/after row snapshot BEFORE
taking `BEGIN IMMEDIATE`'s write lock, so a single concurrent INSERT landing
in that gap tripped the in-transaction verification into a SAFE but
SPURIOUS `CompanyRebindError` -- exactly how a busy till could reach
Defect 2's split state. See company_rebind.py::rebind_company_id's inline
comment at the count block for the full mechanism.

This test proves the fix closes the window: with the pre-counts read AFTER
`BEGIN IMMEDIATE` (this file's current, correct state), a second REAL
sqlite3 connection to the SAME on-disk database, attempting a write at the
EXACT instant the count query runs, can no longer land -- the write lock is
already held by the time the count is taken, so the second connection's own
write blocks and fails with `database is locked` instead of silently
succeeding and corrupting the snapshot the verification later checks
against.

MUTATION PROOF (performed during the task that added this file, see the
task's own instructions): moving the before_old/before_new/before_total
block back out of the `try:` block -- i.e. back to BEFORE `BEGIN IMMEDIATE`,
matching what shipped before Defect 3's fix -- makes `test_a_concurrent_
insert_landing_during_the_count_cannot_corrupt_the_snapshot` fail
differently: the second connection's INSERT now SUCCEEDS (no lock exists
yet), and `rebind_company_id` itself then raises `CompanyRebindError` with
the exact text `"company_id rebind failed its own count check on 'users'"`
-- the false-positive failure this fix exists to prevent, reproduced
verbatim rather than merely asserted.

Run (ONE FILE PER PYTEST PROCESS -- see products/run_all_tests.py):
    pytest commercial_runtime/identity/tests/test_registry_v4_concurrent_insert_race.py -v
"""
import os
import shutil
import sqlite3
import tempfile
import uuid

from commercial_runtime.identity import registry_db
from commercial_runtime.identity.company_rebind import rebind_company_id

LEGACY_COMPANY_ID = 'd41d8cd98f00b204e9800998ecf8427e'   # a real md5 shape
OWNER_COMPANY_ID = '7c9e6679-7425-40de-944b-e07fc1f90ae7'  # a real Owner licence uuid

_TMP_DIRS = []


def teardown_module(module):
    for d in _TMP_DIRS:
        shutil.rmtree(d, ignore_errors=True)
    _TMP_DIRS.clear()


def _fresh_registry(prefix='aura-registry-v4-race-'):
    """A real registry.db (full v0->v4 chain), seeded with exactly ONE user
    under the legacy tenant key -- deliberately minimal, so `result['rows']`
    below is unambiguous about whether the race's concurrent INSERT landed."""
    tmp = tempfile.mkdtemp(prefix=prefix)
    _TMP_DIRS.append(tmp)
    os.environ['AURA_APP_DATA'] = tmp
    registry_db._db_dir = os.path.join(tmp, 'database')
    registry_db.DB_PATH = os.path.join(registry_db._db_dir, 'registry.db')
    registry_db.init_registry_db()

    conn = sqlite3.connect(registry_db.DB_PATH)
    conn.execute(
        "INSERT INTO users (id, company_id, employee_id, email, password_hash, role) "
        "VALUES (?,?,?,?,?,?)",
        (str(uuid.uuid4()), LEGACY_COMPANY_ID, 'EMP-1', 'admin-race@test.local', 'x', 'admin'),
    )
    conn.commit()
    conn.close()
    return registry_db.DB_PATH


class _RaceConnection(sqlite3.Connection):
    """A `sqlite3.Connection` subclass -- not a monkeypatched attribute,
    since `sqlite3.Connection.execute` is read-only on the C type; the same
    technique test_registry_v4_company_rebind.py's own `FlakyConnection`
    uses -- that fires a SECOND, real, independent connection's write at the
    EXACT instant `rebind_company_id`'s "before_old" count for `users` runs.

    Matches on the SQL TEXT rather than wrapping every call generically, and
    fires only ONCE (`fired`): the verification loop later in
    `rebind_company_id` issues the IDENTICAL SQL text again (the "stranded"
    check re-reads the old id's count) -- without the guard this hook would
    also fire there and attempt a second concurrent write mid-verification,
    which is not the scenario this test isolates.
    """
    db_path = None
    fired = False
    concurrent_insert_error = None
    concurrent_insert_succeeded = False

    def execute(self, sql, *args):
        # Run the REAL query FIRST -- Python's sqlite3 module steps a SELECT
        # to completion (or to its first-row buffer) inside `execute()`
        # itself, not lazily deferred to `.fetchone()`, so the count this
        # returns is fixed at THIS point regardless of what happens next.
        # The concurrent write is fired AFTER, simulating it landing in the
        # gap immediately following the count -- the exact gap Defect 3 is
        # about. Firing the write BEFORE calling the real execute would let
        # the very count under test already see it, proving nothing.
        cur = sqlite3.Connection.execute(self, sql, *args)
        if not _RaceConnection.fired and sql == 'SELECT COUNT(*) FROM "users" WHERE company_id=?':
            _RaceConnection.fired = True
            second = sqlite3.connect(_RaceConnection.db_path, timeout=0.5)
            try:
                second.execute(
                    "INSERT INTO users (id, company_id, employee_id, email, password_hash, role) "
                    "VALUES (?,?,?,?,?,?)",
                    (str(uuid.uuid4()), LEGACY_COMPANY_ID, 'EMP-RACE', 'race-concurrent@test.local', 'x', 'cashier'),
                )
                second.commit()
                _RaceConnection.concurrent_insert_succeeded = True
            except sqlite3.OperationalError as exc:
                _RaceConnection.concurrent_insert_error = exc
            finally:
                second.close()
        return cur


def test_a_concurrent_insert_landing_during_the_count_cannot_corrupt_the_snapshot():
    db_path = _fresh_registry()

    _RaceConnection.db_path = db_path
    _RaceConnection.fired = False
    _RaceConnection.concurrent_insert_error = None
    _RaceConnection.concurrent_insert_succeeded = False

    race_conn = sqlite3.connect(db_path, factory=_RaceConnection, timeout=5)
    race_conn.row_factory = sqlite3.Row
    try:
        result = rebind_company_id(race_conn, OWNER_COMPANY_ID)
    finally:
        race_conn.close()

    assert _RaceConnection.fired, (
        'fixture bug: the hook never saw the before_old count for "users" -- '
        'this test proved nothing about the race window'
    )

    # ── THE ASSERTION ────────────────────────────────────────────────────
    # With the pre-counts read AFTER BEGIN IMMEDIATE, the write lock is
    # already held by the time the count runs, so the second connection's
    # own concurrent write CANNOT land.
    assert not _RaceConnection.concurrent_insert_succeeded, (
        'a concurrent INSERT from a second, independent connection SUCCEEDED while the '
        'count that feeds rebind_company_id\'s verification was being read -- the write '
        'lock is not actually held yet at that point, which is Defect 3 reopened'
    )
    assert _RaceConnection.concurrent_insert_error is not None, (
        'the concurrent write neither succeeded nor raised -- something about this '
        'fixture is not exercising the race at all'
    )
    assert 'database is locked' in str(_RaceConnection.concurrent_insert_error).lower(), \
        _RaceConnection.concurrent_insert_error

    # And the rebind itself completed correctly -- not merely "didn't
    # crash": the race did not sneak a row in, so exactly the one originally
    # seeded user moved, nothing stranded, nothing spuriously rejected.
    assert result['status'] == 'rebound', result
    assert result['rows'] == 1, result

    conn = sqlite3.connect(db_path)
    assert conn.execute(
        'SELECT COUNT(*) FROM users WHERE company_id=?', (LEGACY_COMPANY_ID,)
    ).fetchone()[0] == 0
    assert conn.execute(
        'SELECT COUNT(*) FROM users WHERE company_id=?', (OWNER_COMPANY_ID,)
    ).fetchone()[0] == 1
    # The concurrent connection's write was rejected outright by SQLite's
    # own locking (not merely delayed past this test) -- the "race" account
    # was never created at all, under EITHER company_id.
    assert conn.execute(
        "SELECT COUNT(*) FROM users WHERE email='race-concurrent@test.local'"
    ).fetchone()[0] == 0
    assert conn.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
    conn.close()
