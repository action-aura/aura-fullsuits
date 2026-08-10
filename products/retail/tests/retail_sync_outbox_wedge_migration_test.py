"""
Aura Retail -- schema v7 migration regression coverage (outbox-wedge fix,
2026-08-10 audit -- see database/schema.py's
`_migrate_sync_outbox_ordering_and_dead_letter` and
`commercial_runtime/sync/sync_service.py`'s module docstring for the full
writeup of the bug this closes).

Hand-builds a v6-shaped sync_outbox (exactly the shape init_retail() +
the v1-v6 chain leave it: `id TEXT PRIMARY KEY, ..., created_at TIMESTAMP
DEFAULT CURRENT_TIMESTAMP`, no attempt_count/last_error, no sync_dead_letter
table at all) using the same "build the old-version table by hand, then
invoke the migration function directly" technique as
retail_po_split_migration_test.py. Only sync_outbox is built -- every other
table this migration doesn't touch is irrelevant here.

Run:
    pytest products/retail/tests/retail_sync_outbox_wedge_migration_test.py -v
"""
import json
import os
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

NEW_OUTBOX_COLUMNS = {
    "id", "entity_type", "entity_id", "event_type", "payload", "created_at",
    "attempt_count", "last_error",
}
DEAD_LETTER_COLUMNS = {
    "id", "entity_type", "entity_id", "event_type", "payload", "created_at",
    "attempt_count", "last_error", "dead_lettered_at",
}


def _fresh_conn(path):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _seed_v6_sync_outbox(conn, rows=None):
    """Builds the schema-v6 shape of sync_outbox: created_at still carries
    `DEFAULT CURRENT_TIMESTAMP`, and neither attempt_count/last_error nor
    sync_dead_letter exist yet -- exactly the state a real device is in the
    instant before this migration runs. `rows`, if given, is a list of
    (id, entity_type, entity_id, event_type, payload_dict, created_at) tuples
    inserted in that exact order (their rowid therefore reflects true
    insertion order, same as any real device's outbox)."""
    conn.executescript("""
        CREATE TABLE sync_outbox (
            id TEXT PRIMARY KEY,
            entity_type TEXT NOT NULL,
            entity_id TEXT NOT NULL,
            event_type TEXT NOT NULL,
            payload TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)
    for row_id, entity_type, entity_id, event_type, payload, created_at in (rows or []):
        if created_at is None:
            conn.execute(
                "INSERT INTO sync_outbox (id, entity_type, entity_id, event_type, payload) VALUES (?,?,?,?,?)",
                (row_id, entity_type, entity_id, event_type, json.dumps(payload)),
            )
        else:
            conn.execute(
                "INSERT INTO sync_outbox (id, entity_type, entity_id, event_type, payload, created_at) VALUES (?,?,?,?,?,?)",
                (row_id, entity_type, entity_id, event_type, json.dumps(payload), created_at),
            )
    conn.commit()


def test_migration_creates_new_columns_and_dead_letter_table():
    from database.schema import _migrate_sync_outbox_ordering_and_dead_letter

    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "retail.db")
        conn = _fresh_conn(path)
        row_id = str(uuid.uuid4())
        _seed_v6_sync_outbox(conn, rows=[
            (row_id, "category", "cat-1", "create", {"id": "cat-1", "name": "Beverages"}, "2026-08-06T00:00:00+00:00"),
        ])

        _migrate_sync_outbox_ordering_and_dead_letter(conn)
        conn.commit()

        cols = {r["name"] for r in conn.execute("PRAGMA table_info(sync_outbox)").fetchall()}
        assert cols == NEW_OUTBOX_COLUMNS

        col_info = {r["name"]: r for r in conn.execute("PRAGMA table_info(sync_outbox)").fetchall()}
        assert col_info["created_at"]["notnull"] == 1
        assert col_info["created_at"]["dflt_value"] is None  # DEFAULT CURRENT_TIMESTAMP is gone
        assert col_info["attempt_count"]["dflt_value"] == "0"

        tables = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        assert "sync_dead_letter" in tables
        dl_cols = {r["name"] for r in conn.execute("PRAGMA table_info(sync_dead_letter)").fetchall()}
        assert dl_cols == DEAD_LETTER_COLUMNS

        # pre-existing row survives untouched (it already had a proper
        # T-separated created_at, so normalization is a no-op for it)
        row = conn.execute("SELECT * FROM sync_outbox WHERE id=?", (row_id,)).fetchone()
        assert row is not None
        assert row["created_at"] == "2026-08-06T00:00:00+00:00"
        assert row["attempt_count"] == 0
        assert row["last_error"] is None

        conn.close()


def test_migration_rejects_a_future_insert_with_no_created_at():
    """The actual point of dropping DEFAULT CURRENT_TIMESTAMP: a writer that
    forgets to supply created_at must fail loudly (NOT NULL violation), never
    silently fall back to a differently-formatted timestamp that could sort
    out of true insertion order. See RETAIL_SCHEMA_VERSION's v7 comment."""
    from database.schema import _migrate_sync_outbox_ordering_and_dead_letter

    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "retail.db")
        conn = _fresh_conn(path)
        _seed_v6_sync_outbox(conn)
        _migrate_sync_outbox_ordering_and_dead_letter(conn)
        conn.commit()

        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO sync_outbox (id, entity_type, entity_id, event_type, payload) VALUES (?,?,?,?,?)",
                (str(uuid.uuid4()), "category", "cat-x", "create", json.dumps({"id": "cat-x"})),
            )

        conn.close()


def test_migration_is_idempotent():
    from database.schema import _migrate_sync_outbox_ordering_and_dead_letter

    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "retail.db")
        conn = _fresh_conn(path)
        row_id = str(uuid.uuid4())
        _seed_v6_sync_outbox(conn, rows=[
            (row_id, "category", "cat-1", "create", {"id": "cat-1"}, "2026-08-06T00:00:00+00:00"),
        ])

        _migrate_sync_outbox_ordering_and_dead_letter(conn)
        conn.commit()
        before_tables = sorted(r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall())
        before_cols = sorted(r["name"] for r in conn.execute("PRAGMA table_info(sync_outbox)").fetchall())
        before_row = dict(conn.execute("SELECT * FROM sync_outbox WHERE id=?", (row_id,)).fetchone())

        _migrate_sync_outbox_ordering_and_dead_letter(conn)  # second call must be a clean no-op
        conn.commit()
        after_tables = sorted(r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall())
        after_cols = sorted(r["name"] for r in conn.execute("PRAGMA table_info(sync_outbox)").fetchall())
        after_row = dict(conn.execute("SELECT * FROM sync_outbox WHERE id=?", (row_id,)).fetchone())

        assert before_tables == after_tables
        assert before_cols == after_cols
        assert before_row == after_row

        conn.close()


def test_ensure_schema_version_advances_user_version_to_7():
    """Exercises the real ensure_schema_version path (integrity check, live
    backup, migrate, integrity re-check, advance PRAGMA user_version) --
    proves the v6->v7 bump is wired up correctly end to end, not merely that
    the migration body is correct in isolation. Mirrors
    retail_po_split_migration_test.py's identical test for v5->v6."""
    from database.schema import _migrate_sync_outbox_ordering_and_dead_letter
    from commercial_runtime.security.migration_safety import ensure_schema_version

    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "retail.db")
        backup_dir = os.path.join(tmp, "migration_backups")
        conn = _fresh_conn(path)
        _seed_v6_sync_outbox(conn, rows=[
            (str(uuid.uuid4()), "category", "cat-1", "create", {"id": "cat-1"}, "2026-08-06T00:00:00+00:00"),
        ])
        conn.execute("PRAGMA user_version = 6")
        conn.commit()

        ensure_schema_version(conn, path, 7, _migrate_sync_outbox_ordering_and_dead_letter, backup_dir=backup_dir)

        assert conn.execute("PRAGMA user_version").fetchone()[0] == 7
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(sync_outbox)").fetchall()}
        assert "attempt_count" in cols and "last_error" in cols

        # a later app launch against an already-migrated db: current(7) >=
        # target(7), so ensure_schema_version's fast path returns immediately
        # and migrate_fn never runs again -- version simply stays 7.
        ensure_schema_version(conn, path, 7, _migrate_sync_outbox_ordering_and_dead_letter, backup_dir=backup_dir)
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 7

        conn.close()


def test_migration_normalizes_legacy_space_separated_timestamps_and_preserves_true_insertion_order():
    """The exact scenario the v7 bump exists to close: a pre-fix install can
    have SOME rows that fell back to SQLite's own space-separated
    `CURRENT_TIMESTAMP` default (any row inserted by a writer that forgot to
    supply created_at) interleaved with rows that always had a proper
    T-separated isoformat() value (the sole real writer, _queue_sync_event).
    Under the OLD `ORDER BY created_at` (string comparison, space 0x20 sorts
    before 'T' 0x54), a space-separated row could sort ahead of an
    earlier-inserted T-separated row regardless of real insertion order.

    This seeds four rows in a specific insertion order (rowid 1..4) with a
    deliberately adversarial mix of formats/values, runs the migration (which
    normalizes every space-separated created_at to T-separated +00:00 --
    see _normalize_legacy_outbox_created_at), then proves
    SyncService.read_outbox -- the actual drain path push_once() uses, not
    just a raw SQL query -- returns them in TRUE insertion order afterward."""
    from database.schema import _migrate_sync_outbox_ordering_and_dead_letter
    from commercial_runtime.sync.sync_service import SyncService

    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "retail.db")
        conn = _fresh_conn(path)

        # Inserted in this exact order (rowid 1, 2, 3, 4), with created_at
        # VALUES that genuinely increase with real insertion time -- exactly
        # what CURRENT_TIMESTAMP would have actually recorded, since it is
        # the SQL engine's own clock at insert time; only the STRING FORMAT
        # is wrong for rows 2 and 4 (legacy space-separated, what a
        # fallback-to-DEFAULT row looked like pre-fix), not the value.
        _seed_v6_sync_outbox(conn, rows=[
            ("row-1", "category", "cat-1", "create", {"id": "cat-1"}, "2026-08-06T08:00:00+00:00"),
            ("row-2", "category", "cat-2", "create", {"id": "cat-2"}, "2026-08-06 09:00:00"),  # legacy default shape
            ("row-3", "category", "cat-3", "create", {"id": "cat-3"}, "2026-08-06T10:00:00+00:00"),
            ("row-4", "category", "cat-4", "create", {"id": "cat-4"}, "2026-08-06 11:00:00"),  # legacy default shape
        ])

        # Confirm the trap is real BEFORE the fix: naive created_at-only
        # string ordering groups both space-separated rows ahead of both
        # T-separated rows (space 0x20 sorts before 'T' 0x54 at the same
        # position) regardless of their actual chronological value, so this
        # is scrambled relative to true insertion order (row-1..row-4).
        naive_order = [r["id"] for r in conn.execute("SELECT id FROM sync_outbox ORDER BY created_at").fetchall()]
        assert naive_order == ["row-2", "row-4", "row-1", "row-3"]  # scrambled -- the bug, reproduced

        _migrate_sync_outbox_ordering_and_dead_letter(conn)
        conn.commit()

        # Every created_at is now T-separated (normalized) -- no row still
        # carries the old space-separated shape.
        for row in conn.execute("SELECT id, created_at FROM sync_outbox").fetchall():
            assert "T" in row["created_at"], f"{row['id']} was not normalized: {row['created_at']!r}"

        conn.row_factory = sqlite3.Row
        service = SyncService(client_factory=lambda: None, get_conn=lambda: conn)
        events = service.read_outbox(conn)
        drained_order = [e["id"] for e in events]

        assert drained_order == ["row-1", "row-2", "row-3", "row-4"]  # true insertion order, recovered

        conn.close()
