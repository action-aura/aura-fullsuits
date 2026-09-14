"""
Aura Retail -- the LAN site relay's hub-side tables (schema v30, ROADMAP.md's
"2026-09-14 - retail schema v30 CLAIMED for the LAN site relay (R-LAN)"
entry), implementing docs/launch-readiness/lan-restaurant-design.md §3's
"Mechanics of the site relay (hub side -- all new, all product-side)".

WHAT THIS FILE PROVES, in order (matching this task's own numbered plan):

  1. All six tables exist after a REAL migration to v30, and `PRAGMA
     user_version` reads 30 -- via the actual production boot path
     (`database.schema.init_retail()`, which runs `_init_retail`'s
     executescript and then `ensure_schema_version(..., _migrate_retail_
     schema, ...)` exactly the way `app.py::init_app()` does), never by
     executing this migration's own DDL string directly. See
     `retail_sync_freshness_test.py`'s identical `_install()` helper, which
     this file's own `_install()` is copied from.
  2. RE-RUNNING THE MIGRATION IS A NO-OP: calling `_migrate_add_site_relay`
     a second time against an already-migrated database changes no row
     count and no `sqlite_master` object.
  3. `site_sync_events.id` is genuinely UNIQUE -- this IS the dedup
     mechanism the cloud relay relies on (see the migration's own
     docstring), not an incidental constraint, so a duplicate id must raise
     `sqlite3.IntegrityError`.
  4. `seq` is AUTOINCREMENT, not a reused rowid: deleting the highest-`seq`
     row and inserting a fresh one must produce a seq GREATER than the
     deleted row's -- the exact property that protects a device's cursor
     from silently skipping an event whose number was reissued.
  5. `site_forward_cursor` seeds to exactly one row with BOTH
     `forwarded_to_seq = 0` and `cloud_pull_seq = 0` (the coordinator's
     spec amendment adding `cloud_pull_seq` alongside the original
     `forwarded_to_seq`), and a second `INSERT OR IGNORE` of id=1 leaves it
     at one row, unchanged.
  6. `site_sync_nonces` allows the SAME nonce string under two different
     scopes (push vs. pull must not share a burn history) and refuses a
     duplicate nonce within the SAME scope.

MUTATION-PROVED (see this session's own report for both directions of each
run, not encoded here as source-mutating test code -- same convention
retail_loyalty_return_link_test.py's own module docstring already states
for its sibling file): case 3 (`test_site_sync_events_id_is_unique`) was
confirmed to go RED when the migration's `id` column was changed from
`TEXT NOT NULL UNIQUE` to plain `TEXT NOT NULL`, and case 4
(`test_seq_is_autoincrement_not_reused_after_delete`) was confirmed to go
RED when `seq INTEGER PRIMARY KEY AUTOINCREMENT` was changed to
`seq INTEGER PRIMARY KEY` (a bare rowid alias). Both back to GREEN on
revert.

Self-contained bootstrap; no shared conftest.py exists here. CRITICAL:
exactly ONE pytest process per file -- AURA_APP_DATA resolves at import/
call time via database.schema's own module-level BASE_DIR/SUBSYS_DIR, so
two test files sharing one pytest invocation corrupt each other
(AUDIT-010).

Run:
    py -3.14 -m pytest products/retail/tests/retail_site_relay_schema_test.py -v
"""
import os
import shutil
import sqlite3
import sys
import tempfile
import uuid
from pathlib import Path

import pytest

PRODUCT_DIR = Path(__file__).resolve().parents[1]        # products/retail
BACKEND_DIR = PRODUCT_DIR / 'backend'
SUITE_ROOT = PRODUCT_DIR.parent.parent                    # aura-fullsuits
for _p in (str(SUITE_ROOT), str(BACKEND_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

_TMP_DIRS = []

SITE_RELAY_TABLES = [
    'site_sync_events',
    'site_sync_nonces',
    'site_device_cursors',
    'site_paired_devices',
    'site_forward_cursor',
    'site_roster',
]


def teardown_module(module):
    for d in _TMP_DIRS:
        shutil.rmtree(d, ignore_errors=True)
    _TMP_DIRS.clear()


# ── fixture bedrock (mirrors retail_sync_freshness_test.py's identical
#    helper, itself mirroring retail_v17_catalogue_migration_test.py) ──────

def _fresh_app_data(prefix):
    tmp = tempfile.mkdtemp(prefix=prefix)
    _TMP_DIRS.append(tmp)
    os.environ['AURA_APP_DATA'] = tmp
    return tmp


def _install():
    """A REAL install: fresh temp AURA_APP_DATA, the genuine `init_retail()`
    boot path, the full v0 -> v30 migration chain, real demo seed data.
    Deliberately not a hand-built minimal schema and deliberately not this
    migration's own DDL string executed directly -- this file's whole point
    is that the REAL `site_sync_events`/`site_sync_nonces`/
    `site_device_cursors`/`site_paired_devices`/`site_forward_cursor`/
    `site_roster` tables exist and behave as the real production migration
    chain built them, `PRAGMA user_version` included."""
    tmp = _fresh_app_data('aura-retail-site-relay-')
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


def _insert_event(conn, *, entity_id, origin_device_id='dev-a'):
    """Inserts one minimal, valid `site_sync_events` row and returns the
    `seq` SQLite assigned it. A fresh `id` every call unless the caller
    passes a duplicate on purpose (see the UNIQUE test)."""
    conn.execute(
        "INSERT INTO site_sync_events "
        "(id, entity_type, entity_id, event_type, payload, created_at, origin_device_id) "
        "VALUES (?,?,?,?,?,?,?)",
        (str(uuid.uuid4()), 'sale', entity_id, 'create', '{}',
         '2026-09-14T00:00:00Z', origin_device_id),
    )
    conn.commit()
    return conn.execute(
        "SELECT seq FROM site_sync_events ORDER BY seq DESC LIMIT 1"
    ).fetchone()[0]


# ── 1. All six tables exist, PRAGMA user_version is 30 ─────────────────────

def test_all_six_tables_exist_and_the_version_advanced_past_v30():
    """`>= 30`, not `== 30`, and not `== RETAIL_SCHEMA_VERSION` either.

    v30 is the version at which these six tables appeared, so that is the floor
    this file can meaningfully assert: anything below it means the site-relay
    migration did not run. A hard `== 30` breaks on every future bump for no
    gain -- v31 landed within a day and broke exactly this line. Comparing
    against the live `RETAIL_SCHEMA_VERSION` constant instead would be worse
    than either: two values that move together can no longer catch either one
    drifting, which is the quiet test-weakening ENGINEERING.md names
    specifically. The table existence checks below are what actually prove the
    migration ran."""
    sch, db_path = _install()
    conn = _open(db_path)
    try:
        assert conn.execute('PRAGMA user_version').fetchone()[0] >= 30

        live_tables = {
            row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        for table in SITE_RELAY_TABLES:
            assert table in live_tables, f"{table} must exist after migrating to v30"
    finally:
        conn.close()


# ── 2. Re-running the migration is a no-op ─────────────────────────────────

def test_rerunning_migration_is_a_no_op():
    sch, db_path = _install()
    conn = _open(db_path)
    try:
        counts_before = {
            table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in SITE_RELAY_TABLES
        }
        objects_before = [
            dict(r) for r in conn.execute(
                "SELECT type, name FROM sqlite_master WHERE name LIKE 'site\\_%' ESCAPE '\\' "
                "ORDER BY type, name"
            )
        ]

        sch._migrate_add_site_relay(conn)  # second, unnecessary pass
        conn.commit()

        counts_after = {
            table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in SITE_RELAY_TABLES
        }
        objects_after = [
            dict(r) for r in conn.execute(
                "SELECT type, name FROM sqlite_master WHERE name LIKE 'site\\_%' ESCAPE '\\' "
                "ORDER BY type, name"
            )
        ]

        assert counts_before == counts_after, \
            "a redundant second call must not change any site_* table's row count"
        assert objects_before == objects_after, \
            "sqlite_master's own site_* object list must be byte-identical after a redundant second call"
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == 'ok'
    finally:
        conn.close()


# ── 3. site_sync_events.id is genuinely UNIQUE (the dedup mechanism) ───────

def test_site_sync_events_id_is_unique():
    sch, db_path = _install()
    conn = _open(db_path)
    try:
        event_id = str(uuid.uuid4())
        conn.execute(
            "INSERT INTO site_sync_events "
            "(id, entity_type, entity_id, event_type, payload, created_at, origin_device_id) "
            "VALUES (?,?,?,?,?,?,?)",
            (event_id, 'sale', 'sale-1', 'create', '{}', '2026-09-14T00:00:00Z', 'dev-a'),
        )
        conn.commit()

        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO site_sync_events "
                "(id, entity_type, entity_id, event_type, payload, created_at, origin_device_id) "
                "VALUES (?,?,?,?,?,?,?)",
                (event_id, 'sale', 'sale-2', 'create', '{}', '2026-09-14T00:00:01Z', 'dev-b'),
            )
    finally:
        conn.close()


# ── 4. seq is AUTOINCREMENT, not a reused rowid ────────────────────────────

def test_seq_is_autoincrement_not_reused_after_delete():
    sch, db_path = _install()
    conn = _open(db_path)
    try:
        seqs = [_insert_event(conn, entity_id=f'sale-{i}') for i in range(3)]
        highest = max(seqs)

        conn.execute("DELETE FROM site_sync_events WHERE seq=?", (highest,))
        conn.commit()

        new_seq = _insert_event(conn, entity_id='sale-3')
        assert new_seq > highest, (
            "AUTOINCREMENT must never reissue a deleted row's seq -- a device holding a "
            "cursor past the deleted number would silently skip whatever event now "
            "occupies it", new_seq, highest,
        )
    finally:
        conn.close()


# ── 5. site_forward_cursor: one row, both watermarks seeded to 0 ──────────

def test_site_forward_cursor_seeded_single_row_both_watermarks_zero():
    sch, db_path = _install()
    conn = _open(db_path)
    try:
        rows = conn.execute(
            "SELECT id, forwarded_to_seq, cloud_pull_seq FROM site_forward_cursor"
        ).fetchall()
        assert len(rows) == 1, rows
        assert rows[0]['id'] == 1
        assert rows[0]['forwarded_to_seq'] == 0
        assert rows[0]['cloud_pull_seq'] == 0

        conn.execute(
            "INSERT OR IGNORE INTO site_forward_cursor (id, forwarded_to_seq, cloud_pull_seq) "
            "VALUES (1, 999, 999)"
        )
        conn.commit()

        rows_after = conn.execute(
            "SELECT id, forwarded_to_seq, cloud_pull_seq FROM site_forward_cursor"
        ).fetchall()
        assert len(rows_after) == 1, \
            "INSERT OR IGNORE against id=1 must never create a second row"
        assert rows_after[0]['forwarded_to_seq'] == 0, \
            "INSERT OR IGNORE must not overwrite the existing forwarded_to_seq"
        assert rows_after[0]['cloud_pull_seq'] == 0, \
            "INSERT OR IGNORE must not overwrite the existing cloud_pull_seq"
    finally:
        conn.close()


# ── 6. site_sync_nonces: scoped uniqueness ─────────────────────────────────

def test_site_sync_nonces_allow_same_value_across_scopes_but_not_within_one():
    sch, db_path = _install()
    conn = _open(db_path)
    try:
        nonce = str(uuid.uuid4())
        conn.execute("INSERT INTO site_sync_nonces (scope, nonce) VALUES ('push', ?)", (nonce,))
        conn.execute("INSERT INTO site_sync_nonces (scope, nonce) VALUES ('pull', ?)", (nonce,))
        conn.commit()  # same nonce string, two different scopes -- both must be allowed

        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO site_sync_nonces (scope, nonce) VALUES ('push', ?)", (nonce,))
    finally:
        conn.close()
