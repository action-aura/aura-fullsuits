"""Phase 5 wave B2 stage 3 -- end-to-end proof that an emitted
`user_permission` event actually crosses devices through the REAL push/pull
path (`SyncService.push_once`/`pull_once`, never `_apply_event` called by
hand).

See docs/launch-readiness/phase5-waveb2-user-sync.md ("Decision 5 -- user_
permissions is in scope") and commercial_runtime/sync/tests/
test_user_sync_emit_two_device_roundtrip.py (stage 2b, the `user` entity's
own two-device suite) for the sibling this one is the permission-side
counterpart to -- same harness, duplicated here rather than imported,
matching that file's own stated convention.

Two independent registry.db files stand in for two tills of the SAME shop.
A single in-memory `_FakeRelay` plays Owner's relay: device A's
`push_once()` drains its own outbox into it, device B's `pull_once()` reads
from it and applies through the real `apply_pull_result`/`_apply_event`
path.

Run (ONE FILE PER PYTEST PROCESS -- see products/run_all_tests.py):
    pytest commercial_runtime/sync/tests/test_user_permission_sync_two_device_roundtrip.py -v
"""
from __future__ import annotations

import sqlite3
import uuid

import pytest
from flask import Flask

from commercial_runtime.identity import mt_auth, onboarding_routes, registry_db, user_accounts
from commercial_runtime.identity.onboarding_routes import onboarding_bp
from commercial_runtime.licensing_contracts import flask_guard
from commercial_runtime.licensing_contracts.state_repository import LicenseStateRecord
from commercial_runtime.sync.sync_service import REGISTRY_SYNC_ENTITY_TYPES, SyncService


def _conn_factory(db_path):
    def _get_conn():
        c = sqlite3.connect(str(db_path), timeout=30)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA foreign_keys=ON")
        return c
    return _get_conn


def _build_device(tmp_path, name):
    db_dir = tmp_path / name
    db_path = db_dir / "registry.db"
    import pytest as _pytest
    mp = _pytest.MonkeyPatch()
    mp.setattr(registry_db, "_db_dir", str(db_dir))
    mp.setattr(registry_db, "DB_PATH", str(db_path))
    registry_db.init_registry_db()
    mp.undo()
    return str(db_path), _conn_factory(db_path)


class _FakeRelay:
    def __init__(self):
        self.events = []

    def push(self, chunk):
        self.events.extend(chunk)

    def pull(self, since):
        return {"events": self.events[since:], "cursor": len(self.events)}


COMPANY_ID = "shop-co-1"


@pytest.fixture
def two_devices(tmp_path):
    db_path_a, get_conn_a = _build_device(tmp_path, "device_a")
    db_path_b, get_conn_b = _build_device(tmp_path, "device_b")
    relay = _FakeRelay()
    service_a = SyncService(
        client_factory=lambda: relay, get_conn=get_conn_a,
        local_company_id_provider=lambda: COMPANY_ID,
        handled_entity_types=REGISTRY_SYNC_ENTITY_TYPES,
    )
    service_b = SyncService(
        client_factory=lambda: relay, get_conn=get_conn_b,
        local_company_id_provider=lambda: COMPANY_ID,
        handled_entity_types=REGISTRY_SYNC_ENTITY_TYPES,
    )
    return {
        "db_path_a": db_path_a, "get_conn_a": get_conn_a, "service_a": service_a,
        "db_path_b": db_path_b, "get_conn_b": get_conn_b, "service_b": service_b,
        "relay": relay,
    }


@pytest.fixture
def app_a(two_devices, tmp_path, monkeypatch):
    monkeypatch.setattr(onboarding_routes, "get_conn", two_devices["get_conn_a"])
    monkeypatch.setattr(mt_auth, "REGISTRY_DB", two_devices["db_path_a"])
    monkeypatch.setenv("AURA_APP_DATA", str(tmp_path / "device_a"))
    # create-employee/permissions on device A now carry a licence gate too
    # (AUDIT: account administration had none). Same monkeypatch, same
    # reasoning, as test_employee_admin_routes.py's own `app` fixture.
    monkeypatch.setattr(flask_guard.LicenseStateRepository, "__init__", lambda self, db_path: None)
    monkeypatch.setattr(
        flask_guard.LicenseStateRepository, "load",
        lambda self: LicenseStateRecord(
            licensing_schema_version=1, product_code="AURA_TEST", platform="WINDOWS",
            current_state="ACTIVE_ONLINE",
        ),
    )
    flask_app = Flask(__name__)
    flask_app.secret_key = "test-secret"
    flask_app.testing = True
    flask_app.register_blueprint(onboarding_bp)
    return flask_app


def _sync_a_to_b(two_devices):
    two_devices["service_a"].push_once()
    two_devices["service_b"].pull_once()


def _user_on(get_conn, **where):
    conn = get_conn()
    try:
        clause = " AND ".join(f"{k}=?" for k in where)
        row = conn.execute(f"SELECT * FROM users WHERE {clause}", tuple(where.values())).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def _capabilities_on(get_conn, local_user_id):
    conn = get_conn()
    try:
        return {
            code: user_accounts.user_has_capability(conn, local_user_id, code)
            for code in user_accounts.CAPABILITY_CODES
        }
    finally:
        conn.close()


def _make_employee(client, role=None):
    email = f'emp-{uuid.uuid4().hex[:8]}@x.com'
    body = {'email': email}
    if role:
        body['role'] = role
    r = client.post('/api/admin/employees', json=body)
    assert r.status_code == 200, r.get_json()
    employees = client.get('/api/admin/employees').get_json()['employees']
    row = next(e for e in employees if e['email'] == email)
    return row['id'], email


# ── C.4 -- the headline end-to-end point of this whole stage: a new
# employee created on A arrives on B with a WORKING permission set ─────────

def test_a_new_cashier_created_on_device_a_arrives_on_device_b_with_a_working_permission_set(
    two_devices, app_a,
):
    """Asserts the ACTUAL capability rows, not just row counts -- a receiver
    that seeded eight rows with the WRONG access_level would pass a bare
    count check and still leave the cashier unable to work the till."""
    client_a = app_a.test_client()
    client_a.post('/api/onboarding/create-admin', json={
        'name': 'Owner', 'email': 'owner@shop.co', 'password': 'OwnerPW11',
    })
    user_id_a, email = _make_employee(client_a, role='cashier')

    _sync_a_to_b(two_devices)

    b_row = _user_on(two_devices["get_conn_b"], email=email)
    assert b_row is not None, "the new cashier never reached device B at all"
    b_caps = _capabilities_on(two_devices["get_conn_b"], b_row["id"])
    expected = user_accounts.capabilities_for_role('cashier')
    for code in user_accounts.CAPABILITY_CODES:
        assert b_caps[code] == (code in expected), (
            f"device B's cashier has the WRONG grant for {code}: got {b_caps[code]}, "
            f"expected {code in expected}"
        )
    # The headline symptom this stage exists to prevent: a cashier who logs
    # in on device B and can do nothing there.
    assert b_caps[user_accounts.CAP_SELL] is True, "a cashier synced to device B must be able to sell"


def test_a_managers_grants_also_differ_correctly_from_a_cashiers_on_device_b(two_devices, app_a):
    """The allow half, proven for a SECOND role too -- a receiver that
    happened to hardcode the cashier defaults (or copy user A's grants onto
    every synced user) would pass the cashier-only test above by accident."""
    client_a = app_a.test_client()
    client_a.post('/api/onboarding/create-admin', json={
        'name': 'Owner', 'email': 'owner2@shop.co', 'password': 'OwnerPW11',
    })
    user_id_a, email = _make_employee(client_a, role='manager')

    _sync_a_to_b(two_devices)

    b_row = _user_on(two_devices["get_conn_b"], email=email)
    b_caps = _capabilities_on(two_devices["get_conn_b"], b_row["id"])
    expected = user_accounts.capabilities_for_role('manager')
    for code in user_accounts.CAPABILITY_CODES:
        assert b_caps[code] == (code in expected), f"device B's manager has the WRONG grant for {code}"
    assert b_caps[user_accounts.CAP_EMPLOYEES] is False, "a manager must never inherit the owner-only codes"
    assert b_caps[user_accounts.CAP_CASH_APPROVE] is False


# ── C.3 / D4 -- a revoke on device A removes the permission on device B.
# Mutation-proven: see this test's own docstring and the task's final
# report for the verbatim RED/GREEN pytest output ──────────────────────────

def test_a_revoke_on_device_a_removes_the_permission_on_device_b(two_devices, app_a):
    """Uses create_admin's re-onboarding wipe (onboarding_routes.py:162) --
    the ONE write site where a deleted grant has NO same-transaction reseed
    for the identical (user, subsystem) pair, which is what makes this
    scenario able to observe the delete-emission's effect in isolation
    (update_role/update_perms's own deletes are immediately followed by a
    reseed of the SAME keys, so their end state on B is correct even
    without an explicit delete event -- proven separately in
    commercial_runtime/identity/tests/
    test_user_permission_sync_emission_write_sites.py).

    MUTATION-PROVEN: temporarily removing the
    `_queue_user_permission_sync_event(..., 'delete')` loop this test
    exercises (onboarding_routes.py's create_admin, right after the
    `DELETE FROM user_permissions WHERE user_id=?` for the re-onboarded
    admin) turns this test RED -- the old admin's grant stays GRANTED on
    device B forever, because nothing ever told it to revoke. See the
    task's final report for the verbatim before/after pytest output."""
    client_a = app_a.test_client()
    r1 = client_a.post('/api/onboarding/create-admin', json={
        'name': 'Owner', 'email': 'owner3@shop.co', 'password': 'OwnerPW11',
    })
    assert r1.status_code == 200, r1.get_json()
    old_admin_uid = _user_on(two_devices["get_conn_a"], email="owner3@shop.co")["uid"]

    _sync_a_to_b(two_devices)
    old_admin_on_b = _user_on(two_devices["get_conn_b"], uid=old_admin_uid)
    assert old_admin_on_b is not None
    caps_before = _capabilities_on(two_devices["get_conn_b"], old_admin_on_b["id"])
    assert caps_before[user_accounts.CAP_EMPLOYEES] is True, (
        "fixture sanity: the owner's grant must have arrived on device B first"
    )

    # Simulate the ONE precondition that unlocks re-onboarding -- see
    # test_create_admin_reonboard_wipe_emits_deletes_for_the_old_admins_
    # grants in commercial_runtime/identity/tests/
    # test_user_permission_sync_emission_write_sites.py for why direct SQL
    # is the correct technique here.
    conn = two_devices["get_conn_a"]()
    try:
        conn.execute("UPDATE users SET password_hash='PENDING' WHERE uid=?", (old_admin_uid,))
        conn.commit()
    finally:
        conn.close()

    r2 = client_a.post('/api/onboarding/create-admin', json={
        'name': 'New Owner', 'email': 'newowner3@shop.co', 'password': 'NewOwnerPW1',
    })
    assert r2.status_code == 200, r2.get_json()

    _sync_a_to_b(two_devices)

    caps_after = _capabilities_on(two_devices["get_conn_b"], old_admin_on_b["id"])
    for code in user_accounts.CAPABILITY_CODES:
        assert caps_after[code] is False, (
            f"the old admin's {code!r} grant STAYED GRANTED on device B after device A revoked it"
        )
