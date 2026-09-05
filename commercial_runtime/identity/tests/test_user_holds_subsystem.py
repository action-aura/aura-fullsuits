"""Regression tests for `user_accounts.user_holds_subsystem` -- the derived
reader `mt_auth.mt_require_subsystem` now uses in place of a bare
`SELECT access_level FROM user_permissions WHERE subsystem='retail'`.

Written because of a real, shipped lockout: nothing the product creates
(`create_employee`, `seed_capabilities_for_user`) ever wrote that legacy row,
so every screen-created cashier and manager held `retail.sell=full` and was
refused every retail route anyway -- observed 2026-09-05 on a real phone and
desktop till. These tests pin the derived rule: an explicit legacy row wins
outright (so a deliberate revocation via `update_perms` still works); absent
that row, one granted `<subsystem>.<code>` row is enough; absent everything,
denied.

Run:
    pytest commercial_runtime/identity/tests/test_user_holds_subsystem.py -v
"""
import sqlite3
import uuid

import pytest

from commercial_runtime.identity import registry_db, user_accounts
from commercial_runtime.security.passwords import hash_password


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    path = tmp_path / "registry.db"
    monkeypatch.setattr(registry_db, "DB_PATH", str(path))
    monkeypatch.setattr(registry_db, "_db_dir", str(tmp_path))
    registry_db.init_registry_db()
    return path


def _make_user(conn, *, role="cashier"):
    user_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO users (id, company_id, employee_id, email, password_hash, role, status, require_password_change) "
        "VALUES (?,?,?,?,?,?,?,0)",
        (
            user_id, str(uuid.uuid4()), f"EMP-{uuid.uuid4().hex[:6]}",
            f"user-{uuid.uuid4().hex[:8]}@test.local", hash_password("Whatever11"),
            role, "active",
        ),
    )
    return user_id


def _grant(conn, user_id, subsystem, access_level):
    conn.execute(
        "INSERT INTO user_permissions (id, user_id, subsystem, access_level) VALUES (?,?,?,?)",
        (str(uuid.uuid4()), user_id, subsystem, access_level),
    )


def test_no_rows_at_all_denies(db_path):
    # Arrange
    conn = sqlite3.connect(str(db_path))
    user_id = _make_user(conn)
    conn.commit()

    # Act
    held = user_accounts.user_holds_subsystem(conn, user_id, "retail")

    # Assert -- silence is not consent, same as user_has_capability.
    assert held is False
    conn.close()


def test_legacy_grant_alone_holds_the_subsystem(db_path):
    # Arrange
    conn = sqlite3.connect(str(db_path))
    user_id = _make_user(conn)
    _grant(conn, user_id, "retail", user_accounts.ACCESS_FULL)
    conn.commit()

    # Act
    held = user_accounts.user_holds_subsystem(conn, user_id, "retail")

    # Assert
    assert held is True
    conn.close()


def test_explicit_revocation_wins_over_every_granted_code(db_path):
    """An admin can flip the legacy row to 'none' through update_perms as a
    deliberate revocation -- that must outrank every namespaced code, or
    revoking an account becomes impossible the moment it holds any
    capability at all."""
    # Arrange
    conn = sqlite3.connect(str(db_path))
    user_id = _make_user(conn)
    _grant(conn, user_id, "retail", user_accounts.ACCESS_NONE)
    for code in user_accounts.CAPABILITY_CODES:
        _grant(conn, user_id, code, user_accounts.ACCESS_FULL)
    conn.commit()

    # Act
    held = user_accounts.user_holds_subsystem(conn, user_id, "retail")

    # Assert -- an explicit revocation wins over every granted code.
    assert held is False
    conn.close()


def test_every_namespaced_code_denied_with_no_legacy_row_denies(db_path):
    # Arrange
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    user_id = _make_user(conn, role="cashier")
    user_accounts.seed_capabilities_for_user(conn, user_id, "cashier")
    conn.execute(
        "UPDATE user_permissions SET access_level=? WHERE user_id=?",
        (user_accounts.ACCESS_NONE, user_id),
    )
    conn.commit()

    # Act
    held = user_accounts.user_holds_subsystem(conn, user_id, "retail")

    # Assert
    assert held is False
    conn.close()


def test_a_screen_created_cashier_holds_retail_through_capability_codes_alone(db_path):
    """THE regression test. `seed_capabilities_for_user` is the real shape
    every cashier and manager is created with -- no legacy row, ever -- and
    this must resolve to True or every screen-created employee is locked out
    of the app that sells, exactly as shipped on 2026-09-05."""
    # Arrange
    conn = sqlite3.connect(str(db_path))
    user_id = _make_user(conn, role="cashier")
    user_accounts.seed_capabilities_for_user(conn, user_id, "cashier")
    conn.commit()

    # Assert the fixture stayed honest about production's shape before
    # trusting the rest of this test -- if seed_capabilities_for_user ever
    # starts writing the legacy row, this assertion (not the one below) is
    # what should fail, so nobody mistakes a fixture drift for the fix
    # working.
    legacy_row = conn.execute(
        "SELECT 1 FROM user_permissions WHERE user_id=? AND subsystem=?",
        (user_id, "retail"),
    ).fetchone()
    assert legacy_row is None, (
        "seed_capabilities_for_user must never write the legacy 'retail' row "
        "-- if it now does, this test has quietly started relying on the "
        "exact row production never writes, and would stop catching the "
        "lockout it exists to catch."
    )

    # Act
    held = user_accounts.user_holds_subsystem(conn, user_id, "retail")

    # Assert
    assert held is True
    conn.close()


def test_a_capability_for_a_different_subsystem_does_not_grant_this_one(db_path):
    # Arrange
    conn = sqlite3.connect(str(db_path))
    user_id = _make_user(conn)
    _grant(conn, user_id, "retail.sell", user_accounts.ACCESS_FULL)
    conn.commit()

    # Act
    held = user_accounts.user_holds_subsystem(conn, user_id, "clinic")

    # Assert
    assert held is False
    conn.close()


def test_a_similarly_prefixed_subsystem_does_not_count_towards_this_one(db_path):
    """Dot-boundary guard: 'retailx.sell' must not count towards 'retail'
    just because it starts with the same characters -- the LIKE prefilter
    alone would let it through, which is why the module re-checks with a
    Python startswith(subsystem + '.') afterwards."""
    # Arrange
    conn = sqlite3.connect(str(db_path))
    user_id = _make_user(conn)
    _grant(conn, user_id, "retailx.sell", user_accounts.ACCESS_FULL)
    conn.commit()

    # Act
    held = user_accounts.user_holds_subsystem(conn, user_id, "retail")

    # Assert
    assert held is False
    conn.close()


def test_positional_indexing_answers_the_same_with_and_without_row_factory(db_path):
    """The module is handed connections both with and without
    `row_factory=sqlite3.Row` set -- registry_db.get_conn sets it, a bare
    sqlite3.connect in a migration fixture does not -- and reads
    positionally so both shapes answer identically."""
    # Arrange
    plain_conn = sqlite3.connect(str(db_path))
    user_id = _make_user(plain_conn, role="cashier")
    user_accounts.seed_capabilities_for_user(plain_conn, user_id, "cashier")
    plain_conn.commit()

    # Act
    plain_result = user_accounts.user_holds_subsystem(plain_conn, user_id, "retail")
    plain_conn.close()

    row_conn = sqlite3.connect(str(db_path))
    row_conn.row_factory = sqlite3.Row
    row_result = user_accounts.user_holds_subsystem(row_conn, user_id, "retail")
    row_conn.close()

    # Assert
    assert plain_result is True
    assert row_result is True
    assert plain_result == row_result
