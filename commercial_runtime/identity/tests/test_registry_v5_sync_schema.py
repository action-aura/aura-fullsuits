"""Registry v5 -- registry.db gets its own sync_outbox/sync_cursor (Phase 5
wave B2 stage 1). See docs/launch-readiness/phase5-waveb2-user-sync.md
§Decision 1 and commercial_runtime/identity/registry_sync_schema.py's module
docstring for why registry.db needs its own outbox rather than sharing
retail.db's or writing through an ATTACH-ed cross-database transaction.

Proves the migration itself, not merely that the code compiles: build a
real registry.db at v4 (the version immediately before this one) through the
REAL `init_registry_db()` path, run the real v4->v5 upgrade, and confirm
`PRAGMA user_version` reads 5, both new tables exist with the expected
shape, and running the exact same upgrade a second time is a clean no-op
(no error, same shape, version stays 5) -- the "idempotent, additive, safe
to run twice" guarantee migration_safety.py's own docstring requires of
every step wired into `ensure_schema_version`.

Run (ONE FILE PER PYTEST PROCESS -- see products/run_all_tests.py):
    pytest commercial_runtime/identity/tests/test_registry_v5_sync_schema.py -v
"""
import sqlite3

import pytest

from commercial_runtime.identity import registry_db


@pytest.fixture
def v4_registry_db(tmp_path, monkeypatch):
    """A real registry.db, built by the real init path, pinned at v4 -- the
    version immediately before the one this file tests. Monkeypatches
    REGISTRY_SCHEMA_VERSION down to 4 for exactly this build:
    `init_registry_db()` reads the module attribute fresh at call time (it
    is not baked into a default argument anywhere), so this genuinely limits
    `_migrate_registry_schema` to running only its v1-v4 steps against a
    brand-new database -- a REAL v4 shape, not a hand-simulated one that
    could silently diverge from what v1-v4 actually leave behind.

    `_db_dir`/`DB_PATH` are also patched directly rather than via
    `AURA_APP_DATA`: registry_db.py resolves both from the environment ONCE,
    at import time (module already imported by the time this fixture runs),
    so setting the env var this late would never be picked up -- patching
    the already-resolved module attributes is what actually redirects every
    call in this test to an isolated tmp_path database instead of a real
    on-disk registry.db."""
    db_dir = tmp_path / "database"
    db_path = db_dir / "registry.db"
    monkeypatch.setattr(registry_db, "REGISTRY_SCHEMA_VERSION", 4)
    monkeypatch.setattr(registry_db, "_db_dir", str(db_dir))
    monkeypatch.setattr(registry_db, "DB_PATH", str(db_path))

    registry_db.init_registry_db()

    conn = sqlite3.connect(str(db_path))
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    conn.close()
    assert version == 4, f"fixture bug: expected a v4 database, got v{version}"
    return db_path


def _table_exists(db_path, name):
    conn = sqlite3.connect(str(db_path))
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    conn.close()
    return row is not None


def test_v4_to_v5_upgrade_adds_sync_outbox_and_cursor(v4_registry_db, monkeypatch):
    monkeypatch.setattr(registry_db, "REGISTRY_SCHEMA_VERSION", 5)

    registry_db.init_registry_db()  # the real upgrade path, not a hand-rolled ALTER

    assert _table_exists(v4_registry_db, "sync_outbox")
    assert _table_exists(v4_registry_db, "sync_cursor")

    conn = sqlite3.connect(str(v4_registry_db))
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 5

        outbox_cols = {row[1] for row in conn.execute("PRAGMA table_info(sync_outbox)").fetchall()}
        assert outbox_cols == {"id", "entity_type", "entity_id", "event_type", "payload", "created_at"}

        cursor_cols = {row[1] for row in conn.execute("PRAGMA table_info(sync_cursor)").fetchall()}
        assert cursor_cols == {"id", "last_seq"}

        cursor_row = conn.execute("SELECT id, last_seq FROM sync_cursor WHERE id=1").fetchone()
        assert cursor_row == (1, 0), (
            "sync_cursor must seed exactly one row, last_seq=0, matching retail's own "
            "sync_outbox/sync_cursor shape (products/retail/backend/database/schema.py)"
        )

        # integrity_check is already part of ensure_schema_version's own
        # post-migration gate (migration_safety.py) -- re-confirmed directly
        # here so this test does not merely trust that gate silently did its
        # job.
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"

        # Confirms this migration genuinely ran ADDITIVELY on top of the v4
        # shape, not against some fresh/empty database that never actually
        # exercised v1-v4's own steps -- users.uid (v3) must still be there.
        user_cols = {row[1] for row in conn.execute("PRAGMA table_info(users)").fetchall()}
        assert {"uid", "row_version", "updated_at_utc"} <= user_cols
    finally:
        conn.close()


def test_v5_upgrade_is_a_clean_no_op_when_run_twice(v4_registry_db, monkeypatch):
    monkeypatch.setattr(registry_db, "REGISTRY_SCHEMA_VERSION", 5)
    registry_db.init_registry_db()

    conn = sqlite3.connect(str(v4_registry_db))
    before_outbox_rows = conn.execute("SELECT COUNT(*) FROM sync_outbox").fetchone()[0]
    before_cursor = conn.execute("SELECT last_seq FROM sync_cursor WHERE id=1").fetchone()[0]
    conn.close()

    registry_db.init_registry_db()  # second call, already at v5 -- must be a pure no-op

    conn = sqlite3.connect(str(v4_registry_db))
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 5
        assert conn.execute("SELECT COUNT(*) FROM sync_outbox").fetchone()[0] == before_outbox_rows == 0
        assert conn.execute("SELECT last_seq FROM sync_cursor WHERE id=1").fetchone()[0] == before_cursor == 0
        assert conn.execute("SELECT COUNT(*) FROM sync_cursor").fetchone()[0] == 1, (
            "a second run must not insert a duplicate sync_cursor row"
        )
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        conn.close()


def test_apply_registry_sync_schema_is_idempotent_called_directly_twice(tmp_path):
    """Bypasses ensure_schema_version's version-gate fast path entirely --
    proves the MIGRATION FUNCTION ITSELF tolerates being re-run on the same
    connection, exactly the property every other step in
    `_migrate_registry_schema` already documents (see account_schema.py's
    own module docstring: "each step has to be a no-op against a database
    that already has its changes")."""
    from commercial_runtime.identity.registry_sync_schema import apply_registry_sync_schema

    db_path = tmp_path / "direct.db"
    conn = sqlite3.connect(str(db_path))
    try:
        apply_registry_sync_schema(conn)
        apply_registry_sync_schema(conn)  # must not raise, must not duplicate the seed row

        row_count = conn.execute("SELECT COUNT(*) FROM sync_cursor").fetchone()[0]
        assert row_count == 1
        assert conn.execute("SELECT last_seq FROM sync_cursor WHERE id=1").fetchone()[0] == 0
    finally:
        conn.close()
