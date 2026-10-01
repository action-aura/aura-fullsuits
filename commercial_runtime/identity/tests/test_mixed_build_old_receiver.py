"""Mixed builds (Registry v9, Track S): the OLD build (commit 1d15b35e, registry
v8, twelve-code-blind) exchanging registry sync events with THIS build.

A shop upgrades one computer at a time, so for days a v9 computer and a v8
computer sit on the same sync stream. Two directions, both real:

  NEW -> OLD  Every event this build queues (user create/update carrying the
              new `display_name`/`role_preset` keys, the four books
              `user_permission` rows, `device` and `admin_device` events) is
              fed to the OLD tree's REAL `SyncService._apply_event` running in
              a subprocess against a REAL v8 registry.db. It must apply or
              plain-skip every one of them: ZERO rows in
              `sync_apply_quarantine`, because a quarantined event is a
              permission change that silently never arrives.
  OLD -> NEW  A payload produced by the OLD tree's REAL
              `_queue_user_sync_event` is fed to this build's pure receive
              rules. It carries neither new key, so a PIN change on an old
              computer must not blank the Accountant preset / the name
              (absent key means keep), and an old computer changing an
              Accountant's role must make this build take the books away
              (fail closed).

Why a subprocess: both trees are the package `commercial_runtime`; two copies
cannot live in one interpreter. The old tree is a checkout of 1d15b35e at
`AURA_P2S_OLDTREE` (default: the wave's scratch copy). Without it the whole
file skips -- and says so -- rather than passing on nothing.

What is NOT proved here (needs the new tree's `sync_service.py`, another crew's
file): that the new receiver actually uses these rules with the CASE-WHEN
column form. That is Y's `retail_s_mixed_build_twodevice_test.py`.

Run (ONE FILE PER PYTEST PROCESS):
    pytest commercial_runtime/identity/tests/test_mixed_build_old_receiver.py -v
"""
import json
import os
import sqlite3
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from flask import Flask

from commercial_runtime.identity import mt_auth, onboarding_routes, registry_db
from commercial_runtime.identity import device_registry as dr
from commercial_runtime.identity import user_accounts as ua
from commercial_runtime.identity.onboarding_routes import onboarding_bp
from commercial_runtime.licensing_contracts import flask_guard
from commercial_runtime.licensing_contracts.state_repository import LicenseStateRecord

OLD_TREE = Path(os.environ.get(
    "AURA_P2S_OLDTREE", r"C:\Users\MSI\.claude\jobs\c5a4f4c4\tmp\p2s\oldtree"))

pytestmark = pytest.mark.skipif(
    not (OLD_TREE / "commercial_runtime" / "identity" / "registry_db.py").exists(),
    reason=f"old tree (commit 1d15b35e) not present at {OLD_TREE}; mixed-build proof NOT run")

RECEIVER_CO = "company-x"

# Runs INSIDE the old tree. Reads {"events": [...]} / {"role": ...} on stdin,
# prints one `RESULT:` line of JSON. It also reports which files it imported
# and the old registry version, so the test can prove it really ran the OLD
# code and not this build by accident.
_RUNNER = r'''
import json, os, sys, uuid
oldtree, workdir, mode = sys.argv[1], sys.argv[2], sys.argv[3]
sys.path.insert(0, oldtree)
os.chdir(oldtree)
spec = json.loads(sys.stdin.read())
from commercial_runtime.identity import registry_db
registry_db._db_dir = workdir
registry_db.DB_PATH = os.path.join(workdir, "registry.db")
registry_db.init_registry_db()
from commercial_runtime.identity import user_accounts as accounts
CO = "company-x"
out = {"registry_file": registry_db.__file__,
       "registry_version": registry_db.REGISTRY_SCHEMA_VERSION,
       "old_codes": list(accounts.CAPABILITY_CODES)}
if mode == "receive":
    from commercial_runtime.sync.sync_service import REGISTRY_SYNC_ENTITY_TYPES, SyncService
    from commercial_runtime.identity import onboarding_routes as orr
    svc = SyncService(client_factory=lambda: None, get_conn=registry_db.get_conn,
                      local_company_id_provider=lambda: CO,
                      handled_entity_types=REGISTRY_SYNC_ENTITY_TYPES)
    handled = []
    for ev in spec["events"]:
        conn = registry_db.get_conn()
        try:
            handled.append([ev["entity_type"], svc._apply_event(conn, ev, CO)])
            conn.commit()
        finally:
            conn.close()
    conn = registry_db.get_conn()
    try:
        out["handled"] = handled
        out["quarantine"] = [dict(r) for r in conn.execute(
            "SELECT entity_type, event_type, reason, detail FROM sync_apply_quarantine")]
        users = []
        for u in conn.execute("SELECT id, uid, role, email FROM users"):
            perms = {r["subsystem"]: r["access_level"] for r in conn.execute(
                "SELECT subsystem, access_level FROM user_permissions WHERE user_id=?", (u["id"],))}
            users.append({"uid": u["uid"], "role": u["role"], "email": u["email"], "perms": perms,
                          "session_caps": orr._capabilities_for_session(conn, u["id"], u["role"])})
        out["users"] = users
    finally:
        conn.close()
elif mode == "emit":
    uid = str(uuid.uuid4())
    conn = registry_db.get_conn()
    try:
        conn.execute(
            "INSERT INTO users (id, company_id, employee_id, email, password_hash, role, status, "
            "require_password_change, session_version, language, clinic_role, failed_login_count, "
            "locked_until, uid, pin_hash, row_version, updated_at_utc) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (str(uuid.uuid4()), CO, "E-1", "old@x.com", "H", spec["role"], "active", 0, 1, "en", "",
             0, None, uid, spec.get("pin_hash"), 5, "2026-01-01T00:00:00+00:00"))
        row = conn.execute("SELECT id FROM users WHERE uid=?", (uid,)).fetchone()
        accounts._queue_user_sync_event(conn, row["id"], "update")
        conn.commit()
        ev = conn.execute("SELECT entity_type, entity_id, event_type, payload FROM sync_outbox").fetchone()
        out["event"] = {"entity_type": ev["entity_type"], "entity_id": ev["entity_id"],
                        "event_type": ev["event_type"], "payload": json.loads(ev["payload"])}
    finally:
        conn.close()
print("RESULT:" + json.dumps(out))
'''


def _run_old(tmp_path, mode, spec):
    script = tmp_path / "old_runner.py"
    script.write_text(_RUNNER, encoding="utf-8")
    workdir = tmp_path / "old_db"
    workdir.mkdir()
    proc = subprocess.run(
        [sys.executable, str(script), str(OLD_TREE), str(workdir), mode],
        input=json.dumps(spec), capture_output=True, text=True, timeout=180,
        env={**os.environ, "PYTHONPATH": str(OLD_TREE), "PYTHONIOENCODING": "utf-8"})
    assert proc.returncode == 0, proc.stderr[-2000:]
    line = [ln for ln in proc.stdout.splitlines() if ln.startswith("RESULT:")][-1]
    out = json.loads(line[len("RESULT:"):])
    # It really was the OLD code: imported from the old tree, registry v8.
    assert Path(out["registry_file"]).resolve().is_relative_to(OLD_TREE.resolve()), out["registry_file"]
    assert out["registry_version"] == 8
    assert len(out["old_codes"]) == 8
    return out


# ── the NEW sender's fixtures (same shape as test_role_presets.py) ───────────

@pytest.fixture
def db_path(tmp_path, monkeypatch):
    path = tmp_path / "new_registry.db"
    monkeypatch.setattr(registry_db, "DB_PATH", str(path))
    monkeypatch.setattr(registry_db, "_db_dir", str(tmp_path))
    registry_db.init_registry_db()
    return path


@pytest.fixture
def admin(db_path, tmp_path, monkeypatch):
    def _get_conn():
        c = sqlite3.connect(str(db_path), timeout=30)
        c.row_factory = sqlite3.Row
        return c

    monkeypatch.setattr(onboarding_routes, "get_conn", _get_conn)
    monkeypatch.setenv("AURA_APP_DATA", str(tmp_path))
    monkeypatch.setattr(mt_auth, "REGISTRY_DB", str(db_path))
    monkeypatch.setattr(flask_guard.LicenseStateRepository, "__init__", lambda self, db_path: None)
    monkeypatch.setattr(
        flask_guard.LicenseStateRepository, "load",
        lambda self: LicenseStateRecord(
            licensing_schema_version=1, product_code="AURA_TEST", platform="WINDOWS",
            current_state="ACTIVE_ONLINE"))
    flask_app = Flask(__name__)
    flask_app.secret_key = "test-secret"
    flask_app.testing = True
    flask_app.register_blueprint(onboarding_bp)
    client = flask_app.test_client()
    r = client.post('/api/onboarding/create-admin', json={
        'name': 'Owner', 'email': 'owner@test.local', 'password': 'OwnerPW11'})
    assert r.status_code == 200, r.get_json()
    return client


def _rows(db_path, sql, args=()):
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute(sql, args).fetchall()
    finally:
        conn.close()


def _outbox_events(db_path):
    # sync_outbox.id is a random uuid: insertion order is the rowid.
    return [{"entity_type": r["entity_type"], "entity_id": r["entity_id"],
             "event_type": r["event_type"], "payload": json.loads(r["payload"])}
            for r in _rows(db_path, "SELECT entity_type, entity_id, event_type, payload "
                                    "FROM sync_outbox ORDER BY rowid")]


def _queue_device_events(db_path):
    """A main computer claim plus a peer announce -> one `admin_device` and two
    `device` events, built by the real functions (no hand-written payloads)."""
    company = _rows(db_path, "SELECT company_id FROM users LIMIT 1")[0][0]
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        main_id, peer_id = str(uuid.uuid4()), str(uuid.uuid4())
        dr.upsert_local_device(conn, main_id, company, platform="WINDOWS")
        dr.queue_device_sync_event(conn, main_id, "create")
        dr.claim_admin_device_with_assignment(conn, company, main_id)
        dr.upsert_local_device(conn, peer_id, company, platform="ANDROID")
        dr.queue_device_sync_event(conn, peer_id, "create")
        conn.commit()
    finally:
        conn.close()


# ── NEW -> OLD ───────────────────────────────────────────────────────────────

def test_old_receiver_applies_or_skips_every_new_event_with_nothing_quarantined(admin, db_path, tmp_path):
    """The whole v9 event menu against a real v8 receiver."""
    r = admin.post('/api/admin/employees', json={
        'email': 'layla@test.local', 'preset': 'accountant', 'name': 'Layla'})
    assert r.status_code == 200, r.get_json()
    r = admin.post('/api/admin/employees', json={'email': 'omar@test.local', 'role': 'cashier'})
    assert r.status_code == 200, r.get_json()
    _queue_device_events(db_path)
    events = _outbox_events(db_path)

    # Count the check, not the outcome: the stream really contains every kind
    # of event this proof claims to cover, including the new payload keys and
    # the four books rows -- otherwise "0 quarantined" would be vacuous.
    kinds = {e["entity_type"] for e in events}
    assert {"user", "user_permission", "device", "admin_device"} <= kinds
    layla_uid = next(e["entity_id"] for e in events
                     if e["entity_type"] == "user" and e["payload"].get("email") == "layla@test.local")
    layla_user = [e for e in events if e["entity_type"] == "user" and e["entity_id"] == layla_uid]
    assert all("display_name" in e["payload"] and "role_preset" in e["payload"] for e in layla_user)
    assert layla_user[-1]["payload"]["display_name"] == "Layla"
    assert layla_user[-1]["payload"]["role_preset"] == "accountant"
    books_rows = [e for e in events if e["entity_type"] == "user_permission"
                  and e["payload"].get("subsystem") in ua.BOOKS_CAPABILITY_CODES]
    assert books_rows, "no books user_permission event was queued at all"

    out = _run_old(tmp_path, "receive", {"events": events})

    # 1. Nothing parked.
    assert out["quarantine"] == []
    # 2. Every event was reported "fully handled" (device kinds are plain skips).
    assert len(out["handled"]) == len(events)
    assert all(ok is True for _, ok in out["handled"]), out["handled"]
    assert {t for t, _ in out["handled"]} >= {"device", "admin_device"}
    # 3. The person really arrived, with the books rows STORED (old code writes
    #    any subsystem it is handed) but not LISTED: the old session shows only
    #    the eight codes it knows.
    layla = next(u for u in out["users"] if u["uid"] == layla_uid)
    assert layla["role"] == "manager"
    assert layla["perms"]["retail.books.view"] == "full"
    old_codes = set(out["old_codes"])
    assert not old_codes & set(ua.BOOKS_CAPABILITY_CODES)
    assert layla["session_caps"] == sorted(c for c in ua.ROLE_PRESETS["accountant"] if c in old_codes)
    assert not set(layla["session_caps"]) & set(ua.BOOKS_CAPABILITY_CODES)
    # 4. The cashier is intact too (no cross-user bleed from the books rows).
    omar = next(u for u in out["users"] if u["email"] == "omar@test.local")
    assert omar["role"] == "cashier"
    assert set(omar["session_caps"]) == set(ua.ROLE_CAPABILITIES[ua.ROLE_CASHIER])


# ── OLD -> NEW ───────────────────────────────────────────────────────────────

def test_old_sender_payload_carries_neither_new_key(tmp_path):
    """The premise every OLD -> NEW rule stands on: a v8 build never writes
    display_name / role_preset. If the old tree ever grew them this fails and
    the 'absent means keep' rules would be guarding nothing."""
    out = _run_old(tmp_path, "emit", {"role": "cashier", "pin_hash": "PINHASH"})
    payload = out["event"]["payload"]
    assert "display_name" not in payload and "role_preset" not in payload
    assert payload["pin_hash"] == "PINHASH" and payload["role"] == "cashier"


def test_old_pin_change_does_not_blank_the_name_or_the_preset(tmp_path):
    out = _run_old(tmp_path, "emit", {"role": "manager", "pin_hash": "NEWPIN"})
    plan = ua.user_sync_apply_plan(out["event"]["payload"], "manager")   # same role: a PIN-only change
    assert (plan["has_display_name"], plan["has_role_preset"]) == (False, False)
    assert plan["reset_books"] is False


def test_old_computer_changing_an_accountants_role_takes_the_books_away(tmp_path):
    out = _run_old(tmp_path, "emit", {"role": "cashier"})
    # The receiver's stored role for this person is 'manager' (the Accountant).
    plan = ua.user_sync_apply_plan(out["event"]["payload"], "manager")
    assert plan["reset_books"] is True
    assert plan["books_plan"] == {code: "none" for code in ua.BOOKS_CAPABILITY_CODES}
    # Both directions: the same old payload for a person who was ALREADY a
    # cashier here changes nothing (no needless rewrite of the four rows).
    assert ua.user_sync_apply_plan(out["event"]["payload"], "cashier")["reset_books"] is False


# ── S-AUTH (AUDIT-033): the new `_auth` block ──────────────────────────────

def test_old_receiver_ignores_the_signature_block_on_authority_events(admin, db_path, tmp_path):
    """From now on this build signs every authority event, so a v8 computer sits on
    a stream whose `user` / `user_permission` / `device` / `admin_device` payloads
    carry a reserved `_auth` key (a signature plus the sender's Owner-signed
    lease). The OLD tree's REAL `_apply_event` reads payloads with `.get` and an
    allowlist of columns, so it must apply or plain-skip every one of them exactly
    as it does the unsigned form -- nothing quarantined, the person still arrives.
    A quarantined event here is a permission change that silently never reaches
    the old computer.

    Non-vacuous: every authority event in the stream is checked to REALLY carry an
    `_auth` block before it is sent, and its `lease` is a full Owner envelope."""
    from commercial_runtime.sync import authority
    from commercial_runtime.sync.tests._signing import SigningWorld

    r = admin.post('/api/admin/employees', json={'email': 'layla@test.local', 'role': 'cashier'})
    assert r.status_code == 200, r.get_json()
    _queue_device_events(db_path)
    events = _outbox_events(db_path)

    world = SigningWorld(tmp_path)
    signer = world.new_device("dev-main")
    signed = [world.sign(e, signer) if authority.is_authority_event(e) else e for e in events]
    authority_events = [e for e in signed if authority.is_authority_event(e)]
    assert {e["entity_type"] for e in authority_events} >= {"user", "user_permission", "device", "admin_device"}
    assert all("sig" in e["payload"]["_auth"] and "payload" in e["payload"]["_auth"]["lease"]
               for e in authority_events)

    out = _run_old(tmp_path, "receive", {"events": signed})

    assert out["quarantine"] == []
    assert len(out["handled"]) == len(signed)
    assert all(ok is True for _, ok in out["handled"]), out["handled"]
    assert any(u["email"] == "layla@test.local" for u in out["users"])
