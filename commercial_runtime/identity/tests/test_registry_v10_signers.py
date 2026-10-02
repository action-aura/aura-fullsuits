"""registry.db v10 -- `sync_signers` and the S-AUTH fix-round extension.

The unreleased v10 (AUDIT-033 / S-AUTH) was EXTENDED rather than re-versioned by
the fix round (verifier V findings #1, #4, #6, #7, #8), so a database that
reached v10 before the fix is brought forward by the next run of the migration
chain. Every part is shape-gated and idempotent; these tests pin the resulting
shape and the rebuild of `sync_apply_quarantine` (the `*_new` idiom: copy with a
computed `auth_key`, drop, rename, all in one transaction).

Run (ONE FILE PER PYTEST PROCESS):
    pytest commercial_runtime/identity/tests/test_registry_v10_signers.py -q
"""
from __future__ import annotations

import json
import sqlite3

import pytest

from commercial_runtime.identity import registry_db
from commercial_runtime.identity.sync_signers_schema import rebuild_quarantine_with_auth_key
from commercial_runtime.sync import authority

OLD_QUARANTINE = """
CREATE TABLE sync_apply_quarantine (
    entity_id TEXT NOT NULL, entity_type TEXT NOT NULL, event_type TEXT NOT NULL,
    payload TEXT NOT NULL, reason TEXT NOT NULL, detail TEXT,
    quarantined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (entity_id, event_type))
"""


@pytest.fixture
def registry(tmp_path, monkeypatch):
    db_dir = tmp_path / "database"
    monkeypatch.setattr(registry_db, "_db_dir", str(db_dir))
    monkeypatch.setattr(registry_db, "DB_PATH", str(db_dir / "registry.db"))
    registry_db.init_registry_db()
    conn = sqlite3.connect(str(db_dir / "registry.db"))
    yield conn
    conn.close()


def _cols(conn, table):
    return [r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]


def _pk(conn, table):
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall() if r[5] > 0}


def _signed_payload(sig="c2ln"):
    return {"uid": "u1", "_auth": {"v": 1, "inst": "i", "dev": "d", "pk": "k", "sig": sig, "lease": {}}}


def test_a_fresh_registry_has_every_v10_part(registry):
    assert registry.execute("PRAGMA user_version").fetchone()[0] == 10
    assert _cols(registry, "sync_signers") == [
        "device_id", "company_id", "installation_id", "key_fingerprint", "first_bound_at_utc",
        "last_signed_seen_at_utc", "lease_issued_at_utc"]
    assert _cols(registry, "sync_signed_seen") == [
        "sig_sha256", "device_id", "lease_issued_at_utc", "first_seen_at_utc"]
    assert _pk(registry, "sync_signed_seen") == {"sig_sha256"}
    assert registry.execute(
        "SELECT 1 FROM sqlite_master WHERE type='index' AND name='idx_sync_signed_seen_lease'").fetchone()
    assert "main_row_version" in _cols(registry, "users")
    assert "auth_key" in _cols(registry, "sync_apply_quarantine")
    assert _pk(registry, "sync_apply_quarantine") == {"entity_id", "event_type", "auth_key"}


def test_the_quarantine_key_includes_auth_key_so_two_signatures_coexist(registry):
    for key in ("sig:aaa", "sig:bbb", ""):
        registry.execute(
            "INSERT INTO sync_apply_quarantine (entity_id, entity_type, event_type, payload, reason, auth_key) "
            "VALUES ('e','user','update','{}','r',?)", (key,))
    with pytest.raises(sqlite3.IntegrityError):
        registry.execute(
            "INSERT INTO sync_apply_quarantine (entity_id, entity_type, event_type, payload, reason, auth_key) "
            "VALUES ('e','user','update','{}','r','sig:aaa')")


def test_the_v6_step_by_itself_still_creates_the_two_column_key(tmp_path):
    """The v6 step's own CREATE is unchanged (the rebuild is the v10 step's job), so a
    registry upgraded step by step passes through the old shape and the rebuild fixes it."""
    from commercial_runtime.identity.registry_quarantine_schema import apply_registry_quarantine_schema
    conn = sqlite3.connect(str(tmp_path / "v6.db"))
    apply_registry_quarantine_schema(conn)
    assert _pk(conn, "sync_apply_quarantine") == {"entity_id", "event_type"}
    assert "auth_key" not in _cols(conn, "sync_apply_quarantine")
    assert rebuild_quarantine_with_auth_key(conn) is True
    assert _pk(conn, "sync_apply_quarantine") == {"entity_id", "event_type", "auth_key"}
    conn.close()


def test_rebuild_copies_every_row_with_its_computed_key_and_is_idempotent(tmp_path):
    conn = sqlite3.connect(str(tmp_path / "old.db"))
    conn.execute(OLD_QUARANTINE)
    signed = _signed_payload()
    conn.executemany(
        "INSERT INTO sync_apply_quarantine (entity_id, entity_type, event_type, payload, reason, detail, "
        "quarantined_at) VALUES (?,?,?,?,?,?,?)", [
            ("signed", "user", "update", json.dumps(signed), "owner_change_needs_main", "d1", "2026-01-01 10:00:00"),
            ("unsigned", "user", "update", json.dumps({"uid": "u2"}), "unsigned_authority_event", "d2", "2026-01-02 10:00:00"),
            ("garbled", "user", "update", "not json at all", "malformed_user", None, "2026-01-03 10:00:00"),
            ("sig-not-text", "user", "update", json.dumps({"_auth": {"sig": 5}}), "bad_signature", "d4", "2026-01-04 10:00:00"),
        ])
    conn.commit()
    assert rebuild_quarantine_with_auth_key(conn) is True
    rows = {r[0]: r for r in conn.execute(
        "SELECT entity_id, auth_key, reason, detail, quarantined_at, payload FROM sync_apply_quarantine")}
    # Review V3r G2: an authority row's key is the content hash of its whole payload
    # (it was "sig:<hash of the signature text>" for a signed row and '' for the
    # rest). The migration calls the SAME function a live park does, so each row is
    # compared with that function's answer. What this can no longer catch: a key
    # that is '' for an unsigned authority row -- deliberately gone (a '' key let a
    # copy of a signature replace a parked payload).
    assert rows["signed"][1] == authority.quarantine_auth_key(signed, "user") and len(rows["signed"][1]) == 64
    assert rows["unsigned"][1] == authority.quarantine_auth_key({"uid": "u2"}, "user") != ""
    assert rows["garbled"][1] == authority.quarantine_auth_key(None, "user")
    assert rows["sig-not-text"][1] == authority.quarantine_auth_key({"_auth": {"sig": 5}}, "user")
    assert len({rows[k][1] for k in rows}) == 4
    assert rows["signed"][2:5] == ("owner_change_needs_main", "d1", "2026-01-01 10:00:00")     # nothing else moved
    assert rows["garbled"][3] is None and rows["garbled"][5] == "not json at all"
    assert len(rows) == 4
    assert not conn.execute("SELECT 1 FROM sqlite_master WHERE name='sync_apply_quarantine_new'").fetchone()
    assert rebuild_quarantine_with_auth_key(conn) is False                                      # a no-op the second time
    assert conn.execute("SELECT COUNT(*) FROM sync_apply_quarantine").fetchone()[0] == 4
    assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    conn.close()


def test_rebuild_with_no_table_does_nothing(tmp_path):
    conn = sqlite3.connect(str(tmp_path / "bare.db"))
    assert rebuild_quarantine_with_auth_key(conn) is False
    conn.close()


def test_a_failed_rebuild_rolls_back_to_the_old_shape(tmp_path, monkeypatch):
    """One transaction: if the copy fails half way the old table is untouched and no `_new` table is left."""
    conn = sqlite3.connect(str(tmp_path / "old.db"))
    conn.execute(OLD_QUARANTINE)
    conn.executemany(
        "INSERT INTO sync_apply_quarantine (entity_id, entity_type, event_type, payload, reason) VALUES (?,?,?,?,?)",
        [("a", "user", "update", "{}", "r"), ("b", "user", "update", "{}", "r")])
    conn.commit()
    calls = {"n": 0}
    real = authority.quarantine_auth_key

    def boom(payload, entity_type=None):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("disk full")
        return real(payload, entity_type)

    monkeypatch.setattr(authority, "quarantine_auth_key", boom)
    with pytest.raises(RuntimeError):
        rebuild_quarantine_with_auth_key(conn)
    assert "auth_key" not in _cols(conn, "sync_apply_quarantine")
    assert conn.execute("SELECT COUNT(*) FROM sync_apply_quarantine").fetchone()[0] == 2
    assert not conn.execute("SELECT 1 FROM sqlite_master WHERE name='sync_apply_quarantine_new'").fetchone()
    monkeypatch.setattr(authority, "quarantine_auth_key", real)
    assert rebuild_quarantine_with_auth_key(conn) is True          # a clean retry works
    conn.close()


def test_a_registry_that_reached_the_first_v10_is_brought_forward_by_the_chain(registry):
    """A database migrated by the ORIGINAL v10 (no lease stamp, no seen table, no main_row_version,
    two-column quarantine key) has every part added the next time the chain runs, keeping its rows."""
    registry.execute("DROP TABLE sync_signers")
    registry.execute("""CREATE TABLE sync_signers (
        device_id TEXT PRIMARY KEY, company_id TEXT NOT NULL, installation_id TEXT NOT NULL UNIQUE,
        key_fingerprint TEXT NOT NULL, first_bound_at_utc TEXT NOT NULL, last_signed_seen_at_utc TEXT NOT NULL)""")
    registry.execute("DROP TABLE sync_signed_seen")
    registry.execute("ALTER TABLE users DROP COLUMN main_row_version")
    registry.execute("DROP TABLE sync_apply_quarantine")
    registry.execute(OLD_QUARANTINE)
    registry.execute("INSERT INTO sync_signers VALUES ('d','c','i','f','2026-01-01T00:00:00+00:00','2026-01-02T00:00:00+00:00')")
    registry.execute(
        "INSERT INTO sync_apply_quarantine (entity_id, entity_type, event_type, payload, reason) VALUES (?,?,?,?,?)",
        ("signed", "user", "update", json.dumps(_signed_payload()), "owner_change_needs_main"))
    registry.commit()

    registry_db._migrate_registry_schema(registry)

    assert "lease_issued_at_utc" in _cols(registry, "sync_signers")
    assert registry.execute("SELECT device_id, last_signed_seen_at_utc, lease_issued_at_utc FROM sync_signers"
                            ).fetchone() == ("d", "2026-01-02T00:00:00+00:00", None)
    assert _cols(registry, "sync_signed_seen")
    assert "main_row_version" in _cols(registry, "users")
    assert registry.execute("SELECT auth_key FROM sync_apply_quarantine").fetchone()[0] ==         authority.quarantine_auth_key(_signed_payload(), "user")
    registry_db._migrate_registry_schema(registry)                  # and a second run is a no-op
    assert registry.execute("SELECT COUNT(*) FROM sync_apply_quarantine").fetchone()[0] == 1
    assert registry.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
