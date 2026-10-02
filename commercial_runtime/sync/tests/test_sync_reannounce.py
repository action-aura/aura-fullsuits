"""AUDIT-033 B2b -- the signed RE-ANNOUNCE and the supersede cleanup.

Relays keep history, so a computer that joins after the upgrade receives the shop's
PRE-upgrade authority events, unsigned, and parks every one. These tests pin the
two halves of the cure against a REAL registry.db and REAL signatures:

  * `reannounce.queue_registry_state` / `run_if_due` queue the shop's state with the
    SAME versions (never bumped), once per device and again when it becomes main;
  * the receiver treats an equal-version re-emit as a no-op where it has the row
    and applies it where it lacks it;
  * a verified signed event clears the parked UNSIGNED copies it answers -- and
    never a parked row whose reason is a forgery signal, never a newer one.

Every deny has its allow and vice versa. MUTATION-PROVEN (see B2b/progress.md).

Run (ONE FILE PER PYTEST PROCESS):
    pytest commercial_runtime/sync/tests/test_sync_reannounce.py -q
"""
from __future__ import annotations

import json
import uuid

import pytest

from commercial_runtime.identity import registry_db
from commercial_runtime.sync import authority, reannounce
from commercial_runtime.sync.sync_service import REGISTRY_SYNC_ENTITY_TYPES, SyncService
from commercial_runtime.sync.tests._signing import SigningWorld, install_main
from commercial_runtime.sync.tests.test_sync_authority_rules import COMPANY, OWNER, CASHIER, Shop

FORGERY_REASONS = (
    "bad_signature", "foreign_licence", "untrusted_lease", "signer_binding_clash", "signer_revoked",
    "needs_main_computer", "owner_change_needs_main", "device_change_needs_main",
    "second_admin_refused", "release_ack_wrong_sender", "alias_origin_mismatch",
    "admin_claim_signer_mismatch", "takeover_signer_mismatch", "takeover_main_not_silent",
)


@pytest.fixture
def shop(tmp_path, monkeypatch):
    return Shop(tmp_path, monkeypatch)


# ── helpers ──────────────────────────────────────────────────────────────────

def _add_permissions(shop, rows):
    conn = shop.get_conn()
    try:
        for uid, subsystem, level in rows:
            user_id = conn.execute("SELECT id FROM users WHERE uid=?", (uid,)).fetchone()[0]
            conn.execute("INSERT INTO user_permissions (id, user_id, subsystem, access_level) VALUES (?,?,?,?)",
                         (str(uuid.uuid4()), user_id, subsystem, level))
        conn.commit()
    finally:
        conn.close()


def _emit(shop, *, is_main, dev):
    """Run the registry re-announce on THIS registry and return its outbox as events."""
    conn = shop.get_conn()
    try:
        conn.execute("DELETE FROM sync_outbox")
        queued = reannounce.queue_registry_state(conn, COMPANY, dev, is_main=is_main)
        conn.commit()
        rows = conn.execute(
            "SELECT id, entity_type, entity_id, event_type, payload, created_at FROM sync_outbox ORDER BY rowid"
        ).fetchall()
    finally:
        conn.close()
    events = [{"id": r[0], "entity_type": r[1], "entity_id": r[2], "event_type": r[3],
               "payload": json.loads(r[4]), "created_at": r[5]} for r in rows]
    assert queued == len(events)
    return events


def _snapshot(shop):
    return {
        "users": shop.q("SELECT uid, role, status, password_hash, pin_hash, row_version, email FROM users ORDER BY uid"),
        "perms": shop.q("SELECT u.uid AS uid, p.subsystem AS subsystem, p.access_level AS access_level "
                        "FROM user_permissions p JOIN users u ON u.id=p.user_id ORDER BY 1,2"),
        "devices": shop.q("SELECT id, row_version, admin_row_version, status, device_label FROM devices ORDER BY id"),
        "assignment": shop.q("SELECT device_id, epoch FROM admin_device_assignment"),
    }


def _apply(shop, ev, signer=None):
    """Apply ONE event the way the sync loop does (`_apply_event_guarded`: its own
    savepoint, and the supersede cleanup that follows a verified apply)."""
    if signer is not None:
        ev = shop.world.sign(ev, signer)
    conn = shop.get_conn()
    try:
        handled = shop.svc._apply_event_guarded(conn, ev, local_company_id=COMPANY)
        conn.commit()
        return handled
    finally:
        conn.close()


def _park(shop, entity_type, entity_id, event_type, payload, reason):
    conn = shop.get_conn()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO sync_apply_quarantine (entity_id, entity_type, event_type, payload, reason, detail) "
            "VALUES (?,?,?,?,?,?)", (entity_id, entity_type, event_type, json.dumps(payload), reason, "test"))
        conn.commit()
    finally:
        conn.close()


def _parked(shop):
    return {(r["entity_type"], r["entity_id"], r["event_type"]): r["reason"] for r in
            shop.q("SELECT entity_type, entity_id, event_type, reason FROM sync_apply_quarantine")}


def _new_user_event(uid, *, row_version=1, role="cashier", email=None, **over):
    payload = {"uid": uid, "employee_id": f"EMP-{uid[-4:]}", "email": email or f"{uid}@x.com", "role": role,
               "status": "active", "language": "en", "password_hash": f"HASH-{uid}", "pin_hash": f"PIN-{uid}",
               "session_version": 1, "display_name": f"Name {uid}", "require_password_change": 0,
               "row_version": row_version, "updated_at_utc": "2026-03-01T00:00:00+00:00"}
    payload.update(over)
    return {"entity_type": "user", "entity_id": uid, "event_type": "update", "payload": payload}


def _perm_event(uid, subsystem, level, *, reannounce_flag=True):
    payload = {"user_uid": uid, "subsystem": subsystem, "access_level": level}
    if reannounce_flag:
        payload["_reannounce"] = True
    return {"entity_type": "user_permission", "entity_id": str(uuid.uuid4()), "event_type": "update",
            "payload": payload}


# ═══ what the main computer queues ═══════════════════════════════════════════

def test_the_main_computer_restates_the_whole_shop_without_bumping_any_version(shop):
    _add_permissions(shop, [(OWNER, "retail", "full"), (OWNER, "books.view", "full"),
                            (CASHIER, "retail", "read")])
    before = _snapshot(shop)
    events = _emit(shop, is_main=True, dev=shop.M.dev)

    kinds = [(e["entity_type"], e["event_type"]) for e in events]
    assert kinds.count(("device", "update")) == 1
    assert kinds.count(("admin_device", "update")) == 1
    assert sorted(e["entity_id"] for e in events if e["entity_type"] == "user") == sorted([OWNER, CASHIER])
    perms = [e["payload"] for e in events if e["entity_type"] == "user_permission"]
    assert sorted((p["user_uid"], p["subsystem"], p["access_level"]) for p in perms) == [
        (CASHIER, "retail", "read"), (OWNER, "books.view", "full"), (OWNER, "retail", "full")]
    assert all(p.get("_reannounce") is True for p in perms), "every permission must carry the signed marker"
    # THE POINT: same versions. Nothing in the registry moved, and every payload states the stored version.
    assert _snapshot(shop) == before
    owner_payload = next(e["payload"] for e in events if e["entity_type"] == "user" and e["entity_id"] == OWNER)
    assert owner_payload["row_version"] == before["users"][[u["uid"] for u in before["users"]].index(OWNER)]["row_version"]
    assert next(e["payload"] for e in events if e["entity_type"] == "admin_device")["epoch"] == before["assignment"][0]["epoch"]


def test_an_ordinary_computer_restates_only_itself(shop):
    _add_permissions(shop, [(OWNER, "retail", "full")])
    events = _emit(shop, is_main=False, dev=shop.A.dev)
    assert [(e["entity_type"], e["entity_id"]) for e in events] == [("device", shop.A.dev)]


def test_a_soft_deleted_account_is_not_resurrected_by_a_restatement(shop):
    conn = shop.get_conn()
    try:
        conn.execute("UPDATE users SET deleted_at_utc='2026-02-01T00:00:00+00:00' WHERE uid=?", (CASHIER,))
        conn.commit()
    finally:
        conn.close()
    events = _emit(shop, is_main=True, dev=shop.M.dev)
    assert [e["entity_id"] for e in events if e["entity_type"] == "user"] == [OWNER]


# ═══ what a receiver does with it ═══════════════════════════════════════════

def test_an_equal_version_restatement_changes_nothing_where_the_rows_already_exist(shop):
    _add_permissions(shop, [(OWNER, "retail", "full"), (CASHIER, "retail", "read")])
    events = [shop.world.sign(e, shop.M) for e in _emit(shop, is_main=True, dev=shop.M.dev)]
    before = _snapshot(shop)
    shop.pull(events)
    assert _snapshot(shop) == before
    assert shop.parked() == [], "an equal re-emit must not be parked either"
    assert shop.q("SELECT COUNT(*) AS n FROM sync_outbox")[0]["n"] == len(events), (
        "applying a re-announce must never emit one of its own (that would loop)")


def test_a_restatement_never_overwrites_a_permission_the_receiver_already_has(shop):
    _add_permissions(shop, [(CASHIER, "retail", "read")])
    ev = shop.world.sign(_perm_event(CASHIER, "retail", "full"), shop.M)
    shop.pull([ev])
    assert [p["access_level"] for p in _snapshot(shop)["perms"] if p["subsystem"] == "retail"] == ["read"]


def test_the_same_permission_event_without_the_marker_is_an_ordinary_overwrite(shop):
    """The allow half of the marker: a REAL (non-re-announce) change still lands."""
    _add_permissions(shop, [(CASHIER, "retail", "read")])
    ev = shop.world.sign(_perm_event(CASHIER, "retail", "full", reannounce_flag=False), shop.A)
    shop.pull([ev])
    assert [p["access_level"] for p in _snapshot(shop)["perms"] if p["subsystem"] == "retail"] == ["full"]


def test_a_restatement_applies_on_a_registry_that_has_none_of_it(tmp_path, monkeypatch):
    (tmp_path / "main").mkdir()
    emitter = Shop(tmp_path / "main", monkeypatch)
    _add_permissions(emitter, [(OWNER, "retail", "full"), (CASHIER, "retail", "read"), (CASHIER, "books.view", "none")])
    want = _snapshot(emitter)
    signed = [emitter.world.sign(e, emitter.M) for e in _emit(emitter, is_main=True, dev=emitter.M.dev)]

    # A brand-new computer: empty registry, same Owner trust, its own device identity.
    db_dir = tmp_path / "fresh" / "database"
    monkeypatch.setattr(registry_db, "_db_dir", str(db_dir))
    monkeypatch.setattr(registry_db, "DB_PATH", str(db_dir / "registry.db"))
    registry_db.init_registry_db()
    fresh_dev = emitter.world.new_device("dev-fresh")
    svc = SyncService(client_factory=lambda: None, get_conn=registry_db.get_conn,
                      local_company_id_provider=lambda: COMPANY,
                      handled_entity_types=REGISTRY_SYNC_ENTITY_TYPES,
                      authority_env_provider=emitter.world.provider(fresh_dev))
    conn = registry_db.get_conn()
    try:
        assert conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0
        svc.apply_pull_result(conn, {"events": signed, "cursor": 1})
        conn.commit()
        got = {
            "users": [dict(r) for r in conn.execute(
                "SELECT uid, role, status, password_hash, pin_hash, row_version, email FROM users ORDER BY uid")],
            "perms": [dict(r) for r in conn.execute(
                "SELECT u.uid AS uid, p.subsystem AS subsystem, p.access_level AS access_level "
                "FROM user_permissions p JOIN users u ON u.id=p.user_id ORDER BY 1,2")],
            "assignment": [dict(r) for r in conn.execute("SELECT device_id, epoch FROM admin_device_assignment")],
            "parked": [dict(r) for r in conn.execute("SELECT reason FROM sync_apply_quarantine")],
        }
    finally:
        conn.close()
    assert got["parked"] == []
    assert got["users"] == want["users"]
    assert got["perms"] == want["perms"]
    assert got["assignment"] == want["assignment"]


def test_restatements_that_arrive_in_the_wrong_order_still_converge(tmp_path, monkeypatch):
    (tmp_path / "main").mkdir()
    emitter = Shop(tmp_path / "main", monkeypatch)
    _add_permissions(emitter, [(OWNER, "retail", "full"), (CASHIER, "retail", "read")])
    want_perms = _snapshot(emitter)["perms"]
    signed = [emitter.world.sign(e, emitter.M) for e in _emit(emitter, is_main=True, dev=emitter.M.dev)]
    db_dir = tmp_path / "fresh" / "database"
    monkeypatch.setattr(registry_db, "_db_dir", str(db_dir))
    monkeypatch.setattr(registry_db, "DB_PATH", str(db_dir / "registry.db"))
    registry_db.init_registry_db()
    svc = SyncService(client_factory=lambda: None, get_conn=registry_db.get_conn,
                      local_company_id_provider=lambda: COMPANY,
                      handled_entity_types=REGISTRY_SYNC_ENTITY_TYPES,
                      authority_env_provider=emitter.world.provider(emitter.world.new_device("dev-fresh")))
    conn = registry_db.get_conn()
    try:
        for cursor in (1, 2, 3):   # permissions and users first, the main's claim last
            svc.apply_pull_result(conn, {"events": list(reversed(signed)), "cursor": cursor})
            conn.commit()
        got_perms = [dict(r) for r in conn.execute(
            "SELECT u.uid AS uid, p.subsystem AS subsystem, p.access_level AS access_level "
            "FROM user_permissions p JOIN users u ON u.id=p.user_id ORDER BY 1,2")]
        parked = [dict(r) for r in conn.execute("SELECT entity_type, reason FROM sync_apply_quarantine")]
    finally:
        conn.close()
    assert got_perms == want_perms
    assert parked == []


# ═══ supersede cleanup ══════════════════════════════════════════════════════

def test_a_signed_user_clears_the_unsigned_copies_it_answers(shop):
    uid = "newbie-uid-0001"
    assert _apply(shop, _new_user_event(uid)) is False   # unsigned: parked
    assert shop.parked("user") == ["unsigned_authority_event"]
    assert _apply(shop, _new_user_event(uid), shop.M) is True
    assert shop.user(uid) is not None
    assert shop.parked() == []


def test_supersede_ignores_a_parked_copy_that_is_newer_than_the_applied_state(shop):
    uid = "newbie-uid-0002"
    _apply(shop, _new_user_event(uid, row_version=5))            # unsigned v5: parked
    _apply(shop, _new_user_event(uid, row_version=1), shop.M)    # signed v1: applies
    assert shop.user(uid)["row_version"] == 1
    assert shop.parked("user") == ["unsigned_authority_event"], "a NEWER unanswered change must stay"


def test_supersede_clears_equal_and_lower_versions_alike(shop):
    uid = "newbie-uid-0003"
    for v, etype in ((1, "create"), (2, "update")):
        ev = _new_user_event(uid, row_version=v)
        ev["event_type"] = etype
        _apply(shop, ev)
    assert len(shop.parked("user")) == 2
    _apply(shop, _new_user_event(uid, row_version=2), shop.M)
    assert shop.parked() == []


@pytest.mark.parametrize("reason", FORGERY_REASONS)
def test_supersede_never_touches_a_forgery_signal(shop, reason):
    uid = "newbie-uid-0004"
    ev = _new_user_event(uid, row_version=1)
    _park(shop, "user", uid, "create", ev["payload"], reason)            # forgery signal
    _park(shop, "user", uid, "update", ev["payload"], "unsigned_authority_event")   # answered
    _apply(shop, _new_user_event(uid, row_version=1), shop.M)
    assert _parked(shop) == {("user", uid, "create"): reason}


def test_supersede_also_clears_what_was_waiting_for_the_main_computer(shop):
    uid = "newbie-uid-0005"
    _park(shop, "user", uid, "create", _new_user_event(uid)["payload"], "main_unknown")
    _apply(shop, _new_user_event(uid), shop.M)
    assert _parked(shop) == {}


def test_supersede_only_touches_the_same_entity(shop):
    mine, other = "newbie-uid-0006", "newbie-uid-0007"
    _apply(shop, _new_user_event(other))     # parked, a different person
    _apply(shop, _new_user_event(mine))
    _apply(shop, _new_user_event(mine), shop.M)
    assert _parked(shop) == {("user", other, "update"): "unsigned_authority_event"}


def test_a_parked_unsigned_permission_is_cleared_by_the_signed_restatement_of_the_same_grant(shop):
    _park(shop, "user_permission", "perm-evt-1", "update",
          {"user_uid": CASHIER, "subsystem": "retail", "access_level": "read"}, "unsigned_authority_event")
    _apply(shop, _perm_event(CASHIER, "retail", "read"), shop.M)
    assert _snapshot(shop)["perms"] == [{"uid": CASHIER, "subsystem": "retail", "access_level": "read"}]
    assert _parked(shop) == {}


def test_a_restatement_of_one_grant_does_not_clear_another_grant_of_the_same_person(shop):
    _park(shop, "user_permission", "perm-evt-2", "delete",
          {"user_uid": CASHIER, "subsystem": "books.view"}, "unsigned_authority_event")
    _apply(shop, _perm_event(CASHIER, "retail", "read"), shop.M)
    assert _parked(shop) == {("user_permission", "perm-evt-2", "delete"): "unsigned_authority_event"}


def test_permissions_are_not_cleared_when_the_receiver_holds_a_different_level(shop):
    _add_permissions(shop, [(CASHIER, "retail", "none")])
    _park(shop, "user_permission", "perm-evt-3", "update",
          {"user_uid": CASHIER, "subsystem": "retail", "access_level": "full"}, "unsigned_authority_event")
    _apply(shop, _perm_event(CASHIER, "retail", "read"), shop.M)   # no-op: insert-if-absent
    assert [p["access_level"] for p in _snapshot(shop)["perms"]] == ["none"]
    assert ("user_permission", "perm-evt-3", "update") in _parked(shop), (
        "a real disagreement (what this device holds vs what was re-announced) must stay visible")


def test_an_ordinary_signed_permission_change_does_not_clear_parked_history(shop):
    _park(shop, "user_permission", "perm-evt-4", "update",
          {"user_uid": CASHIER, "subsystem": "retail", "access_level": "read"}, "unsigned_authority_event")
    _apply(shop, _perm_event(CASHIER, "retail", "read", reannounce_flag=False), shop.A)
    assert ("user_permission", "perm-evt-4", "update") in _parked(shop)


def test_admin_device_supersede_is_by_epoch(shop):
    claim = lambda dev, epoch: {"device_id": dev, "epoch": epoch, "assigned_at_utc": "2026-01-01T00:00:00+00:00",
                                "assigned_by_user_uid": None, "assigned_by_device_id": dev}
    _park(shop, "admin_device", "adm-old", "update", claim(shop.M.dev, 1), "unsigned_authority_event")
    _park(shop, "admin_device", "adm-new", "update", claim(shop.M.dev, 7), "unsigned_authority_event")
    _park(shop, "admin_device", "adm-forged", "update", claim(shop.A.dev, 1), "takeover_main_not_silent")
    ev = {"entity_type": "admin_device", "entity_id": str(uuid.uuid4()), "event_type": "update",
          "payload": claim(shop.M.dev, 1)}
    assert _apply(shop, ev, shop.M) is True
    assert _parked(shop) == {("admin_device", "adm-new", "update"): "unsigned_authority_event",
                             ("admin_device", "adm-forged", "update"): "takeover_main_not_silent"}


def test_device_supersede_needs_both_counters_to_be_covered(shop):
    base = {"device_id": shop.A.dev, "device_label": "Till A", "platform": "WINDOWS", "status": "active",
            "row_version": 2, "admin_row_version": 1, "updated_at_utc": "2026-03-01T00:00:00+00:00"}
    _park(shop, "device", shop.A.dev, "create", dict(base, row_version=1), "unsigned_authority_event")
    _park(shop, "device", shop.A.dev, "update", dict(base, admin_row_version=4), "unsigned_authority_event")
    ev = {"entity_type": "device", "entity_id": shop.A.dev, "event_type": "update", "payload": dict(base)}
    assert _apply(shop, ev, shop.A) is True
    assert _parked(shop) == {("device", shop.A.dev, "update"): "unsigned_authority_event"}, (
        "the parked owner decision (admin_row_version 4) is newer than what applied and must stay")


def test_the_retry_pass_does_not_resurrect_a_row_a_sibling_just_cleared(shop):
    """`_retry_quarantined_events` works from a snapshot: a signed `main_unknown` row
    that applies mid-pass clears an unsigned row LATER in the snapshot, which must not
    be re-tried (re-parking is an upsert -- it would bring the row back)."""
    uid = "newbie-uid-0008"
    signed = shop.world.sign(_new_user_event(uid, row_version=2), shop.M)
    # (the event_type matters: the signature covers it, and `_new_user_event` is an "update")
    _park(shop, "user", uid, "update", signed["payload"], "main_unknown")                 # retried first
    _park(shop, "user", uid, "create", _new_user_event(uid, row_version=1)["payload"],
          "unsigned_authority_event")                                                      # later in the snapshot
    shop.pull([])    # an empty pull still runs the retry pass
    assert shop.user(uid) is not None and shop.user(uid)["row_version"] == 2
    assert _parked(shop) == {}


# ═══ run_if_due: once per device, again when it becomes main ═════════════════

class _App:
    """An app-data directory with the one file `run_if_due` reads for identity."""
    __test__ = False

    def __init__(self, tmp_path, dev):
        self.dir = tmp_path / "appdata"
        (self.dir / "device").mkdir(parents=True)
        (self.dir / "device" / "local_device.json").write_text(json.dumps({"device_uuid": dev}), encoding="utf-8")


def _outbox_count(shop):
    return shop.q("SELECT COUNT(*) AS n FROM sync_outbox")[0]["n"]


def _run(shop, app, *, signer, retail_calls=None, fail_retail=False):
    def retail_emit(is_main, dev):
        if retail_calls is not None:
            retail_calls.append((is_main, dev))
        if fail_retail:
            raise RuntimeError("retail outbox unavailable")
        return 3
    conn = shop.get_conn()
    try:
        orig = authority.local_signer_or_none
        authority.local_signer_or_none = lambda *a, **k: signer
        try:
            return reannounce.run_if_due(conn, COMPANY, app_data_dir=app.dir, retail_emit=retail_emit)
        finally:
            authority.local_signer_or_none = orig
    finally:
        conn.close()


def test_run_if_due_waits_for_a_signer_and_writes_no_marker_until_it_has_one(shop, tmp_path):
    app = _App(tmp_path, shop.M.dev)
    out = _run(shop, app, signer=None)
    assert (out["ran"], out["why"]) == (False, "no_signer")
    assert _outbox_count(shop) == 0
    assert not reannounce.marker_path(app.dir).exists(), "marker set too early: nothing was announced"
    out = _run(shop, app, signer=shop.M.signer)
    assert out["ran"] is True and out["retail_events"] == 3
    assert reannounce.read_marker(app.dir)["self_done"] is True


def test_run_if_due_runs_once_then_stays_quiet(shop, tmp_path):
    app = _App(tmp_path, shop.M.dev)
    calls = []
    assert _run(shop, app, signer=shop.M.signer, retail_calls=calls)["ran"] is True
    queued = _outbox_count(shop)
    assert queued > 0 and calls == [(True, shop.M.dev)]
    again = _run(shop, app, signer=shop.M.signer, retail_calls=calls)
    assert (again["ran"], again["why"]) == (False, "nothing_due")
    assert _outbox_count(shop) == queued and len(calls) == 1


def test_an_ordinary_computer_announces_itself_only(shop, tmp_path):
    app = _App(tmp_path, shop.A.dev)
    calls = []
    assert _run(shop, app, signer=shop.A.signer, retail_calls=calls)["ran"] is True
    rows = shop.q("SELECT entity_type, entity_id FROM sync_outbox")
    assert rows == [{"entity_type": "device", "entity_id": shop.A.dev}]
    assert calls == [(False, shop.A.dev)]


def test_becoming_main_runs_it_again_once_and_a_repeat_at_the_same_epoch_does_not(shop, tmp_path):
    app = _App(tmp_path, shop.A.dev)
    calls = []
    _run(shop, app, signer=shop.A.signer, retail_calls=calls)               # not main yet
    after_first = _outbox_count(shop)
    assert _run(shop, app, signer=shop.A.signer, retail_calls=calls)["why"] == "nothing_due"

    conn = shop.get_conn()
    try:   # A takes over the main role at epoch 2 (the registry learned it, by any route)
        install_main(conn, COMPANY, shop.A.dev, epoch=2)
    finally:
        conn.close()
    out = _run(shop, app, signer=shop.A.signer, retail_calls=calls)
    assert out["ran"] is True, "a computer that BECOMES main must re-sign the shop's state"
    assert calls[-1] == (True, shop.A.dev)
    kinds = {r["entity_type"] for r in shop.q("SELECT entity_type FROM sync_outbox")}
    assert {"admin_device", "user"} <= kinds
    assert _outbox_count(shop) > after_first
    settled = _outbox_count(shop)
    assert _run(shop, app, signer=shop.A.signer, retail_calls=calls)["why"] == "nothing_due"
    assert _outbox_count(shop) == settled

    conn = shop.get_conn()
    try:   # ...and again at the NEXT epoch it holds
        install_main(conn, COMPANY, shop.A.dev, epoch=3)
    finally:
        conn.close()
    assert _run(shop, app, signer=shop.A.signer, retail_calls=calls)["ran"] is True


def test_a_failed_retail_half_queues_nothing_and_sets_no_marker(shop, tmp_path):
    app = _App(tmp_path, shop.M.dev)
    out = _run(shop, app, signer=shop.M.signer, fail_retail=True)
    assert (out["ran"], out["why"]) == (False, "error")
    assert _outbox_count(shop) == 0, "the registry half must roll back with it"
    assert not reannounce.marker_path(app.dir).exists()
    assert _run(shop, app, signer=shop.M.signer)["ran"] is True, "and it retries on the next tick"


def test_the_marker_is_a_local_file_nothing_syncs(shop, tmp_path):
    app = _App(tmp_path, shop.M.dev)
    _run(shop, app, signer=shop.M.signer)
    path = reannounce.marker_path(app.dir)
    assert path.parent.name == "device" and path.exists()
    assert json.loads(path.read_text(encoding="utf-8"))["self_done"] is True
    assert "authority_reannounce" not in " ".join(
        r["payload"] for r in shop.q("SELECT payload FROM sync_outbox")), "the marker must never ride the wire"


# ═══ AUDIT-033 fix round (verifier V finding #1): the re-announce waits for an ATTESTED lease ═══

def test_a_version_1_marker_is_redone_once(shop, tmp_path):
    """Before the fix round a re-announce was done under a lease that attested nothing, so no
    receiver could judge it. A v1 marker therefore reads as 'never done' and the re-announce
    runs once more (idempotent: equal versions are a no-op where the row exists)."""
    app = _App(tmp_path, shop.M.dev)
    reannounce.write_marker(app.dir, {"v": 1, "self_done": True, "main_done": [f"{COMPANY}:1"]})
    assert reannounce.read_marker(app.dir) == {"v": reannounce.MARKER_VERSION, "self_done": False, "main_done": []}
    out = _run(shop, app, signer=shop.M.signer)
    assert out["ran"] is True
    again = reannounce.read_marker(app.dir)
    assert again["self_done"] is True and again["v"] == reannounce.MARKER_VERSION
    assert _run(shop, app, signer=shop.M.signer)["why"] == "nothing_due"          # a v2 marker is honoured


def test_a_current_marker_is_read_back_unchanged(tmp_path):
    reannounce.write_marker(tmp_path, {"v": reannounce.MARKER_VERSION, "self_done": True, "main_done": ["c:1"]})
    assert reannounce.read_marker(tmp_path) == {"v": reannounce.MARKER_VERSION, "self_done": True,
                                                 "main_done": ["c:1"]}


@pytest.mark.skipif(__import__("sys").platform != "win32", reason="DPAPI is Windows-only")
def test_run_if_due_waits_for_a_lease_that_attests_this_computer_then_runs_once(shop, tmp_path):
    """The REAL `local_signer_or_none` (no monkeypatching): a computer whose Owner lease names no
    registry id cannot sign, so the re-announce does nothing and writes no marker; once the
    next check-in brings a lease that attests it, it runs exactly once."""
    from commercial_runtime.licensing_contracts.device_identity import (
        WindowsDpapiDeviceIdentityProvider, fingerprint_of)
    from commercial_runtime.licensing_contracts.state_machine import LicenseState as LS
    from commercial_runtime.licensing_contracts.state_repository import (
        LICENSING_SCHEMA_VERSION, LicenseStateRecord, LicenseStateRepository)
    import base64
    from commercial_runtime.sync.tests._signing import make_lease

    app = _App(tmp_path, "dev-real-reannounce")
    provider = WindowsDpapiDeviceIdentityProvider(app.dir / "licensing")
    provider.generate_new_key()
    fp = fingerprint_of(base64.b64decode(provider.get_public_key_b64()))
    repo = LicenseStateRepository(app.dir / "database" / "subsystems" / "licensing.db")

    def store(lease):
        repo.save(LicenseStateRecord(
            licensing_schema_version=LICENSING_SCHEMA_VERSION, product_code="AURA_RETAIL", platform="WINDOWS",
            current_state=LS.ACTIVE_ONLINE.value, assertion_envelope_json=json.dumps(lease)))

    def run():
        conn = shop.get_conn()
        try:
            return reannounce.run_if_due(conn, COMPANY, app_data_dir=app.dir)
        finally:
            conn.close()

    store(make_lease(shop.world.owner_key, inst="inst-real-reannounce", fingerprint=fp))        # attests nothing
    out = run()
    assert (out["ran"], out["why"]) == (False, "no_signer")
    assert _outbox_count(shop) == 0 and not reannounce.marker_path(app.dir).exists()

    store(make_lease(shop.world.owner_key, inst="inst-real-reannounce", fingerprint=fp,
                     registry_device_id="dev-real-reannounce"))                                  # the check-in
    assert run()["ran"] is True
    assert reannounce.read_marker(app.dir)["self_done"] is True
    assert run()["why"] == "nothing_due"
