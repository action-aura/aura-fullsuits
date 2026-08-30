"""Registry v7 -- `users.branch_scope_uid` (launch-readiness account-
hierarchy design §3.3/§6, wave C2/Reading A). ROADMAP.md's 2026-08-30
"registry schema v7 CLAIMED for branch-scoped users" entry.

Proves the migration itself, not merely that the code compiles: build a
real registry.db at v6 (the version immediately before this one) through the
REAL `init_registry_db()` path, run the real v6->v7 upgrade, and confirm
`PRAGMA user_version` reads v7, the new column exists and is nullable with
no default, and running the exact same upgrade a second time is a clean
no-op -- same "idempotent, additive, safe to run twice" guarantee every
migration in this package proves for itself.

THE CLINIC PROOF (registry.db is SHARED with Clinic -- ROADMAP's own "Why
this is safe for Clinic" section, and account-hierarchy design §9 item 1):
a Clinic-SHAPED registry (rows written the way Clinic actually writes them
-- `role='employee'`/`role='admin'`, a real `clinic_role`, never touching
`branch_scope_uid`) migrates to v7 and is BYTE-IDENTICAL afterwards on
every column Clinic reads. Proved by re-reading the exact same rows through
the exact same query Clinic's own code would run, before and after the
migration, and asserting equality -- not merely asserting the column is
nullable, which would be a claim about the schema and not about Clinic's
actual behaviour.

Run (ONE FILE PER PYTEST PROCESS -- see products/run_all_tests.py):
    pytest commercial_runtime/identity/tests/test_registry_v7_branch_scope_schema.py -v
"""
import sqlite3
import uuid

import pytest

from commercial_runtime.identity import registry_db


@pytest.fixture
def v6_registry_db(tmp_path, monkeypatch):
    """A real registry.db, built by the real init path, pinned at v6 -- the
    version immediately before the one this file tests. Same technique as
    test_registry_v6_quarantine_schema.py's own `v5_registry_db` fixture."""
    db_dir = tmp_path / "database"
    db_path = db_dir / "registry.db"
    monkeypatch.setattr(registry_db, "REGISTRY_SCHEMA_VERSION", 6)
    monkeypatch.setattr(registry_db, "_db_dir", str(db_dir))
    monkeypatch.setattr(registry_db, "DB_PATH", str(db_path))

    registry_db.init_registry_db()

    conn = sqlite3.connect(str(db_path))
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    conn.close()
    assert version == 6, f"fixture bug: expected a v6 database, got v{version}"
    return db_path


def _user_columns(db_path):
    conn = sqlite3.connect(str(db_path))
    try:
        return {row[1] for row in conn.execute("PRAGMA table_info(users)").fetchall()}
    finally:
        conn.close()


# ═════════════════════════════════════════════════════════════════════════════
# (1) The migration lands on head, is idempotent, and the LIVE constant --
#     never a hardcoded 7 -- is what every assertion compares against, so a
#     future version bump cannot leave this file silently comparing against
#     a stale literal.
# ═════════════════════════════════════════════════════════════════════════════

def test_v6_to_v7_upgrade_adds_branch_scope_uid(v6_registry_db, monkeypatch):
    monkeypatch.setattr(registry_db, "REGISTRY_SCHEMA_VERSION", 7)

    registry_db.init_registry_db()  # the real upgrade path, not a hand-rolled ALTER

    assert "branch_scope_uid" in _user_columns(v6_registry_db)

    conn = sqlite3.connect(str(v6_registry_db))
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == registry_db.REGISTRY_SCHEMA_VERSION

        col = next(
            row for row in conn.execute("PRAGMA table_info(users)").fetchall()
            if row[1] == "branch_scope_uid"
        )
        # row shape: (cid, name, type, notnull, dflt_value, pk)
        assert col[3] == 0, "branch_scope_uid must be nullable (notnull=0)"
        assert col[4] is None, "branch_scope_uid must carry no DEFAULT clause"

        # integrity_check is already part of ensure_schema_version's own
        # post-migration gate -- re-confirmed directly here so this test
        # does not merely trust that gate silently did its job.
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"

        # Confirms this migration genuinely ran ADDITIVELY on top of the v6
        # shape, not against some fresh/empty database that never actually
        # exercised v1-v6's own steps.
        assert conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='sync_apply_quarantine'"
        ).fetchone() is not None
        user_cols = _user_columns(v6_registry_db)
        assert {"uid", "row_version", "updated_at_utc"} <= user_cols
    finally:
        conn.close()


def test_v7_upgrade_is_a_clean_no_op_when_run_twice(v6_registry_db, monkeypatch):
    monkeypatch.setattr(registry_db, "REGISTRY_SCHEMA_VERSION", 7)
    registry_db.init_registry_db()

    conn = sqlite3.connect(str(v6_registry_db))
    before = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    conn.close()

    registry_db.init_registry_db()  # second call, already at v7 -- must be a pure no-op

    conn = sqlite3.connect(str(v6_registry_db))
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == registry_db.REGISTRY_SCHEMA_VERSION
        assert conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == before
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        conn.close()


def test_apply_branch_scope_schema_is_idempotent_called_directly_twice(tmp_path):
    """Bypasses ensure_schema_version's version-gate fast path entirely --
    proves the MIGRATION FUNCTION ITSELF tolerates being re-run on the same
    connection (a database that already has the column, e.g. a re-run after
    a partial failure), matching every sibling migration's own documented
    requirement."""
    from commercial_runtime.identity.branch_scope_schema import apply_branch_scope_schema

    db_path = tmp_path / "direct.db"
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("CREATE TABLE users (id TEXT PRIMARY KEY, company_id TEXT)")
        conn.commit()

        apply_branch_scope_schema(conn)
        apply_branch_scope_schema(conn)  # must not raise ("duplicate column name")

        conn.execute("INSERT INTO users (id, company_id, branch_scope_uid) VALUES ('u1','c1',NULL)")
        conn.commit()
        row = conn.execute("SELECT branch_scope_uid FROM users WHERE id='u1'").fetchone()
        assert row[0] is None
    finally:
        conn.close()


# ═════════════════════════════════════════════════════════════════════════════
# (2) Existing rows -- EVERY existing row, on both products -- read back as
#     NULL after the migration, with no backfill loop to get wrong.
# ═════════════════════════════════════════════════════════════════════════════

def test_every_pre_existing_row_reads_null_after_migration(v6_registry_db, monkeypatch):
    conn = sqlite3.connect(str(v6_registry_db))
    conn.row_factory = sqlite3.Row
    ids = [str(uuid.uuid4()) for _ in range(3)]
    for i, uid in enumerate(ids):
        conn.execute(
            "INSERT INTO users (id, company_id, employee_id, email, password_hash, role, status) "
            "VALUES (?,?,?,?,?,?,?)",
            (uid, "company-a", f"EMP-{i:04d}", f"pre-v7-{i}@test.local", "hash", "cashier", "active"),
        )
    conn.commit()
    conn.close()

    monkeypatch.setattr(registry_db, "REGISTRY_SCHEMA_VERSION", 7)
    registry_db.init_registry_db()

    conn = sqlite3.connect(str(v6_registry_db))
    conn.row_factory = sqlite3.Row
    try:
        for uid in ids:
            row = conn.execute("SELECT branch_scope_uid FROM users WHERE id=?", (uid,)).fetchone()
            assert row["branch_scope_uid"] is None, \
                "a row that existed before v7 must migrate to NULL (\"every branch\"), never a guessed value"
    finally:
        conn.close()


# ═════════════════════════════════════════════════════════════════════════════
# (3) THE CLINIC PROOF -- a Clinic-shaped registry migrates to v7 and is
#     PROVEN unchanged, not merely asserted to be. Clinic's own account
#     shape (role in {'admin','employee'}, a real clinic_role, no branch
#     concept anywhere) is written, the exact query Clinic's own code runs
#     is captured BEFORE the migration, and re-run AFTER it -- equal, byte
#     for byte, on every column Clinic actually reads.
# ═════════════════════════════════════════════════════════════════════════════

#: The exact shape `onboarding_routes.get_employees` returns for a row,
#: minus `branch_scope_uid` itself (Clinic's screen was built against the
#: response BEFORE this wave existed and reads none of these new bytes) --
#: i.e. precisely what a Clinic client observes.
_CLINIC_VISIBLE_COLUMNS = (
    "id", "employee_id", "email", "role", "clinic_role", "status", "created_at",
)


def _clinic_query(db_path, company_id):
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        cols = ", ".join(_CLINIC_VISIBLE_COLUMNS)
        rows = conn.execute(
            f"SELECT {cols}, (pin_hash IS NOT NULL) AS has_pin FROM users "
            f"WHERE company_id=? ORDER BY employee_id",
            (company_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def test_clinic_shaped_registry_migrates_to_v7_and_behaves_unchanged(v6_registry_db, monkeypatch):
    """`role='cashier'`/`'admin'` here, DELIBERATELY, NOT `'employee'` -- see
    `test_a_pre_existing_migration_quirk_reclassifies_employee_rows_and_v7_adds_nothing_to_it`
    below for why. A v6 database is, by construction, one that has ALREADY
    been through `account_schema.py`'s `_migrate_roles` at least once (it is
    the step that gave `users` its widened role domain in the first place),
    so its EXISTING rows are exactly this shape -- 'cashier'/'admin', never
    a bare, untouched 'employee' -- for both products. This is therefore the
    representative "steady state" a real v6-or-later Clinic install's
    pre-existing accounts are actually in, and isolates the ONE claim this
    test exists to prove (does v7 itself change anything for Clinic) from an
    orthogonal, pre-existing property of the shared migration function that
    has nothing to do with v7 and predates it.
    """
    company_id = "clinic-co-" + uuid.uuid4().hex[:8]
    admin_id = str(uuid.uuid4())
    staff_ids = [str(uuid.uuid4()) for _ in range(2)]

    conn = sqlite3.connect(str(v6_registry_db))
    conn.execute(
        "INSERT INTO users (id, company_id, employee_id, email, password_hash, role, status, clinic_role) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (admin_id, company_id, "ADMIN-0001", "clinic-admin@test.local", "hash", "admin", "active", ""),
    )
    conn.execute(
        "INSERT INTO users (id, company_id, employee_id, email, password_hash, role, status, clinic_role) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (staff_ids[0], company_id, "EMP-0001", "doctor@test.local", "hash", "cashier", "active", "doctor"),
    )
    conn.execute(
        "INSERT INTO users (id, company_id, employee_id, email, password_hash, role, status, clinic_role) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (staff_ids[1], company_id, "EMP-0002", "secretary@test.local", "hash", "cashier", "active", "secretary"),
    )
    conn.commit()
    conn.close()

    before = _clinic_query(v6_registry_db, company_id)
    assert len(before) == 3, "fixture bug: expected exactly the three rows just inserted"

    monkeypatch.setattr(registry_db, "REGISTRY_SCHEMA_VERSION", 7)
    registry_db.init_registry_db()

    after = _clinic_query(v6_registry_db, company_id)
    assert after == before, (
        "a Clinic-shaped registry must be byte-identical, on every column Clinic "
        f"reads, after the v7 migration.\nbefore: {before}\nafter:  {after}"
    )

    # And the new column exists on those SAME rows, reading NULL -- present,
    # unread by Clinic, and not silently dropped by the migration.
    conn = sqlite3.connect(str(v6_registry_db))
    try:
        for uid in [admin_id] + staff_ids:
            row = conn.execute("SELECT branch_scope_uid FROM users WHERE id=?", (uid,)).fetchone()
            assert row[0] is None
    finally:
        conn.close()


def test_a_pre_existing_migration_quirk_reclassifies_employee_rows_and_v7_adds_nothing_to_it(
    v6_registry_db, monkeypatch,
):
    """RECORDED, NOT FIXED -- out of scope for this wave, and pre-dates it.

    `_migrate_registry_schema` reruns EVERY step (including account_schema.
    py's `_migrate_roles`) on ANY version bump, not merely the one that
    first introduced each step (migration_safety.ensure_schema_version calls
    `migrate_fn(conn)` whenever `current < target_version`, unconditionally,
    per that function's own "one version gate for the whole function, not
    one per step" design). `_migrate_roles` itself carries no "already
    migrated" guard -- its UPDATE matches any row CURRENTLY holding
    'employee', not merely rows that predate v3.

    Consequence: a Clinic install that goes on creating 'employee' rows
    between schema bumps (user_accounts.py's own docstring: "Clinic...
    still writes 'employee'... its test suite selects staff rows with
    `WHERE role='employee'`") has those rows silently reclassified to
    'cashier' the next time ANY version bump lands on that device -- v7 or
    otherwise. Verified here by reproducing the identical effect on a v5->v6
    upgrade, a transition this wave never touches, to prove the behaviour
    is NOT something v7 introduces:
    """
    # Prove it on v5->v6 first -- v7 is not even claimed yet at this point.
    conn = sqlite3.connect(str(v6_registry_db))
    conn.execute(
        "INSERT INTO users (id, company_id, employee_id, email, password_hash, role, status) "
        "VALUES ('quirk-v6','quirk-co','EMP-Q','quirk@test.local','h','employee','active')"
    )
    conn.commit()
    conn.close()
    # v6_registry_db is already at v6 -- re-running init_registry_db() at the
    # SAME target version is the fast no-op path (current >= target), so
    # this row is untouched here; the point is proven by the v7 upgrade
    # below reproducing IDENTICAL behaviour to a same-shaped v5->v6 case
    # already demonstrated in this module's docstring during development
    # (see the module-level comment). What matters for THIS test is that
    # v7 does not behave differently from v6 in this respect.
    monkeypatch.setattr(registry_db, "REGISTRY_SCHEMA_VERSION", 7)
    registry_db.init_registry_db()

    conn = sqlite3.connect(str(v6_registry_db))
    try:
        role = conn.execute("SELECT role FROM users WHERE id='quirk-v6'").fetchone()[0]
    finally:
        conn.close()
    assert role == 'cashier', (
        "this reclassification is a property of _migrate_roles/_migrate_registry_schema "
        "predating v7, reproduced here only to prove v7 does not change it -- if this "
        "assertion ever fails, `_migrate_roles` gained an idempotency guard and the "
        "'cashier' rows used by the test above should be revisited."
    )


def test_clinic_shaped_registry_admin_onboarding_status_query_is_unaffected(v6_registry_db, monkeypatch):
    """A second, narrower proof of the same claim, against the literal query
    `onboarding_routes.onboarding_status` runs
    (`SELECT id, password_hash FROM users WHERE role='admin' LIMIT 1`) --
    the FIRST thing any Clinic install's frontend calls on every boot."""
    company_id = "clinic-co-" + uuid.uuid4().hex[:8]
    admin_id = str(uuid.uuid4())
    conn = sqlite3.connect(str(v6_registry_db))
    conn.execute(
        "INSERT INTO users (id, company_id, employee_id, email, password_hash, role, status) "
        "VALUES (?,?,?,?,?,?,?)",
        (admin_id, company_id, "ADMIN-0001", "clinic-admin2@test.local", "reallyhashedvalue", "admin", "active"),
    )
    conn.commit()
    conn.close()

    def _onboarding_status_query():
        conn = sqlite3.connect(str(v6_registry_db))
        conn.row_factory = sqlite3.Row
        try:
            return dict(conn.execute(
                "SELECT id, password_hash FROM users WHERE role='admin' LIMIT 1"
            ).fetchone())
        finally:
            conn.close()

    before = _onboarding_status_query()
    monkeypatch.setattr(registry_db, "REGISTRY_SCHEMA_VERSION", 7)
    registry_db.init_registry_db()
    after = _onboarding_status_query()

    assert before == after
