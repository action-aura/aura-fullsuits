"""Phase 5 wave B2 stage 2b -- end-to-end proof that an emitted `user` event
actually crosses devices through the REAL push/pull path (`SyncService.
push_once`/`pull_once`, never `_apply_event` called by hand), plus the
atomicity guarantee stage 2a's whole design rests on: the row write and its
outbox event share ONE registry.db connection, so a fault between them loses
both or neither, never one alone.

See docs/launch-readiness/phase5-waveb2-user-sync.md and
commercial_runtime/sync/tests/test_registry_user_sync_apply.py (stage 2a,
apply-side) for the sibling suite this one is the emit-side counterpart to.

Two independent registry.db files stand in for two tills of the SAME shop
(same company_id, the way device pairing/provisioning would arrange in
production -- out of scope here). A single in-memory `_FakeRelay` plays
Owner's relay: device A's `push_once()` drains its own outbox into it,
device B's `pull_once()` reads from it and applies through the real
`apply_pull_result`/`_apply_event` path.

Run (ONE FILE PER PYTEST PROCESS -- see products/run_all_tests.py):
    pytest commercial_runtime/sync/tests/test_user_sync_emit_two_device_roundtrip.py -v
"""
from __future__ import annotations

import json
import sqlite3
import uuid

import pytest
from flask import Flask

from commercial_runtime.identity import mt_auth, onboarding_routes, registry_db, verification
from commercial_runtime.identity.onboarding_routes import onboarding_bp
from commercial_runtime.identity.user_accounts import _queue_user_sync_event, now_utc_iso
from commercial_runtime.sync.sync_service import REGISTRY_SYNC_ENTITY_TYPES, SyncService


def _conn_factory(db_path):
    def _get_conn():
        c = sqlite3.connect(str(db_path), timeout=30)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA foreign_keys=ON")
        return c
    return _get_conn


def _build_device(tmp_path, name):
    """A real registry.db (full v6 schema, via the real init path) for one
    simulated device, plus a connection factory bound to its OWN fixed path
    -- independent of registry_db's mutable module attributes, so two
    devices can coexist in one test (registry_db._db_dir/DB_PATH are
    process-global and get repatched for the SECOND device immediately
    after this function builds the first)."""
    db_dir = tmp_path / name
    db_path = db_dir / "registry.db"
    import pytest as _pytest  # local monkeypatch, scoped to this helper call
    mp = _pytest.MonkeyPatch()
    mp.setattr(registry_db, "_db_dir", str(db_dir))
    mp.setattr(registry_db, "DB_PATH", str(db_path))
    registry_db.init_registry_db()
    mp.undo()
    return str(db_path), _conn_factory(db_path)


class _FakeRelay:
    """Single shared in-memory relay -- push() appends, pull(since) returns
    everything after `since`, exactly the real SyncRelayClient's contract
    (`{"events": [...], "cursor": N}`)."""
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
    """Flask app whose routes write against DEVICE A's registry.db --
    onboarding_routes/mt_auth patched the same way test_employee_admin_
    routes.py's own fixture does, just pointed at device A's path instead of
    registry_db's own module state (which device B's build already moved
    on from)."""
    monkeypatch.setattr(onboarding_routes, "get_conn", two_devices["get_conn_a"])
    monkeypatch.setattr(mt_auth, "REGISTRY_DB", two_devices["db_path_a"])
    monkeypatch.setenv("AURA_APP_DATA", str(tmp_path / "device_a"))
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


# ── Task C.3 -- end to end, through the REAL push_once/pull_once path ──────

def test_a_user_created_on_device_a_appears_on_device_b(two_devices, app_a):
    client_a = app_a.test_client()
    r = client_a.post('/api/onboarding/create-admin', json={
        'name': 'Owner', 'email': 'owner@shop.co', 'password': 'OwnerPW11',
    })
    assert r.status_code == 200, r.get_json()

    _sync_a_to_b(two_devices)

    b_row = _user_on(two_devices["get_conn_b"], email="owner@shop.co")
    assert b_row is not None, "the admin created on device A never reached device B"
    assert b_row["role"] == "admin"
    assert b_row["company_id"] == COMPANY_ID, "device B must stamp its OWN company_id, never the payload's"


def test_a_role_change_on_device_a_reaches_device_b(two_devices, app_a):
    client_a = app_a.test_client()
    client_a.post('/api/onboarding/create-admin', json={
        'name': 'Owner', 'email': 'owner2@shop.co', 'password': 'OwnerPW11',
    })
    user_id, email = _make_employee(client_a)
    _sync_a_to_b(two_devices)
    assert _user_on(two_devices["get_conn_b"], email=email)["role"] != "manager"

    r = client_a.put(f'/api/admin/employees/{user_id}/role', json={'role': 'manager'})
    assert r.status_code == 200, r.get_json()
    _sync_a_to_b(two_devices)

    b_row = _user_on(two_devices["get_conn_b"], email=email)
    assert b_row["role"] == "manager", "the role change on device A never reached device B"


def test_a_password_change_on_device_a_reaches_device_b(two_devices, app_a):
    client_a = app_a.test_client()
    client_a.post('/api/onboarding/create-admin', json={
        'name': 'Owner', 'email': 'owner3@shop.co', 'password': 'OwnerPW11',
    })
    user_id, email = _make_employee(client_a)
    client_a.put(f'/api/admin/employees/{user_id}/status', json={'status': 'active'})
    _sync_a_to_b(two_devices)
    before_hash_on_b = _user_on(two_devices["get_conn_b"], email=email)["password_hash"]

    conn = two_devices["get_conn_a"]()
    try:
        raw_token = verification._create_link(
            conn, company_id=COMPANY_ID, email=email, purpose="password_reset", ttl_hours=1,
        )
        conn.commit()
    finally:
        conn.close()
    r = client_a.post('/api/auth/reset-password', json={'token': raw_token, 'password': 'BrandNewPW1'})
    assert r.status_code == 200, r.get_json()

    _sync_a_to_b(two_devices)

    a_row = _user_on(two_devices["get_conn_a"], email=email)
    b_row = _user_on(two_devices["get_conn_b"], email=email)
    assert b_row["password_hash"] == a_row["password_hash"], "the new password hash never reached device B"
    assert b_row["password_hash"] != before_hash_on_b


def test_a_suspend_on_device_a_reaches_device_b(two_devices, app_a):
    client_a = app_a.test_client()
    client_a.post('/api/onboarding/create-admin', json={
        'name': 'Owner', 'email': 'owner4@shop.co', 'password': 'OwnerPW11',
    })
    user_id, email = _make_employee(client_a)
    _sync_a_to_b(two_devices)
    assert _user_on(two_devices["get_conn_b"], email=email)["status"] != "disabled"

    r = client_a.put(f'/api/admin/employees/{user_id}/status', json={'status': 'disabled'})
    assert r.status_code == 200, r.get_json()
    _sync_a_to_b(two_devices)

    b_row = _user_on(two_devices["get_conn_b"], email=email)
    assert b_row["status"] == "disabled", "the suspend on device A never reached device B"


# ── Task C.5 -- row + event are atomic within registry.db ──────────────────

def _write_and_queue_status_change(conn, user_id, new_status):
    """The exact shape update_status's SQL takes -- reproduced directly
    (not via HTTP) so the test controls the commit/rollback boundary
    precisely."""
    conn.execute(
        "UPDATE users SET status=?, row_version=COALESCE(row_version, 1)+1, "
        "updated_at_utc=? WHERE id=?",
        (new_status, now_utc_iso(), user_id),
    )
    _queue_user_sync_event(conn, user_id, "update")


def _seed_bare_user(get_conn):
    row = {
        "id": str(uuid.uuid4()), "company_id": COMPANY_ID, "employee_id": "E1",
        "email": f"atomic-{uuid.uuid4().hex[:8]}@x.com", "password_hash": "H",
        "role": "cashier", "status": "active", "require_password_change": 0,
        "session_version": 1, "language": "en", "clinic_role": "",
        "failed_login_count": 0, "locked_until": None, "uid": str(uuid.uuid4()),
        "pin_hash": None, "row_version": 1, "updated_at_utc": "2026-01-01T00:00:00+00:00",
    }
    conn = get_conn()
    try:
        conn.execute(
            "INSERT INTO users (id, company_id, employee_id, email, password_hash, role, status, "
            "require_password_change, session_version, language, clinic_role, failed_login_count, "
            "locked_until, uid, pin_hash, row_version, updated_at_utc) "
            "VALUES (:id,:company_id,:employee_id,:email,:password_hash,:role,:status,"
            ":require_password_change,:session_version,:language,:clinic_role,:failed_login_count,"
            ":locked_until,:uid,:pin_hash,:row_version,:updated_at_utc)",
            row,
        )
        conn.commit()
    finally:
        conn.close()
    return row


def test_a_fault_before_commit_loses_both_the_row_change_and_the_event(two_devices):
    get_conn = two_devices["get_conn_a"]
    row = _seed_bare_user(get_conn)

    conn = get_conn()
    try:
        _write_and_queue_status_change(conn, row["id"], "disabled")
        # Simulate the process dying HERE, before commit() is ever reached --
        # close the connection outright rather than committing.
        conn.close()
    except Exception:
        pass

    fresh = get_conn()
    try:
        after = dict(fresh.execute("SELECT * FROM users WHERE id=?", (row["id"],)).fetchone())
        outbox = fresh.execute("SELECT COUNT(*) FROM sync_outbox").fetchone()[0]
    finally:
        fresh.close()
    assert after["status"] == "active", "the uncommitted row change must not be visible"
    assert after["row_version"] == 1, "the uncommitted row_version bump must not be visible"
    assert outbox == 0, "the uncommitted outbox event must not be visible either"


def test_a_clean_commit_lands_both_the_row_change_and_the_event_together(two_devices):
    get_conn = two_devices["get_conn_a"]
    row = _seed_bare_user(get_conn)

    conn = get_conn()
    try:
        _write_and_queue_status_change(conn, row["id"], "disabled")
        conn.commit()
    finally:
        conn.close()

    fresh = get_conn()
    try:
        after = dict(fresh.execute("SELECT * FROM users WHERE id=?", (row["id"],)).fetchone())
        outbox_rows = fresh.execute(
            "SELECT entity_type, entity_id, event_type FROM sync_outbox"
        ).fetchall()
    finally:
        fresh.close()
    assert after["status"] == "disabled"
    assert after["row_version"] == 2
    assert len(outbox_rows) == 1
    assert outbox_rows[0]["entity_id"] == row["uid"]


# ── Task C.6 -- no plaintext credential in the raw stored outbox row ───────

def test_no_plaintext_password_in_the_raw_stored_outbox_payload(two_devices, app_a):
    client_a = app_a.test_client()
    client_a.post('/api/onboarding/create-admin', json={
        'name': 'Owner', 'email': 'owner5@shop.co', 'password': 'SuperSecretPW1',
    })
    user_id, email = _make_employee(client_a)
    client_a.put(f'/api/admin/employees/{user_id}/status', json={'status': 'active'})

    conn = two_devices["get_conn_a"]()
    try:
        raw_token = verification._create_link(
            conn, company_id=COMPANY_ID, email=email, purpose="password_reset", ttl_hours=1,
        )
        conn.commit()
    finally:
        conn.close()
    client_a.post('/api/auth/reset-password', json={'token': raw_token, 'password': 'AnotherSecretPW2'})
    client_a.put(f'/api/admin/employees/{user_id}/pin', json={'pin': '9876'})

    conn = two_devices["get_conn_a"]()
    try:
        raw_rows = conn.execute("SELECT payload FROM sync_outbox").fetchall()
    finally:
        conn.close()

    blob = "\n".join(r[0] for r in raw_rows)
    assert "SuperSecretPW1" not in blob
    assert "AnotherSecretPW2" not in blob
    assert "9876" not in blob
    for r in raw_rows:
        payload = json.loads(r[0])
        pw = payload.get("password_hash")
        # 'PENDING' is the documented sentinel create_employee stores before
        # the invite is ever redeemed (onboarding_routes.py) -- not a hash,
        # but also not a credential; every REAL hash must carry the modern
        # prefix, never anything that looks like the plaintext itself.
        if pw and pw != "PENDING":
            assert pw.startswith("pbkdf2_sha256$"), f"password_hash payload was not a real hash: {pw!r}"
        if payload.get("pin_hash"):
            assert payload["pin_hash"].startswith("pbkdf2_sha256$")
