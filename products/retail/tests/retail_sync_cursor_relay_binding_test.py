"""
Aura Retail -- the sync cursor's relay binding (schema v31, ROADMAP.md's
"2026-09-14 - retail schema v31 CLAIMED: the sync cursor must know which
relay it belongs to" entry).

THE BUG THIS CLOSES, in the codebase's own words (see the RETAIL_SCHEMA_
VERSION v31 comment in products/retail/backend/database/schema.py and
_migrate_add_sync_cursor_relay_url's own docstring for the full story):
`sync_cursor.last_seq` has always recorded how far this device has pulled,
but never FROM WHICH relay. Every relay (Owner's cloud relay in Postgres,
each LAN hub in its own SQLite `site_sync_events`) counts in its own
independent sequence space, so a cursor left over from a relay this device
is no longer pointed at asks the new relay for events past a seq number
that means nothing there -- the new relay has nothing to send back, and the
device silently stops receiving data forever, with no error anywhere. This
is a REAL, PRE-EXISTING BUG, found while wiring Android to the v30 LAN hub,
reachable today simply by changing AURA_SYNC_RELAY_URL.

WHAT THIS FILE PROVES, in order (matching the task's own numbered plan):

  1. After migrating to v31, `sync_cursor` has a `relay_url` column and
     `PRAGMA user_version` reads 31 -- via the REAL production boot path
     (`database.schema.init_retail()`), never by executing this migration's
     own DDL string directly. See retail_site_relay_schema_test.py's
     identical `_install()` helper, which this file's own `_install()` is
     copied from.
  2. RE-RUNNING THE MIGRATION IS A NO-OP: calling
     `_migrate_add_sync_cursor_relay_url` a second time against an
     already-migrated database raises nothing and leaves exactly one
     `relay_url` column behind.
  3. `SyncService.ensure_cursor_matches_relay` with the SAME url already
     stored leaves `last_seq` untouched and returns False.
  4. `ensure_cursor_matches_relay` with a DIFFERENT url resets `last_seq` to
     0, records the new url, and returns True.
  5. THE REAL-WORLD CASE, asserted end-to-end on the data: a cursor at
     `last_seq=500` with the cloud relay's url recorded, switched to a LAN
     hub's url, ends at `last_seq=0`. This is exactly what prevents a device
     asking a hub whose own log is at seq 12 for `since=500` and receiving
     NOTHING, forever, with no error anywhere -- the failure mode the
     RETAIL_SCHEMA_VERSION v31 comment names directly.
  6. A NULL stored `relay_url` (the pre-v31 state on every row written
     before this column existed) counts as DIFFERENT from any real url and
     resets -- never as "matches whatever we are pointed at", which would
     silently preserve the exact bug this version exists to close.
  7. A blank/None `relay_url` ARGUMENT (the caller does not know which relay
     this is) does NOTHING and returns False, even when a real url is
     already stored -- fail SAFE toward not resetting, since the opposite
     would re-pull the entire history on every tick for any caller that
     never supplies one.
  8. Calling `ensure_cursor_matches_relay` twice in a row with the SAME new
     url resets only the FIRST time -- the second call is a no-op, the same
     rule case 3 proves, now checked against a url that was itself just
     written by a reset rather than one seeded by the test's own arrange
     step.

MUTATION-PROVED (see this session's own report for both directions of each
run, not encoded here as source-mutating test code -- same convention
retail_site_relay_schema_test.py's own module docstring already states):

  - Making a NULL stored value count as "same" (so the equality check
    passes when nothing is stored yet) sends case 6 RED.
  - Making a blank/None argument trigger a reset (removing the `if not
    relay_url: return False` guard) sends case 7 RED.
  - Making the reset record the new url without zeroing `last_seq` sends
    case 5 RED.

Self-contained bootstrap; no shared conftest.py exists here. CRITICAL:
exactly ONE pytest process per file -- AURA_APP_DATA resolves at import/
call time via database.schema's own module-level BASE_DIR/SUBSYS_DIR, so
two test files sharing one pytest invocation corrupt each other
(AUDIT-010).

Run:
    py -3.14 -m pytest products/retail/tests/retail_sync_cursor_relay_binding_test.py -v
"""
import os
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

PRODUCT_DIR = Path(__file__).resolve().parents[1]        # products/retail
BACKEND_DIR = PRODUCT_DIR / 'backend'
SUITE_ROOT = PRODUCT_DIR.parent.parent                    # aura-fullsuits
for _p in (str(SUITE_ROOT), str(BACKEND_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from commercial_runtime.sync.sync_service import SyncService  # noqa: E402

_TMP_DIRS = []

CLOUD_URL = 'https://relay.owner.example/api/sync/v1'
HUB_URL = 'https://192.168.1.50:8843/api/sync/v1'


def teardown_module(module):
    for d in _TMP_DIRS:
        shutil.rmtree(d, ignore_errors=True)
    _TMP_DIRS.clear()


# ── fixture bedrock (mirrors retail_site_relay_schema_test.py's identical
#    helper, itself mirroring retail_sync_freshness_test.py) ───────────────

def _fresh_app_data(prefix):
    tmp = tempfile.mkdtemp(prefix=prefix)
    _TMP_DIRS.append(tmp)
    os.environ['AURA_APP_DATA'] = tmp
    return tmp


def _install():
    """A REAL install: fresh temp AURA_APP_DATA, the genuine `init_retail()`
    boot path, the full v0 -> v31 migration chain, real demo seed data.
    Deliberately not a hand-built minimal schema and deliberately not this
    migration's own DDL string executed directly -- this file's whole point
    is that the REAL `sync_cursor.relay_url` column exists and behaves as
    the real production migration chain built it, `PRAGMA user_version`
    included."""
    tmp = _fresh_app_data('aura-retail-sync-cursor-relay-')
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


def _sync_service():
    """A real `SyncService` instance, constructed the way `__init__`'s own
    docstring says every call site must -- but with dummy client_factory/
    get_conn callables, never invoked here: `ensure_cursor_matches_relay`
    only ever touches the `conn` it is explicitly handed, exactly like
    `read_cursor`/`apply_pull_result`'s own cursor UPDATE, so a test calling
    it directly against a real connection never needs a real relay client or
    a second, test-only connection factory."""
    return SyncService(client_factory=lambda: None, get_conn=lambda: None)


def _set_cursor(conn, *, last_seq, relay_url):
    """Directly seeds `sync_cursor`'s single row for a test's own arrange
    step -- raw SQL, matching this table's own single-row (id=1) idiom,
    rather than going through `ensure_cursor_matches_relay` itself, which is
    the thing under test."""
    conn.execute(
        'UPDATE sync_cursor SET last_seq=?, relay_url=? WHERE id=1',
        (last_seq, relay_url),
    )
    conn.commit()


def _read_cursor_row(conn):
    row = conn.execute('SELECT last_seq, relay_url FROM sync_cursor WHERE id=1').fetchone()
    return dict(row)


# ── 1. relay_url column exists, schema is at least v31 ──────────────────

def test_relay_url_column_exists_and_schema_is_at_least_v31():
    sch, db_path = _install()
    conn = _open(db_path)
    try:
        # THIS WAS `== 31`, and that literal is why it went red. It pins the
        # WHOLE schema's version, so any unrelated migration shipping later
        # breaks it: the value had drifted to 36 (v32 cheques, v33 quotations,
        # v34 doc series, and two more) with nothing here being wrong.
        #
        # What the assertion is actually for is "the relay_url migration has
        # run", and `>= 31` says exactly that -- any database at or past v31
        # has run it, and one that has not still fails. The column check
        # immediately below is the direct evidence; this is belt-and-braces.
        #
        # Deliberately NOT compared against the schema-version CONSTANT: that
        # would compare two values that move together, and could no longer
        # catch either of them drifting.
        #
        # WHAT THIS CAN NO LONGER CATCH: that v31 is the EXACT version this
        # migration lands at. Nothing depended on that; the column is what the
        # rest of this file tests.
        assert conn.execute('PRAGMA user_version').fetchone()[0] >= 31

        cursor_cols = {row[1] for row in conn.execute('PRAGMA table_info(sync_cursor)').fetchall()}
        assert 'relay_url' in cursor_cols
    finally:
        conn.close()


# ── 2. Re-running the migration is a no-op ─────────────────────────────────

def test_rerunning_migration_is_a_no_op():
    sch, db_path = _install()
    conn = _open(db_path)
    try:
        sch._migrate_add_sync_cursor_relay_url(conn)  # second, unnecessary pass
        conn.commit()

        cursor_cols = [row[1] for row in conn.execute('PRAGMA table_info(sync_cursor)').fetchall()]
        assert cursor_cols.count('relay_url') == 1, \
            "a redundant second call must not duplicate the column or raise"
        assert conn.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
    finally:
        conn.close()


# ── 3. Same url stored: last_seq untouched, returns False ─────────────────

def test_same_url_leaves_last_seq_untouched_and_returns_false():
    sch, db_path = _install()
    conn = _open(db_path)
    try:
        _set_cursor(conn, last_seq=42, relay_url=CLOUD_URL)
        service = _sync_service()

        result = service.ensure_cursor_matches_relay(conn, CLOUD_URL)
        conn.commit()

        assert result is False
        row = _read_cursor_row(conn)
        assert row['last_seq'] == 42
        assert row['relay_url'] == CLOUD_URL
    finally:
        conn.close()


# ── 4. Different url stored: resets last_seq to 0, records new url, True ──

def test_different_url_resets_last_seq_records_new_url_returns_true():
    sch, db_path = _install()
    conn = _open(db_path)
    try:
        _set_cursor(conn, last_seq=42, relay_url=CLOUD_URL)
        service = _sync_service()

        result = service.ensure_cursor_matches_relay(conn, HUB_URL)
        conn.commit()

        assert result is True
        row = _read_cursor_row(conn)
        assert row['last_seq'] == 0
        assert row['relay_url'] == HUB_URL
    finally:
        conn.close()


# ── 5. The real-world case, asserted end to end on the data ───────────────

def test_till_pointed_from_cloud_to_hub_resets_cursor_to_zero():
    """Prevents exactly this: a till synced with the cloud relay and sitting
    at seq 500 gets re-pointed at a LAN hub whose own `site_sync_events` log
    is only at seq 12. Without this reset the till would pull with
    `since=500` forever, the hub would have nothing above 500 to send back,
    and the device would receive NOTHING, with no error anywhere -- looking
    exactly like "already fully caught up" when it is actually stuck for
    good."""
    sch, db_path = _install()
    conn = _open(db_path)
    try:
        _set_cursor(conn, last_seq=500, relay_url=CLOUD_URL)
        service = _sync_service()

        service.ensure_cursor_matches_relay(conn, HUB_URL)
        conn.commit()

        row = _read_cursor_row(conn)
        assert row['last_seq'] == 0, (
            "a stale last_seq=500 carried over from the cloud relay must never survive "
            "a switch to a hub whose own log is at seq 12 -- pulling since=500 against "
            "that hub would return nothing, forever"
        )
        assert row['relay_url'] == HUB_URL
    finally:
        conn.close()


# ── 6. NULL stored relay_url counts as different and resets ───────────────

def test_null_stored_relay_url_counts_as_different_and_resets():
    sch, db_path = _install()
    conn = _open(db_path)
    try:
        # Explicitly NULL -- the real pre-v31 state on every row written
        # before this column existed (see _migrate_add_sync_cursor_relay_
        # url's own docstring: no backfill, so every such row stays NULL
        # until this method's own reset first runs against it).
        _set_cursor(conn, last_seq=77, relay_url=None)
        service = _sync_service()

        result = service.ensure_cursor_matches_relay(conn, CLOUD_URL)
        conn.commit()

        assert result is True, \
            "NULL must be treated as unknown-therefore-different, never as a match"
        row = _read_cursor_row(conn)
        assert row['last_seq'] == 0
        assert row['relay_url'] == CLOUD_URL
    finally:
        conn.close()


# ── 7. Blank/None argument does nothing, even with a url stored ───────────

def test_blank_argument_does_nothing_even_with_url_stored():
    sch, db_path = _install()
    conn = _open(db_path)
    try:
        _set_cursor(conn, last_seq=500, relay_url=CLOUD_URL)
        service = _sync_service()

        result_none = service.ensure_cursor_matches_relay(conn, None)
        result_blank = service.ensure_cursor_matches_relay(conn, '')
        conn.commit()

        assert result_none is False
        assert result_blank is False
        row = _read_cursor_row(conn)
        assert row['last_seq'] == 500, (
            "an unknown caller-supplied relay_url must never reset a real stored "
            "cursor -- resetting on every unknown-url tick would re-pull the entire "
            "history over and over instead of the one-time catch-up this guard costs"
        )
        assert row['relay_url'] == CLOUD_URL
    finally:
        conn.close()


# ── 8. Same new url called twice: resets only the first time ──────────────

def test_calling_twice_with_same_new_url_resets_only_first_time():
    sch, db_path = _install()
    conn = _open(db_path)
    try:
        _set_cursor(conn, last_seq=500, relay_url=CLOUD_URL)
        service = _sync_service()

        first = service.ensure_cursor_matches_relay(conn, HUB_URL)
        conn.commit()
        # Simulate a normal pull advancing the cursor after the reset -- a
        # second call with the SAME url must leave this alone, not reset it
        # back to 0 a second time.
        conn.execute('UPDATE sync_cursor SET last_seq=15 WHERE id=1')
        conn.commit()

        second = service.ensure_cursor_matches_relay(conn, HUB_URL)
        conn.commit()

        assert first is True
        assert second is False
        row = _read_cursor_row(conn)
        assert row['last_seq'] == 15, \
            "the second call with the same url must leave last_seq exactly where the pull left it"
        assert row['relay_url'] == HUB_URL
    finally:
        conn.close()
