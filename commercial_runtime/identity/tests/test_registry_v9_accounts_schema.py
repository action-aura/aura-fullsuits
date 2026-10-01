"""Registry v9 -- ACCOUNTS half: `users.display_name`, `users.role_preset`, and
the four `retail.books.*` capability rows.

Proves the migration on a COPY of a real lab registry.db, not on a hand-built
schema: census copy `proof-a2` (a v8 registry with an admin and a cashier), see
tmp/p2s/census/census.md. The copy is taken again into pytest's tmp_path
(database + -wal + -shm) so the census copy stays pristine and no live
AuraLab / %LOCALAPPDATA% database is ever opened for writing.

No lab copy has a MANAGER, and the manager is the account the books rows exist
to protect (it must NOT silently gain money-books access on upgrade). So the
test builds the pre-v9 situation explicitly: it adds a manager to the v8 copy
and then strips every `retail.books.*` row, which is exactly what a v8 build
left behind (a v8 build only knows eight codes).

What each test guards, and how it was mutation-proved, is recorded in the
per-test comments. The migration tests count the EFFECT (rows, outbox, columns),
never just "did not raise".

Run (ONE FILE PER PYTEST PROCESS):
    pytest commercial_runtime/identity/tests/test_registry_v9_accounts_schema.py -v
"""
import os
import shutil
import sqlite3

import pytest

from commercial_runtime.identity import registry_db
from commercial_runtime.identity import user_accounts as ua

CENSUS = r"C:\Users\MSI\.claude\jobs\c5a4f4c4\tmp\p2s\census\copies\proof-a2\database"

BOOKS = sorted(c for c in ua.CAPABILITY_CODES if c.startswith("retail.books."))


def _copy_census(tmp_path):
    src = os.path.join(CENSUS, "registry.db")
    if not os.path.exists(src):
        pytest.skip("census copy not present on this machine")
    db_dir = tmp_path / "database"
    db_dir.mkdir()
    for suffix in ("", "-wal", "-shm"):
        if os.path.exists(src + suffix):
            shutil.copy2(src + suffix, str(db_dir / ("registry.db" + suffix)))
    return db_dir, db_dir / "registry.db"


def _q(db_path, sql, args=()):
    conn = sqlite3.connect(str(db_path))
    try:
        return conn.execute(sql, args).fetchall()
    finally:
        conn.close()


def _add_manager_and_strip_books(db_path):
    """Make the copy look like a v8 install that has a manager: clone the
    cashier row as a manager, give it the eight pre-v9 rows, then delete every
    books row for EVERY user (a v8 build never wrote them)."""
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        cashier = dict(conn.execute("SELECT * FROM users WHERE role='cashier'").fetchone())
        cashier.update(id="mgr-local-id", employee_id="mgr-emp", email="mgr@example.test",
                       uid="mgr-uid-0001", role="manager")
        cols = ",".join(cashier)
        conn.execute(f"INSERT INTO users ({cols}) VALUES ({','.join('?' * len(cashier))})",
                     list(cashier.values()))
        ua.seed_capabilities_for_user(conn, "mgr-local-id", "manager", emit_sync=False)
        conn.execute("DELETE FROM user_permissions WHERE subsystem LIKE 'retail.books.%'")
        conn.commit()
    finally:
        conn.close()


@pytest.fixture
def v8_with_manager(tmp_path, monkeypatch):
    db_dir, db_path = _copy_census(tmp_path)
    _add_manager_and_strip_books(db_path)
    assert _q(db_path, "PRAGMA user_version")[0][0] == 8
    assert _q(db_path, "SELECT COUNT(*) FROM user_permissions WHERE subsystem LIKE 'retail.books.%'")[0][0] == 0
    monkeypatch.setattr(registry_db, "_db_dir", str(db_dir))
    monkeypatch.setattr(registry_db, "DB_PATH", str(db_path))
    return db_dir, db_path


def _books(db_path, role):
    return {
        (r[0], r[1])
        for r in _q(
            db_path,
            "SELECT p.subsystem, p.access_level FROM user_permissions p "
            "JOIN users u ON u.id = p.user_id WHERE u.role=? AND p.subsystem LIKE 'retail.books.%'",
            (role,),
        )
    }


def test_migration_gives_admin_full_and_manager_none_on_the_books(v8_with_manager):
    db_dir, db_path = v8_with_manager
    registry_db.init_registry_db()

    # S-AUTH (AUDIT-033) moved the registry to v10 on top of v9: this migrates a v8 copy
    # through the REAL current chain, so the final marker is 10. The v9 step's own claims
    # (columns, backfill, no emit, backup taken) are asserted unchanged below.
    assert _q(db_path, "PRAGMA user_version")[0][0] == 10
    assert _books(db_path, "admin") == {(c, "full") for c in BOOKS}
    # THE guard: a manager that existed before v9 must not gain the books.
    # Mutation (ROLE_MANAGER without the books subtraction) makes this go RED.
    assert _books(db_path, "manager") == {(c, "none") for c in BOOKS}
    assert _books(db_path, "cashier") == {(c, "none") for c in BOOKS}
    assert len(BOOKS) == 4


def test_migration_emits_nothing_to_the_outbox(v8_with_manager):
    db_dir, db_path = v8_with_manager
    before = _q(db_path, "SELECT COUNT(*) FROM sync_outbox")[0][0]
    registry_db.init_registry_db()
    # Count the effect: the books rows DID land (so the seed ran) ...
    assert _q(db_path, "SELECT COUNT(*) FROM user_permissions WHERE subsystem LIKE 'retail.books.%'")[0][0] == 12
    # ... and not one of them told a peer. Mutation (emit_sync=True in the
    # seed) makes this go RED.
    assert _q(db_path, "SELECT COUNT(*) FROM sync_outbox")[0][0] == before == 0


def test_migration_adds_two_nullable_columns_and_touches_no_existing_row(v8_with_manager):
    db_dir, db_path = v8_with_manager
    registry_db.init_registry_db()
    cols = {r[1]: r for r in _q(db_path, "PRAGMA table_info(users)")}
    for name in ("display_name", "role_preset"):
        assert name in cols
        assert cols[name][3] == 0, f"{name} must be nullable"
        assert cols[name][4] is None, f"{name} must have no default"
    assert _q(db_path, "SELECT COUNT(*) FROM users WHERE display_name IS NOT NULL OR role_preset IS NOT NULL")[0][0] == 0


def test_integrity_checked_and_a_backup_exists(v8_with_manager):
    db_dir, db_path = v8_with_manager
    assert _q(db_path, "PRAGMA integrity_check")[0][0] == "ok"
    registry_db.init_registry_db()
    assert _q(db_path, "PRAGMA integrity_check")[0][0] == "ok"
    backups = os.listdir(str(db_dir / "migration_backups"))
    assert any("v8-to-v10" in b for b in backups), backups
    # The backup is the PRE-migration file: it still has no books rows.
    bk = str(db_dir / "migration_backups" / [b for b in backups if "v8-to-v10" in b][0])
    assert _q(bk, "SELECT COUNT(*) FROM user_permissions WHERE subsystem LIKE 'retail.books.%'")[0][0] == 0


def test_rerun_is_a_no_op(v8_with_manager):
    db_dir, db_path = v8_with_manager
    registry_db.init_registry_db()
    snap = _q(db_path, "SELECT user_id, subsystem, access_level FROM user_permissions ORDER BY 1,2")
    registry_db.init_registry_db()
    registry_db._migrate_registry_schema(sqlite3.connect(str(db_path)))
    assert _q(db_path, "SELECT user_id, subsystem, access_level FROM user_permissions ORDER BY 1,2") == snap


def test_a_tuned_grant_survives_the_reseed(v8_with_manager):
    """The seed is INSERT OR IGNORE: an admin who already tuned a books row
    (impossible on v8, possible on a retry after a partial failure) keeps it."""
    db_dir, db_path = v8_with_manager
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "INSERT INTO user_permissions (id, user_id, subsystem, access_level) "
        "VALUES ('tuned', 'mgr-local-id', 'retail.books.view', 'full')")
    conn.commit()
    conn.close()
    registry_db.init_registry_db()
    assert _q(db_path, "SELECT access_level FROM user_permissions WHERE user_id='mgr-local-id' "
                       "AND subsystem='retail.books.view'") == [("full",)]


def test_legacy_subsystem_rows_are_never_touched(v8_with_manager):
    db_dir, db_path = v8_with_manager
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "INSERT INTO user_permissions (id, user_id, subsystem, access_level) "
        "VALUES ('legacy', 'mgr-local-id', 'retail', 'full')")
    conn.commit()
    conn.close()
    registry_db.init_registry_db()
    assert _q(db_path, "SELECT access_level FROM user_permissions WHERE id='legacy'") == [("full",)]


def test_a_clinic_shaped_registry_is_unchanged_by_the_accounts_step(tmp_path, monkeypatch):
    """registry.db is shared with Clinic. A Clinic install has `users` rows with
    role 'employee'/'admin' and clinic_role set, no retail capability rows the
    Retail way, and never a `retail.books.*` intent. The v9 accounts step must
    add the two columns and leave every existing value byte-identical."""
    db_dir = tmp_path / "database"
    db_dir.mkdir()
    db_path = db_dir / "registry.db"
    monkeypatch.setattr(registry_db, "REGISTRY_SCHEMA_VERSION", 8)
    monkeypatch.setattr(registry_db, "_db_dir", str(db_dir))
    monkeypatch.setattr(registry_db, "DB_PATH", str(db_path))
    registry_db.init_registry_db()
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "INSERT INTO users (id, company_id, employee_id, email, password_hash, role, clinic_role) "
        "VALUES ('c-u1', 'c-co', 'c-e1', 'dr@clinic.test', 'x', 'admin', 'dentist')")
    conn.execute(
        "INSERT INTO user_permissions (id, user_id, subsystem, access_level) "
        "VALUES ('c-p1', 'c-u1', 'clinic', 'full')")
    conn.commit()
    before_users = conn.execute(
        "SELECT id, company_id, employee_id, email, password_hash, role, clinic_role, status "
        "FROM users").fetchall()
    before_perm = conn.execute("SELECT id, user_id, subsystem, access_level FROM user_permissions "
                               "WHERE subsystem NOT LIKE 'retail.%'").fetchall()
    conn.close()

    monkeypatch.setattr(registry_db, "REGISTRY_SCHEMA_VERSION", 9)
    registry_db.init_registry_db()

    conn = sqlite3.connect(str(db_path))
    try:
        assert conn.execute("SELECT id, company_id, employee_id, email, password_hash, role, "
                            "clinic_role, status FROM users").fetchall() == before_users
        assert conn.execute("SELECT id, user_id, subsystem, access_level FROM user_permissions "
                            "WHERE subsystem NOT LIKE 'retail.%'").fetchall() == before_perm
        assert conn.execute("SELECT display_name, role_preset FROM users").fetchall() == [(None, None)]
        assert conn.execute("SELECT COUNT(*) FROM sync_outbox").fetchone()[0] == 0
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        conn.close()
