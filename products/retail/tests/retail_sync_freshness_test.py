"""Aura Retail -- schema v18 / SyncService regression coverage:
launch-readiness Phase 7 stage 7a, persisted sync freshness. See
docs/launch-readiness/phase7-offline-ux.md "FINDING 1",
database/schema.py::_migrate_add_sync_freshness /
::load_sync_freshness / ::record_sync_freshness, and
commercial_runtime/sync/sync_service.py::SyncFreshnessStore.

WHY THIS FILE EXISTS. `SyncService._health["push"/"pull"]["last_success_at"]`
was in-memory only, built by `_fresh_half_health()` and reset to None on
every process start. Every Phase 7 rule that measures elapsed time since the
last successful sync -- 30-minute stale-stock hiding, the 24-hour warning,
the 72-hour hard stop on new sales -- is defined against that clock, so on a
till restarted each morning (the normal way a shop opens) none of them could
ever fire. This stage gives the clock a durable home; this file proves it
actually survives a restart, not just that the code compiles.

THE DECISIVE TEST is `test_last_success_survives_a_restart`. A single
long-lived `SyncService` instance cannot fail it -- its in-memory dict
already holds the value regardless of whether persistence works at all --
so it deliberately constructs a SECOND `SyncService` instance against the
SAME database after the first one records a success, and asserts the
second instance reports the first one's value. That second construction
is the restart. Every other test in this file is a supporting proof: the
never-synced/long-offline distinction (STEP 3's real trap -- collapsing
"never synced" into a huge elapsed number would trip the 72-hour hard stop
on a shop's very first day), the most-recent-of-both-halves rule, and that
the collaborator really is optional (the registry `SyncService` construction
site in app.py never gets one).

Follows the SAME bootstrap convention as retail_v15_ledger_truth_migration_
test.py / retail_v16_terminal_cash_drawer_test.py / retail_v17_catalogue_
migration_test.py (no shared conftest.py exists for products/retail/tests/):
its own temp app-data dir per test via `_install()`, real `init_retail()`,
no Flask app anywhere in this file -- this file constructs `SyncService`
directly against the real database `init_retail()` builds, exactly the way
`_sync_get_conn`/`load_sync_freshness`/`record_sync_freshness` are wired
together in products/retail/backend/app.py, just without a Flask app or a
real relay in between. `AURA_APP_DATA` is reassigned per test the same way
retail_sync_starts_when_configured_test.py / retail_tombstone_test.py set it
before their own imports -- here via `database.schema.BASE_DIR`/
`SUBSYS_DIR` reassignment inside `_install()` instead, since this file never
imports `app.py` (no Flask app needed to exercise `SyncService` directly).

Every test here proves persistence via `SyncService._record_sync_success`
directly -- the actual method `run_once()` calls after a real push_once()/
pull_once() success -- never a real push/pull round trip against a fake
relay (that mechanism is already covered by commercial_runtime/sync/
tests/test_sync_service.py). This file is only about whether the recorded
fact survives a restart, not about push/pull mechanics.

Run:
    pytest products/retail/tests/retail_sync_freshness_test.py -v
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

from commercial_runtime.sync.sync_service import SyncFreshnessStore, SyncService  # noqa: E402

_TMP_DIRS = []


def teardown_module(module):
    for d in _TMP_DIRS:
        shutil.rmtree(d, ignore_errors=True)
    _TMP_DIRS.clear()


# ── fixture bedrock (mirrors retail_v17_catalogue_migration_test.py) ────────

def _fresh_app_data(prefix):
    tmp = tempfile.mkdtemp(prefix=prefix)
    _TMP_DIRS.append(tmp)
    os.environ['AURA_APP_DATA'] = tmp
    return tmp


def _install():
    """A REAL install: fresh temp AURA_APP_DATA, the genuine `init_retail()`
    boot path, the full v0 -> current migration chain, real demo seed data.
    Deliberately not a hand-built minimal schema -- see
    retail_v15_ledger_truth_migration_test.py's identical helper for why:
    this file's whole point is that the REAL `sync_freshness` table (v18)
    exists and behaves as the real migration built it."""
    tmp = _fresh_app_data('aura-retail-sync-freshness-')
    import database.schema as sch
    sch.BASE_DIR = os.path.join(tmp, 'database')
    sch.SUBSYS_DIR = os.path.join(sch.BASE_DIR, 'subsystems')
    sch.init_retail()
    return sch, os.path.join(sch.SUBSYS_DIR, 'retail.db')


def _open(db_path):
    conn = sqlite3.connect(db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys=ON')
    return conn


def _user_version(db_path):
    conn = _open(db_path)
    try:
        return conn.execute('PRAGMA user_version').fetchone()[0]
    finally:
        conn.close()


def _integrity_ok(db_path):
    conn = _open(db_path)
    try:
        row = conn.execute('PRAGMA integrity_check').fetchone()
        return bool(row) and row[0] == 'ok'
    finally:
        conn.close()


def _service(sch, with_freshness=True):
    """Builds a `SyncService` against `sch`'s own retail db -- `get_conn` is
    `sch.get_retail_conn` itself, matching production's `_sync_get_conn`
    wiring in app.py exactly (both resolve through the same module-level
    `BASE_DIR`/`SUBSYS_DIR`, so every instance built from the same `sch`
    within one test talks to the SAME on-disk database). `client_factory`
    and `local_company_id_provider` are both None throughout this file --
    never exercised, since no test here calls push_once()/pull_once() or
    apply_pull_result()."""
    store = None
    if with_freshness:
        store = SyncFreshnessStore(
            load=sch.load_sync_freshness, record=sch.record_sync_freshness,
            record_override=sch.record_offline_override)
    return SyncService(None, sch.get_retail_conn, None, local_freshness_store=store)


# ── 1. THE DECISIVE TEST ─────────────────────────────────────────────────────

def test_last_success_survives_a_restart():
    sch, db_path = _install()

    service_a = _service(sch)
    service_a._record_sync_success('push')
    service_a._record_sync_success('pull')
    recorded = service_a.get_health()
    recorded_push = recorded['push']['last_success_at']
    recorded_pull = recorded['pull']['last_success_at']
    assert recorded_push is not None
    assert recorded_pull is not None

    # THE RESTART: a genuinely SECOND SyncService instance, constructed
    # fresh against the SAME database. Instance A's in-memory self._health
    # is never touched or shared -- if this instance ever reports None
    # here, the clock did not survive the restart, which is the exact
    # Phase 7 bug this stage exists to close (docs/launch-readiness/
    # phase7-offline-ux.md "FINDING 1"). A single long-lived instance could
    # not fail this assertion even with none of stage 7a's work done, which
    # is why this second construction is not optional.
    service_b = _service(sch)
    health_b = service_b.get_health()
    assert health_b['push']['last_success_at'] == recorded_push, (
        'a restarted SyncService must report the SAME last_success_at the '
        'previous instance recorded, not None')
    assert health_b['pull']['last_success_at'] == recorded_pull


# ── 2. never-synced vs long-offline (the STEP 3 trap) ────────────────────────

def test_a_fresh_install_reports_never_synced_not_a_huge_elapsed_time():
    sch, db_path = _install()
    service = _service(sch)
    health = service.get_health()
    assert health['never_synced'] is True, (
        'a fresh install that has never synced successfully must be '
        'reported as never_synced=True, not silently folded into an '
        'elapsed number')
    assert health['seconds_since_last_success'] is None, (
        'seconds_since_last_success must be None on a fresh install -- a '
        'huge number here would misread as "offline for days" on day one, '
        'before the shop has done anything wrong, and would wrongly trip '
        'the 72-hour hard stop stage 7c builds on top of this figure. A '
        'fabricated zero would be equally wrong the other way, silently '
        'claiming a freshness that was never actually observed -- neither '
        'extreme is acceptable, which is why this is None, not 0.')


def test_elapsed_is_measured_from_the_most_recent_success_across_both_halves():
    sch, db_path = _install()
    service = _service(sch)

    # push "succeeded" a long time ago -- set directly on the in-memory
    # cache (what get_health() actually reads) rather than via
    # _record_sync_success + a real sleep, so the gap is large and
    # unambiguous rather than depending on real wall-clock timing during
    # the test run.
    service._health['push']['last_success_at'] = '2020-01-01T00:00:00+00:00'

    # pull succeeds NOW, genuinely through the real method under test.
    service._record_sync_success('pull')

    health = service.get_health()
    assert health['never_synced'] is False
    assert health['seconds_since_last_success'] is not None
    assert health['seconds_since_last_success'] < 60, (
        'elapsed must be measured from the MORE RECENT of push/pull -- '
        'pull just succeeded, so this must read as a few seconds, not the '
        'years since the artificially old push timestamp. Reading this as '
        'a huge number would mean a device that just successfully talked '
        'to the relay (via pull) is nonetheless treated as long offline '
        'because push happens to be older -- exactly the wrong answer for '
        'every Phase 7 consumer of this figure.')


# ── 3. the registry case: the collaborator really is optional ───────────────

def test_a_service_with_no_freshness_store_still_syncs_and_persists_nothing():
    """The registry `SyncService` construction site's own case (products/
    retail/backend/app.py): `local_freshness_store=None` must not raise,
    and must genuinely persist nothing -- proven against a database that
    DOES carry the real `sync_freshness` table (this is the real retail
    schema built by init_retail(), not a stripped-down fixture missing the
    table), so a passing result here cannot be explained by there simply
    being no table to fail against."""
    sch, db_path = _install()
    service = _service(sch, with_freshness=False)

    service._record_sync_success('push')  # must not raise
    service._record_sync_success('pull')  # must not raise

    health = service.get_health()
    assert health['push']['last_success_at'] is not None  # still recorded IN MEMORY
    assert health['pull']['last_success_at'] is not None

    conn = _open(db_path)
    try:
        row = conn.execute(
            'SELECT last_push_success_at, last_pull_success_at FROM sync_freshness WHERE id = 1'
        ).fetchone()
    finally:
        conn.close()
    assert row['last_push_success_at'] is None, (
        'no freshness store was configured for this instance -- nothing '
        'should have been written to sync_freshness')
    assert row['last_pull_success_at'] is None


# ── 4. schema v18 itself ─────────────────────────────────────────────────────

def test_v18_migration_is_idempotent_and_lands_on_head():
    sch, db_path = _install()

    assert _user_version(db_path) == sch.RETAIL_SCHEMA_VERSION
    assert _integrity_ok(db_path)

    conn = _open(db_path)
    try:
        row_before = conn.execute(
            'SELECT last_push_success_at, last_pull_success_at FROM sync_freshness WHERE id = 1'
        ).fetchone()
    finally:
        conn.close()
    assert row_before is not None, 'sync_freshness must exist and be seeded on a fresh v18 install'
    assert row_before['last_push_success_at'] is None
    assert row_before['last_pull_success_at'] is None

    # Rewind the marker one version behind head and run the WHOLE chain
    # again -- ensure_schema_version only knows "behind", not "behind by
    # one" -- exactly retail_v17_catalogue_migration_test.py's own
    # `test_v17_migration_is_idempotent_a_second_pass_is_a_clean_noop`
    # pattern, generalised so it never hardcodes 18.
    conn = _open(db_path)
    try:
        conn.execute(f'PRAGMA user_version = {sch.RETAIL_SCHEMA_VERSION - 1}')
        conn.commit()
    finally:
        conn.close()

    from commercial_runtime.security.migration_safety import ensure_schema_version
    conn = _open(db_path)
    try:
        ensure_schema_version(conn, db_path, sch.RETAIL_SCHEMA_VERSION,
                               sch._migrate_retail_schema,
                               backup_dir=os.path.join(sch.BASE_DIR, 'migration_backups'))
    finally:
        conn.close()

    assert _user_version(db_path) == sch.RETAIL_SCHEMA_VERSION
    assert _integrity_ok(db_path)

    conn = _open(db_path)
    try:
        row_count = conn.execute('SELECT COUNT(*) FROM sync_freshness').fetchone()[0]
        row_after = conn.execute(
            'SELECT last_push_success_at, last_pull_success_at FROM sync_freshness WHERE id = 1'
        ).fetchone()
    finally:
        conn.close()
    assert row_count == 1, 'a second v18 pass must not create or duplicate the single sync_freshness row'
    assert row_after['last_push_success_at'] is None
    assert row_after['last_pull_success_at'] is None

    # Idempotency at the function level too, not just via
    # ensure_schema_version's version gate -- mirrors
    # test_dropping_quantity_reserved_is_idempotent_on_a_database_that_
    # never_had_the_column in retail_v17_catalogue_migration_test.py.
    conn = _open(db_path)
    try:
        sch._migrate_add_sync_freshness(conn)  # must not raise
        conn.commit()
        row_count_again = conn.execute('SELECT COUNT(*) FROM sync_freshness').fetchone()[0]
    finally:
        conn.close()
    assert row_count_again == 1
