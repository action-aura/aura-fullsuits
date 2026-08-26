"""Registry v6 -- registry.db gets its own sync_apply_quarantine (Phase 5
wave B2 stage 2a). See docs/launch-readiness/phase5-waveb2-user-sync.md
§Decision 4 and commercial_runtime/identity/registry_quarantine_schema.py's
module docstring for why registry.db needs this table before
`_apply_event`'s `user` branch can safely catch `users`' two non-wire UNIQUE
constraints (`email`, `UNIQUE(company_id, employee_id)`) rather than let
them escape as a raw, batch-wedging IntegrityError.

Proves the migration itself, not merely that the code compiles: build a
real registry.db at v5 (the version immediately before this one) through the
REAL `init_registry_db()` path, run the real v5->v6 upgrade, and confirm
`PRAGMA user_version` reads 6, the new table exists with the expected shape,
and running the exact same upgrade a second time is a clean no-op -- the
same "idempotent, additive, safe to run twice" guarantee
test_registry_v5_sync_schema.py already proves for v5.

Run (ONE FILE PER PYTEST PROCESS -- see products/run_all_tests.py):
    pytest commercial_runtime/identity/tests/test_registry_v6_quarantine_schema.py -v
"""
import sqlite3

import pytest

from commercial_runtime.identity import registry_db


@pytest.fixture
def v5_registry_db(tmp_path, monkeypatch):
    """A real registry.db, built by the real init path, pinned at v5 -- the
    version immediately before the one this file tests. Same technique as
    test_registry_v5_sync_schema.py's own `v4_registry_db` fixture -- see
    that fixture's docstring for why `_db_dir`/`DB_PATH` are patched
    directly rather than via `AURA_APP_DATA` (registry_db.py resolves both
    from the environment ONCE, at import time, already past by the time
    this fixture runs)."""
    db_dir = tmp_path / "database"
    db_path = db_dir / "registry.db"
    monkeypatch.setattr(registry_db, "REGISTRY_SCHEMA_VERSION", 5)
    monkeypatch.setattr(registry_db, "_db_dir", str(db_dir))
    monkeypatch.setattr(registry_db, "DB_PATH", str(db_path))

    registry_db.init_registry_db()

    conn = sqlite3.connect(str(db_path))
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    conn.close()
    assert version == 5, f"fixture bug: expected a v5 database, got v{version}"
    return db_path


def _table_exists(db_path, name):
    conn = sqlite3.connect(str(db_path))
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    conn.close()
    return row is not None


def test_v5_to_v6_upgrade_adds_sync_apply_quarantine(v5_registry_db, monkeypatch):
    monkeypatch.setattr(registry_db, "REGISTRY_SCHEMA_VERSION", 6)

    registry_db.init_registry_db()  # the real upgrade path, not a hand-rolled CREATE

    assert _table_exists(v5_registry_db, "sync_apply_quarantine")

    conn = sqlite3.connect(str(v5_registry_db))
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 6

        cols = {row[1] for row in conn.execute("PRAGMA table_info(sync_apply_quarantine)").fetchall()}
        assert cols == {
            "entity_id", "entity_type", "event_type", "payload", "reason",
            "detail", "quarantined_at",
        }

        pk_cols = {row[1] for row in conn.execute("PRAGMA table_info(sync_apply_quarantine)").fetchall()
                   if row[5] > 0}  # row[5] is the `pk` column, 1-indexed position when part of the PK
        assert pk_cols == {"entity_id", "event_type"}, (
            "composite PRIMARY KEY (entity_id, event_type) must match retail.db's own "
            "sync_apply_quarantine verbatim -- entity_id alone would be too coarse"
        )

        # integrity_check is already part of ensure_schema_version's own
        # post-migration gate -- re-confirmed directly here so this test
        # does not merely trust that gate silently did its job.
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"

        # Confirms this migration genuinely ran ADDITIVELY on top of the v5
        # shape, not against some fresh/empty database that never actually
        # exercised v1-v5's own steps -- sync_outbox/sync_cursor (v5) must
        # still be there.
        assert _table_exists(v5_registry_db, "sync_outbox")
        assert _table_exists(v5_registry_db, "sync_cursor")
        user_cols = {row[1] for row in conn.execute("PRAGMA table_info(users)").fetchall()}
        assert {"uid", "row_version", "updated_at_utc"} <= user_cols
    finally:
        conn.close()


def test_v6_upgrade_is_a_clean_no_op_when_run_twice(v5_registry_db, monkeypatch):
    monkeypatch.setattr(registry_db, "REGISTRY_SCHEMA_VERSION", 6)
    registry_db.init_registry_db()

    conn = sqlite3.connect(str(v5_registry_db))
    before_rows = conn.execute("SELECT COUNT(*) FROM sync_apply_quarantine").fetchone()[0]
    conn.close()

    registry_db.init_registry_db()  # second call, already at v6 -- must be a pure no-op

    conn = sqlite3.connect(str(v5_registry_db))
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 6
        assert conn.execute("SELECT COUNT(*) FROM sync_apply_quarantine").fetchone()[0] == before_rows == 0
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        conn.close()


def test_apply_registry_quarantine_schema_is_idempotent_called_directly_twice(tmp_path):
    """Bypasses ensure_schema_version's version-gate fast path entirely --
    proves the MIGRATION FUNCTION ITSELF tolerates being re-run on the same
    connection, matching account_schema.py's own documented requirement of
    every step wired into `_migrate_registry_schema`."""
    from commercial_runtime.identity.registry_quarantine_schema import apply_registry_quarantine_schema

    db_path = tmp_path / "direct.db"
    conn = sqlite3.connect(str(db_path))
    try:
        apply_registry_quarantine_schema(conn)
        apply_registry_quarantine_schema(conn)  # must not raise

        conn.execute(
            "INSERT INTO sync_apply_quarantine (entity_id, entity_type, event_type, payload, reason) "
            "VALUES ('e1','user','create','{}','test')"
        )
        conn.commit()
        assert conn.execute("SELECT COUNT(*) FROM sync_apply_quarantine").fetchone()[0] == 1
    finally:
        conn.close()
