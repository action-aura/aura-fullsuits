"""SYNC-BOOT Phase 2 (retail schema v56): `site_device_cursors` per (installation, stream)
and `site_prune_state`, through the REAL migration path.

The deny half and the allow half of "rows survive": an existing hub's cursor rows must
come through the rebuild as stream 'legacy' with their positions intact (losing them
would reset every paired device to 0 and pin pruning there; changing them would let a
device skip history), and the new primary key must now ALLOW a second stream row for
the same device (the whole point -- the old key could not).

Run: pytest products/retail/tests/retail_site_stream_cursors_migration_test.py -q
"""
from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path

import pytest

for _k in ("AURA_SYNC_RELAY_URL", "AURA_OWNER_LICENSING_URL"):
    os.environ.pop(_k, None)

_REPO_ROOT = Path(__file__).resolve().parents[3]
_RETAIL_BACKEND_DIR = _REPO_ROOT / "products" / "retail" / "backend"
for _p in (str(_REPO_ROOT), str(_RETAIL_BACKEND_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import database.schema as schema  # noqa: E402
from commercial_runtime.identity import registry_db  # noqa: E402

#: v30's table exactly as the pre-v56 chain created it (single-column primary key).
OLD_SHAPE = """
CREATE TABLE site_device_cursors (
    installation_id TEXT PRIMARY KEY,
    last_seq        INTEGER NOT NULL DEFAULT 0,
    updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
"""


@pytest.fixture
def install(tmp_path, monkeypatch):
    reg_dir = tmp_path / "registry_db"
    monkeypatch.setattr(registry_db, "_db_dir", str(reg_dir))
    monkeypatch.setattr(registry_db, "DB_PATH", str(reg_dir / "registry.db"))
    registry_db.init_registry_db()
    root = tmp_path / "install"
    subsys = root / "subsystems"
    subsys.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(schema, "BASE_DIR", str(root))
    monkeypatch.setattr(schema, "SUBSYS_DIR", str(subsys))
    schema.init_retail()
    return subsys / "retail.db"


def _conn(path):
    c = sqlite3.connect(str(path), timeout=30)
    c.row_factory = sqlite3.Row
    return c


def _pk(conn, table):
    return [r["name"] for r in sorted(
        (r for r in conn.execute(f'PRAGMA table_info("{table}")').fetchall() if r["pk"]),
        key=lambda r: r["pk"])]


def test_a_fresh_install_is_stamped_56(install):
    c = _conn(install)
    try:
        assert schema.RETAIL_SCHEMA_VERSION == 56
        assert c.execute("PRAGMA user_version").fetchone()[0] == 56
        assert _pk(c, "site_device_cursors") == ["installation_id", "stream"]
        assert c.execute("SELECT pruned_through_seq, site_log_floor FROM site_prune_state WHERE id=1"
                         ).fetchone()[:] == (0, 0)
    finally:
        c.close()


def test_a_second_stream_row_for_the_same_device_is_now_allowed(install):
    c = _conn(install)
    try:
        c.execute("INSERT INTO site_device_cursors (installation_id, stream, last_seq) VALUES ('d','retail',5)")
        c.execute("INSERT INTO site_device_cursors (installation_id, stream, last_seq) VALUES ('d','registry',2)")
        with pytest.raises(sqlite3.IntegrityError):
            c.execute("INSERT INTO site_device_cursors (installation_id, stream, last_seq) VALUES ('d','retail',9)")
    finally:
        c.close()


def test_a_v54_hub_database_is_upgraded_through_the_real_chain_and_keeps_its_cursors(install):
    """Turn a freshly migrated database back into a v54 hub (old table, no prune state, stamped 54),
    then let the REAL init run again: ensure_schema_version takes its backup, migrates, checks."""
    c = _conn(install)
    try:
        c.execute("DROP TABLE site_device_cursors")
        c.execute(OLD_SHAPE)
        c.execute("INSERT INTO site_device_cursors (installation_id, last_seq) VALUES ('tablet', 41)")
        c.execute("INSERT INTO site_device_cursors (installation_id, last_seq) VALUES ('phone', 7)")
        c.execute("DROP TABLE site_prune_state")
        c.execute("PRAGMA user_version = 54")
        c.commit()
    finally:
        c.close()

    schema.init_retail()

    c = _conn(install)
    try:
        assert c.execute("PRAGMA user_version").fetchone()[0] == 56
        assert _pk(c, "site_device_cursors") == ["installation_id", "stream"]
        rows = {(r["installation_id"], r["stream"]): r["last_seq"]
                for r in c.execute("SELECT * FROM site_device_cursors")}
        assert rows == {("tablet", "legacy"): 41, ("phone", "legacy"): 7}
        # Nothing was ever pruned under the SYNC-JOIN stop-gap: claiming otherwise would tell every
        # paired device to resync for events that were never deleted.
        assert tuple(c.execute("SELECT pruned_through_seq, site_log_floor FROM site_prune_state").fetchone()) == (0, 0)
    finally:
        c.close()


def test_the_step_is_idempotent_and_replaying_it_changes_nothing(install):
    c = _conn(install)
    try:
        c.execute("INSERT INTO site_device_cursors (installation_id, stream, last_seq) VALUES ('d','retail',5)")
        c.execute("UPDATE site_prune_state SET pruned_through_seq = 12, site_log_floor = 3")
        c.commit()
        schema._migrate_add_site_stream_cursors_and_prune_state(c)
        schema._migrate_add_site_stream_cursors_and_prune_state(c)
        c.commit()
        assert [tuple(r) for r in c.execute("SELECT installation_id, stream, last_seq FROM site_device_cursors")] == [
            ("d", "retail", 5)]
        # INSERT OR IGNORE: a replay must not reset the recorded prune state to the seed.
        assert tuple(c.execute("SELECT pruned_through_seq, site_log_floor FROM site_prune_state").fetchone()) == (12, 3)
    finally:
        c.close()


def test_a_stray_new_table_from_a_crashed_rebuild_does_not_block_the_step(install):
    c = _conn(install)
    try:
        c.execute("DROP TABLE site_device_cursors")
        c.execute(OLD_SHAPE)
        c.execute("INSERT INTO site_device_cursors (installation_id, last_seq) VALUES ('t', 3)")
        c.execute("CREATE TABLE site_device_cursors_new (junk TEXT)")
        c.commit()
        schema._migrate_add_site_stream_cursors_and_prune_state(c)
        c.commit()
        assert [tuple(r) for r in c.execute("SELECT installation_id, stream, last_seq FROM site_device_cursors")] == [
            ("t", "legacy", 3)]
        assert not c.execute("SELECT 1 FROM sqlite_master WHERE name='site_device_cursors_new'").fetchone()
    finally:
        c.close()


def test_the_site_relay_step_alone_builds_the_v56_shape(tmp_path):
    """Several hub tests (and any tool) build a hub database with `_migrate_add_site_relay` alone."""
    c = sqlite3.connect(str(tmp_path / "x.db"))
    try:
        schema._migrate_add_site_relay(c)
        assert _pk_plain(c, "site_device_cursors") == ["installation_id", "stream"]
        assert c.execute("SELECT COUNT(*) FROM site_prune_state").fetchone()[0] == 1
    finally:
        c.close()


def _pk_plain(conn, table):
    return [r[1] for r in sorted((r for r in conn.execute(f'PRAGMA table_info("{table}")').fetchall() if r[5]),
                                 key=lambda r: r[5])]
