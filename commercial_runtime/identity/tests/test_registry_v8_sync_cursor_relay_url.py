"""Registry v8 -- registry.db's `sync_cursor` gains `relay_url`, the column
retail.db's own `sync_cursor` has had since retail schema v31.

WHY THIS MIGRATION EXISTS, measured rather than reasoned:

    GET /api/registry-sync/_internal/cursor
    sqlite3.OperationalError: no such column: relay_url   -> HTTP 500

Found in logcat on a real phone, then reproduced against a fresh registry
database built by `apply_registry_sync_schema` alone -- so it was every
install, not one stale device. `products/retail/backend/app.py` registers
`make_sync_internal_blueprint` TWICE: once at `/api/sync` backed by the
retail connection, once at `/api/registry-sync` backed by registry_db's. The
shared `_internal/cursor` route calls
`SyncService.ensure_cursor_matches_relay()` whenever the client sends a
`relay_url` query parameter, and Android's `SyncCoordinator` sends it. Retail
v31 added the column to retail.db only, so the registry stream queried a
column that lives in the other database.

Proves the migration, not that the code compiles: builds a REAL v7
registry.db through the REAL `init_registry_db()` path -- the version
immediately before this one, and the version that reproduces the defect --
then runs the real v7->v8 upgrade, following
test_registry_v5_sync_schema.py's established fixture shape exactly.

Run (ONE FILE PER PYTEST PROCESS -- see products/run_all_tests.py):
    pytest commercial_runtime/identity/tests/test_registry_v8_sync_cursor_relay_url.py -v
"""
import sqlite3

import pytest

from commercial_runtime.identity import registry_db


def _cursor_columns(db_path):
    conn = sqlite3.connect(str(db_path))
    try:
        return {row[1] for row in conn.execute("PRAGMA table_info(sync_cursor)").fetchall()}
    finally:
        conn.close()


def _user_version(db_path):
    conn = sqlite3.connect(str(db_path))
    try:
        return conn.execute("PRAGMA user_version").fetchone()[0]
    finally:
        conn.close()


@pytest.fixture
def v7_registry_db(tmp_path, monkeypatch):
    """A real registry.db pinned at v7 -- the version immediately before this
    one. Same technique as test_registry_v5_sync_schema.py's fixture and for
    the same reasons: REGISTRY_SCHEMA_VERSION is read fresh at call time so
    pinning it genuinely limits the migration to its v1-v7 steps, and
    `_db_dir`/`DB_PATH` are patched directly because registry_db.py resolves
    both from the environment ONCE at import time.
    """
    db_dir = tmp_path / "database"
    db_path = db_dir / "registry.db"
    monkeypatch.setattr(registry_db, "REGISTRY_SCHEMA_VERSION", 7)
    monkeypatch.setattr(registry_db, "_db_dir", str(db_dir))
    monkeypatch.setattr(registry_db, "DB_PATH", str(db_path))

    registry_db.init_registry_db()

    assert _user_version(db_path) == 7, (
        f"fixture bug: expected a v7 database, got v{_user_version(db_path)}")
    return db_path


"""A FIRST VERSION OF THIS FILE ASSERTED SOMETHING FALSE, and the note is
kept because the misunderstanding is easy to repeat.

It tried to pin the defect by building a database with
REGISTRY_SCHEMA_VERSION monkeypatched to 7 and asserting `relay_url` was
absent. It is not: `_migrate_registry_schema` is documented as "run every
step, each independently idempotent" -- the STEPS are not individually
version-gated. The version integer decides only WHETHER the chain runs at
all, never which parts of it do. So once v8's ALTER exists in
`apply_registry_sync_schema`, even a database being built "at v7" gets the
column, and the assertion failed for a correct reason.

That is also exactly why the version bump is the load-bearing half of this
migration, and it is worth being precise about: `ensure_schema_version`
skips `migrate_fn` ENTIRELY when `user_version >= target`. An installed
device sitting at v7 would therefore never re-run the chain and would keep
returning HTTP 500 forever, no matter how correct the ALTER was. Bumping to
8 is what drags those databases back through it.

So the legacy shape is built explicitly below, with the exact SQL
registry_sync_schema.py used before v8, rather than by pretending a version
number can produce it.
"""


def _legacy_pre_v8_cursor(db_path):
    """Rebuild `sync_cursor` in its pre-v8 shape -- the schema every device
    in the field actually has -- and return the connection's path. Uses the
    literal v5 definition rather than DROP COLUMN, so this does not depend
    on the host SQLite being new enough to support it."""
    conn = sqlite3.connect(str(db_path))
    try:
        seq = conn.execute("SELECT last_seq FROM sync_cursor WHERE id = 1").fetchone()[0]
        conn.execute("DROP TABLE sync_cursor")
        conn.execute(
            "CREATE TABLE sync_cursor ("
            "  id INTEGER PRIMARY KEY CHECK (id = 1),"
            "  last_seq INTEGER NOT NULL DEFAULT 0"
            ")")
        conn.execute("INSERT INTO sync_cursor (id, last_seq) VALUES (1, ?)", (seq,))
        conn.commit()
    finally:
        conn.close()
    return db_path


def test_the_pre_v8_shape_really_does_reproduce_the_defect(v7_registry_db):
    """The bug, pinned against the shape devices actually have. If this stops
    raising, the defect is no longer what this migration claims to fix and
    the upgrade test below would be passing for the wrong reason."""
    _legacy_pre_v8_cursor(v7_registry_db)

    cols = _cursor_columns(v7_registry_db)
    assert cols == {"id", "last_seq"}, (
        f"pre-v8 sync_cursor columns are {sorted(cols)}; expected exactly "
        "['id', 'last_seq'], the shape registry_sync_schema.py created before v8.")

    conn = sqlite3.connect(str(v7_registry_db))
    try:
        with pytest.raises(sqlite3.OperationalError, match="no such column: relay_url"):
            conn.execute("SELECT relay_url FROM sync_cursor").fetchone()
    finally:
        conn.close()


def test_the_migration_upgrades_a_real_pre_v8_database(v7_registry_db):
    """The field case: a device whose sync_cursor predates v8 gains the
    column when the chain runs, and keeps its high-water mark."""
    _legacy_pre_v8_cursor(v7_registry_db)
    conn = sqlite3.connect(str(v7_registry_db))
    try:
        conn.execute("UPDATE sync_cursor SET last_seq = 991 WHERE id = 1")
        conn.commit()
    finally:
        conn.close()

    from commercial_runtime.identity.registry_sync_schema import apply_registry_sync_schema
    conn = sqlite3.connect(str(v7_registry_db))
    try:
        apply_registry_sync_schema(conn)
        row = conn.execute("SELECT last_seq, relay_url FROM sync_cursor WHERE id = 1").fetchone()
    finally:
        conn.close()

    assert "relay_url" in _cursor_columns(v7_registry_db)
    assert row[0] == 991, f"the upgrade clobbered last_seq: {row[0]}"
    assert row[1] is None


def test_the_version_was_bumped_so_installed_devices_re_run_the_chain():
    """The load-bearing half. ensure_schema_version skips migrate_fn entirely
    when user_version >= target, so without this bump every already-installed
    device stays at its old version, never re-runs the chain, and keeps
    returning HTTP 500 from /api/registry-sync/_internal/cursor."""
    assert registry_db.REGISTRY_SCHEMA_VERSION >= 8, (
        f"REGISTRY_SCHEMA_VERSION is {registry_db.REGISTRY_SCHEMA_VERSION}; the "
        "relay_url migration only reaches installed databases if the version "
        "advances past what they already record.")


def test_v7_to_v8_upgrade_adds_relay_url(v7_registry_db, monkeypatch):
    monkeypatch.setattr(registry_db, "REGISTRY_SCHEMA_VERSION", 8)
    registry_db.init_registry_db()

    assert _user_version(v7_registry_db) == 8
    cols = _cursor_columns(v7_registry_db)
    assert "relay_url" in cols, (
        f"v8 did not add sync_cursor.relay_url; columns are {sorted(cols)}. "
        "Every /api/registry-sync/_internal/cursor call that passes a "
        "relay_url parameter still returns HTTP 500.")


def test_the_new_column_is_nullable_and_starts_null(v7_registry_db, monkeypatch):
    """NULL is not incidental -- it is the signal
    `SyncService.ensure_cursor_matches_relay` reads as "not bound to any
    known relay", which makes the first pull after upgrading reset last_seq
    to 0 exactly once. A DEFAULT would suppress that reset and leave a
    high-water mark pointing into a relay it was never measured against,
    which is the confusion retail v31 exists to prevent."""
    monkeypatch.setattr(registry_db, "REGISTRY_SCHEMA_VERSION", 8)
    registry_db.init_registry_db()

    conn = sqlite3.connect(str(v7_registry_db))
    try:
        info = {row[1]: row for row in conn.execute("PRAGMA table_info(sync_cursor)").fetchall()}
        row = info["relay_url"]
        assert row[3] == 0, "relay_url must be nullable (NOT NULL would break the seed row)"
        assert row[4] is None, f"relay_url must have no DEFAULT, got {row[4]!r}"

        value = conn.execute("SELECT relay_url FROM sync_cursor WHERE id = 1").fetchone()
        assert value is not None, "the seed cursor row disappeared during the upgrade"
        assert value[0] is None, f"relay_url should start NULL, got {value[0]!r}"
    finally:
        conn.close()


def test_the_seed_cursor_row_and_its_last_seq_survive_the_upgrade(v7_registry_db, monkeypatch):
    """An ALTER TABLE that silently reset a live cursor would make every
    device re-pull the entire history, every upgrade. Set a non-zero
    high-water mark on the v7 database first, then prove the upgrade
    preserves it -- the column is added, the row is not rewritten."""
    conn = sqlite3.connect(str(v7_registry_db))
    try:
        conn.execute("UPDATE sync_cursor SET last_seq = 4242 WHERE id = 1")
        conn.commit()
    finally:
        conn.close()

    monkeypatch.setattr(registry_db, "REGISTRY_SCHEMA_VERSION", 8)
    registry_db.init_registry_db()

    conn = sqlite3.connect(str(v7_registry_db))
    try:
        row = conn.execute("SELECT last_seq, relay_url FROM sync_cursor WHERE id = 1").fetchone()
    finally:
        conn.close()
    assert row[0] == 4242, f"the upgrade clobbered last_seq: {row[0]}"
    assert row[1] is None


def test_running_the_upgrade_twice_is_a_clean_no_op(v7_registry_db, monkeypatch):
    """`ALTER TABLE ... ADD COLUMN` raises "duplicate column name" on a
    second run, which is why this step is guarded by PRAGMA table_info at
    all. migration_safety.py requires every step wired into
    `ensure_schema_version` to be safe to run twice."""
    monkeypatch.setattr(registry_db, "REGISTRY_SCHEMA_VERSION", 8)
    registry_db.init_registry_db()
    first = _cursor_columns(v7_registry_db)

    registry_db.init_registry_db()          # again, same version
    assert _user_version(v7_registry_db) == 8
    assert _cursor_columns(v7_registry_db) == first

    # And directly, bypassing the version gate that would otherwise skip it:
    from commercial_runtime.identity.registry_sync_schema import apply_registry_sync_schema
    conn = sqlite3.connect(str(v7_registry_db))
    try:
        apply_registry_sync_schema(conn)    # must not raise
        apply_registry_sync_schema(conn)
        assert {row[1] for row in conn.execute("PRAGMA table_info(sync_cursor)")} == first
    finally:
        conn.close()


def test_a_fresh_v8_database_has_the_column_too(tmp_path, monkeypatch):
    """The upgrade path and the fresh-install path must agree. A migration
    that only works for existing databases leaves every NEW install broken,
    which is how this defect shipped in the first place: retail's v31 moved
    and the registry copy was created without it."""
    db_dir = tmp_path / "fresh"
    db_path = db_dir / "registry.db"
    monkeypatch.setattr(registry_db, "REGISTRY_SCHEMA_VERSION", 8)
    monkeypatch.setattr(registry_db, "_db_dir", str(db_dir))
    monkeypatch.setattr(registry_db, "DB_PATH", str(db_path))

    registry_db.init_registry_db()

    assert _user_version(db_path) == 8
    assert "relay_url" in _cursor_columns(db_path)
