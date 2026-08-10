"""Per-device login / "one Admin Device" registry -- unit tests for
commercial_runtime/identity/device_registry.py.

Standalone: builds its own in-memory sqlite3 connection (row_factory set to
sqlite3.Row, matching registry_db.get_conn()'s real behaviour) and calls
apply_identity_device_schema() directly -- doesn't go through
registry_db.get_conn()/AURA_APP_DATA, same "no dependency on real product
boot state" spirit as commercial_runtime/tests/migration_safety_test.py.

Covers: schema creation + idempotency, the partial-unique-index "one Admin
Device" invariant (both via the DB directly and via set_admin_device's
atomic clear-then-set), the user<->device grant lifecycle, revoke_device's
audit-trail preservation, and the fingerprint unique index's NULL handling.

Run:
    pytest commercial_runtime/identity/tests/test_device_registry.py -v
"""
import sqlite3

import pytest

from commercial_runtime.identity.device_registry import (
    allowed_on_device,
    apply_identity_device_schema,
    get_device,
    grant_user_device,
    list_devices,
    revoke_device,
    revoke_user_device,
    set_admin_device,
    upsert_local_device,
)


@pytest.fixture
def conn():
    c = sqlite3.connect(':memory:')
    c.row_factory = sqlite3.Row
    apply_identity_device_schema(c)
    return c


def _insert_device(conn, device_id, company_id, is_admin=0, fingerprint=None):
    """Raw INSERT, bypassing upsert_local_device -- used where the test
    needs to prove the database itself enforces an invariant, not just the
    Python helper."""
    now = '2026-08-10T00:00:00+00:00'
    conn.execute('''
        INSERT INTO devices
          (id, company_id, device_fingerprint, is_admin_device, status, first_seen_at, last_seen_at)
        VALUES (?, ?, ?, ?, 'active', ?, ?)
    ''', (device_id, company_id, fingerprint, is_admin, now, now))
    conn.commit()


def test_migration_creates_both_tables(conn):
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name IN ('devices','user_devices')"
    ).fetchall()}
    assert tables == {'devices', 'user_devices'}


def test_migration_called_a_second_time_is_a_clean_noop(conn):
    _insert_device(conn, 'dev-a', 'company-1', is_admin=1)

    apply_identity_device_schema(conn)  # second call -- must not raise or alter anything

    row = get_device(conn, 'dev-a')
    assert row['is_admin_device'] == 1, "pre-existing row must survive a second schema application untouched"
    tables = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name IN ('devices','user_devices')"
    ).fetchall()
    assert len(tables) == 2


def test_partial_unique_index_rejects_direct_two_admin_insert(conn):
    """Proves the invariant is enforced by the database itself, not just by
    going through set_admin_device -- a raw second INSERT with
    is_admin_device=1 for the same company must fail."""
    _insert_device(conn, 'dev-a', 'company-1', is_admin=1)
    with pytest.raises(sqlite3.IntegrityError):
        _insert_device(conn, 'dev-b', 'company-1', is_admin=1)


def test_set_admin_device_moves_flag_atomically(conn):
    _insert_device(conn, 'dev-a', 'company-1', is_admin=1)
    _insert_device(conn, 'dev-b', 'company-1', is_admin=0)

    set_admin_device(conn, 'company-1', 'dev-b')

    assert get_device(conn, 'dev-a')['is_admin_device'] == 0
    assert get_device(conn, 'dev-b')['is_admin_device'] == 1


def test_set_admin_device_leaves_exactly_one_admin_row(conn):
    _insert_device(conn, 'dev-a', 'company-1', is_admin=1)
    _insert_device(conn, 'dev-b', 'company-1', is_admin=0)
    _insert_device(conn, 'dev-c', 'company-1', is_admin=0)

    set_admin_device(conn, 'company-1', 'dev-b')

    admins = conn.execute(
        "SELECT id FROM devices WHERE company_id=? AND is_admin_device=1", ('company-1',)
    ).fetchall()
    assert [r['id'] for r in admins] == ['dev-b']


def test_set_admin_device_unknown_device_raises_and_does_not_clear_current_admin(conn):
    _insert_device(conn, 'dev-a', 'company-1', is_admin=1)

    with pytest.raises(ValueError):
        set_admin_device(conn, 'company-1', 'does-not-exist')

    assert get_device(conn, 'dev-a')['is_admin_device'] == 1, "failed set_admin_device must not clear the existing admin"


def test_allowed_on_device_lifecycle(conn):
    _insert_device(conn, 'dev-a', 'company-1')
    assert allowed_on_device(conn, 'user-1', 'dev-a') is False

    grant_user_device(conn, 'user-1', 'dev-a', granted_by='admin-1')
    assert allowed_on_device(conn, 'user-1', 'dev-a') is True

    revoke_user_device(conn, 'user-1', 'dev-a')
    assert allowed_on_device(conn, 'user-1', 'dev-a') is False


def test_revoke_device_preserves_user_devices_audit_trail(conn):
    _insert_device(conn, 'dev-a', 'company-1', is_admin=1)
    grant_user_device(conn, 'user-1', 'dev-a', granted_by='admin-1')

    revoke_device(conn, 'dev-a')

    device = get_device(conn, 'dev-a')
    assert device['status'] == 'revoked'
    assert device['is_admin_device'] == 0, "revoking the admin device must also clear the flag"

    grant = conn.execute(
        "SELECT * FROM user_devices WHERE user_id=? AND device_id=?", ('user-1', 'dev-a')
    ).fetchone()
    assert grant is not None, "revoking a device must NOT delete its user_devices grant rows"


def test_fingerprint_unique_index_allows_multiple_nulls_rejects_duplicate_value(conn):
    _insert_device(conn, 'dev-a', 'company-1', fingerprint=None)
    _insert_device(conn, 'dev-b', 'company-1', fingerprint=None)  # pre-activation devices -- must not raise

    _insert_device(conn, 'dev-c', 'company-1', fingerprint='fp-123')
    with pytest.raises(sqlite3.IntegrityError):
        _insert_device(conn, 'dev-d', 'company-1', fingerprint='fp-123')


def test_upsert_local_device_insert_then_update_preserves_first_seen_and_unspecified_fields(conn):
    first = upsert_local_device(conn, 'dev-a', 'company-1', device_label='Till 1', platform='WINDOWS')
    assert first['company_id'] == 'company-1'
    assert first['device_label'] == 'Till 1'
    first_seen = first['first_seen_at']

    second = upsert_local_device(conn, 'dev-a', 'company-1', platform='WINDOWS')
    assert second['first_seen_at'] == first_seen, "first_seen_at must be stamped once and never overwritten"
    assert second['device_label'] == 'Till 1', "fields left unspecified on update must be preserved (COALESCE)"


def test_list_devices_is_scoped_to_company_id(conn):
    _insert_device(conn, 'dev-a', 'company-1')
    _insert_device(conn, 'dev-b', 'company-2')

    devices = list_devices(conn, 'company-1')

    assert [d['id'] for d in devices] == ['dev-a']
