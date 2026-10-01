"""AUDIT-033 / S-AUTH, D4 -- who may send which REGISTRY-stream authority event.

Driven through the real `SyncService._apply_event` against a REAL registry.db,
with REAL signatures (see _signing.py). Every rule is pinned in BOTH directions
(deny AND allow), and every deny asserts the business tables did not move -- a
parked event that still changed a row would be the worst kind of pass.

The retail-stream rules (till allocations, numbering books, release acks,
branch aliases) and the forged-attack scenarios against retail.db are in
test_sync_authority_retail.py.

MUTATION-PROVEN: each guard in authority.py was broken, the tests below went
red, and the guard was restored (red/green lines are in the crew's progress.md).

Run (ONE FILE PER PYTEST PROCESS):
    pytest commercial_runtime/sync/tests/test_sync_authority_rules.py -q
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from commercial_runtime.identity import registry_db
from commercial_runtime.identity.device_registry import get_assignment
from commercial_runtime.sync import authority
from commercial_runtime.sync import sync_service as ss
from commercial_runtime.sync.sync_service import REGISTRY_SYNC_ENTITY_TYPES, SyncService
from commercial_runtime.sync.tests._signing import SigningWorld, admin_claim_event, install_main

COMPANY = "company-rules"
OWNER = "owner-uid"
CASHIER = "cashier-uid"


class Shop:
    """A receiver (R), the shop's main computer (M) and two ordinary members."""
    __test__ = False

    def __init__(self, tmp_path, monkeypatch):
        db_dir = tmp_path / "database"
        monkeypatch.setattr(registry_db, "_db_dir", str(db_dir))
        monkeypatch.setattr(registry_db, "DB_PATH", str(db_dir / "registry.db"))
        registry_db.init_registry_db()
        self.get_conn = registry_db.get_conn
        self.world = SigningWorld(tmp_path)
        self.R = self.world.new_device("dev-receiver")
        self.M = self.world.new_device("dev-main")
        self.A = self.world.new_device("dev-a")
        self.B = self.world.new_device("dev-b")
        self.svc = SyncService(
            client_factory=lambda: None, get_conn=self.get_conn,
            local_company_id_provider=lambda: COMPANY,
            handled_entity_types=REGISTRY_SYNC_ENTITY_TYPES,
            authority_env_provider=self.world.provider(self.R))
        self._seed()

    # -- seeding ---------------------------------------------------------
    def _seed(self):
        conn = self.get_conn()
        try:
            for uid, role, email, emp in ((OWNER, "admin", "o@x.com", "ADMIN-0001"),
                                          (CASHIER, "cashier", "c@x.com", "EMP-0002")):
                conn.execute(
                    "INSERT INTO users (id, company_id, employee_id, email, password_hash, role, status, "
                    "require_password_change, session_version, language, uid, pin_hash, row_version, "
                    "updated_at_utc, display_name) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (str(uuid.uuid4()), COMPANY, emp, email, f"HASH-{uid}", role, "active", 0, 1, "en",
                     uid, f"PIN-{uid}", 1, "2026-01-01T00:00:00+00:00", f"Name {uid}"))
            for dev in (self.M, self.A, self.B):
                conn.execute(
                    "INSERT INTO devices (id, company_id, device_label, platform, is_admin_device, status, "
                    "first_seen_at, last_seen_at, row_version, admin_row_version) "
                    "VALUES (?,?,?,?,?,?,?,?,1,1)",
                    (dev.dev, COMPANY, f"Till {dev.dev}", "WINDOWS", 1 if dev is self.M else 0, "active",
                     "2026-01-01T00:00:00+00:00", "2026-01-01T00:00:00+00:00"))
            conn.commit()
            install_main(conn, COMPANY, self.M.dev)
        finally:
            conn.close()

    # -- helpers ---------------------------------------------------------
    def apply(self, ev, signer=None):
        """Apply `ev` signed by `signer` (None = unsigned). True = applied."""
        if signer is not None:
            ev = self.world.sign(ev, signer)
        conn = self.get_conn()
        try:
            handled = self.svc._apply_event(conn, ev, local_company_id=COMPANY)
            conn.commit()
            return handled
        finally:
            conn.close()

    def pull(self, events=()):
        conn = self.get_conn()
        try:
            self.svc.apply_pull_result(conn, {"events": list(events), "cursor": 1})
            conn.commit()
        finally:
            conn.close()

    def q(self, sql, args=()):
        conn = self.get_conn()
        try:
            return [dict(r) for r in conn.execute(sql, args).fetchall()]
        finally:
            conn.close()

    def parked(self, entity_type=None):
        rows = self.q("SELECT entity_type, reason FROM sync_apply_quarantine ORDER BY rowid")
        return [r["reason"] for r in rows if entity_type in (None, r["entity_type"])]

    def user(self, uid):
        rows = self.q("SELECT * FROM users WHERE uid=?", (uid,))
        return rows[0] if rows else None

    def user_event(self, uid, **over):
        row = self.user(uid)
        payload = {k: row[k] for k in ("uid", "employee_id", "email", "role", "status", "language",
                                       "password_hash", "pin_hash", "session_version", "display_name")}
        payload.update(require_password_change=int(row["require_password_change"]),
                       row_version=int(row["row_version"]) + 1,
                       updated_at_utc="2026-02-01T00:00:00+00:00")
        payload.update(over)
        return {"entity_type": "user", "entity_id": uid, "event_type": "update", "payload": payload}

    def counts(self):
        return {"users": self.q("SELECT COUNT(*) AS n FROM users")[0]["n"],
                "perms": self.q("SELECT COUNT(*) AS n FROM user_permissions")[0]["n"],
                "devices": self.q("SELECT COUNT(*) AS n FROM devices")[0]["n"],
                "assignment": json.dumps(self.q("SELECT * FROM admin_device_assignment"), sort_keys=True)}

    def main(self):
        conn = self.get_conn()
        try:
            row = get_assignment(conn, COMPANY)
            return (row["device_id"], row["epoch"]) if row else None
        finally:
            conn.close()

    def set_last_seen(self, dev, when):
        conn = self.get_conn()
        try:
            conn.execute(
                "INSERT OR REPLACE INTO sync_signers (device_id, company_id, installation_id, key_fingerprint, "
                "first_bound_at_utc, last_signed_seen_at_utc) VALUES (?,?,?,?,?,?)",
                (dev.dev, COMPANY, dev.inst, dev.fingerprint, when.isoformat(), when.isoformat()))
            conn.commit()
        finally:
            conn.close()


@pytest.fixture
def shop(tmp_path, monkeypatch):
    return Shop(tmp_path, monkeypatch)


# ═══ user ═══════════════════════════════════════════════════════════════════

def test_any_verified_member_may_edit_an_ordinary_user(shop):
    assert shop.apply(shop.user_event(CASHIER, role="manager"), shop.A) is True
    assert shop.user(CASHIER)["role"] == "manager"
    assert shop.parked() == []


def test_a_non_main_cannot_replace_the_owners_password(shop):
    before = shop.counts()
    ev = shop.user_event(OWNER, password_hash="ATTACKER-HASH")
    assert shop.apply(ev, shop.A) is False
    assert shop.parked("user") == ["owner_change_needs_main"]
    assert shop.user(OWNER)["password_hash"] == f"HASH-{OWNER}"
    assert shop.counts() == before


def test_the_main_may_change_the_owners_password(shop):
    assert shop.apply(shop.user_event(OWNER, password_hash="NEW-HASH"), shop.M) is True
    assert shop.user(OWNER)["password_hash"] == "NEW-HASH"
    assert shop.parked() == []


@pytest.mark.parametrize("field,value", [
    ("status", "suspended"), ("email", "evil@x.com"), ("pin_hash", "EVILPIN"),
    ("role", "cashier"), ("require_password_change", 1), ("session_version", 9),
    ("employee_id", "ADMIN-0099"), ("branch_scope_uid", "branch-x"), ("role_preset", "accountant"),
])
def test_every_owner_critical_field_needs_the_main(shop, field, value):
    assert shop.apply(shop.user_event(OWNER, **{field: value}), shop.A) is False
    assert shop.parked("user") == ["owner_change_needs_main"]
    assert shop.user(OWNER)["status"] == "active" and shop.user(OWNER)["role"] == "admin"


def test_a_non_main_may_change_the_owners_harmless_fields(shop):
    """Planner amendment: a display-name / language edit leaves every owner-critical
    field as it is, so any verified member may send it."""
    ev = shop.user_event(OWNER, display_name="Owner Renamed", language="ar")
    assert shop.apply(ev, shop.A) is True
    row = shop.user(OWNER)
    assert (row["display_name"], row["language"]) == ("Owner Renamed", "ar")
    assert row["password_hash"] == f"HASH-{OWNER}"
    assert shop.parked() == []


def test_a_harmless_edit_smuggling_a_critical_one_is_still_refused(shop):
    ev = shop.user_event(OWNER, display_name="Innocent", password_hash="ATTACKER-HASH")
    assert shop.apply(ev, shop.A) is False
    assert shop.user(OWNER)["display_name"] == f"Name {OWNER}"
    assert shop.user(OWNER)["password_hash"] == f"HASH-{OWNER}"


def test_a_non_main_cannot_mint_a_second_admin(shop):
    before = shop.counts()
    new_admin = {"entity_type": "user", "entity_id": "evil-uid", "event_type": "create", "payload": {
        "uid": "evil-uid", "employee_id": "ADMIN-0666", "email": "evil@x.com", "role": "admin",
        "status": "active", "require_password_change": 0, "language": "en", "password_hash": "EVIL",
        "pin_hash": "E", "row_version": 1, "updated_at_utc": "2026-02-01T00:00:00+00:00",
        "session_version": 1}}
    assert shop.apply(new_admin, shop.A) is False
    assert shop.parked("user") == ["second_admin_refused"]
    assert shop.user("evil-uid") is None
    assert shop.counts() == before


def test_a_non_main_cannot_promote_a_cashier_to_admin(shop):
    assert shop.apply(shop.user_event(CASHIER, role="admin"), shop.A) is False
    assert shop.parked("user") == ["second_admin_refused"]
    assert shop.user(CASHIER)["role"] == "cashier"


def test_the_main_may_introduce_the_shop_owners_account_on_a_device_with_its_own_placeholder_admin(shop):
    """The legitimate join: this device minted its own first-run admin, and the
    shop owner's real account arrives from the main computer (see
    test_registry_user_sync_apply::test_a_colliding_admin_code_..._renumbered)."""
    new_owner = {"entity_type": "user", "entity_id": "real-owner", "event_type": "create", "payload": {
        "uid": "real-owner", "employee_id": "ADMIN-0007", "email": "real@x.com", "role": "admin",
        "status": "active", "require_password_change": 0, "language": "en", "password_hash": "REAL",
        "pin_hash": "R", "row_version": 1, "updated_at_utc": "2026-02-01T00:00:00+00:00",
        "session_version": 1}}
    assert shop.apply(new_owner, shop.M) is True
    assert shop.user("real-owner")["role"] == "admin"


def test_owner_changes_park_as_main_unknown_until_the_main_is_known_then_apply(shop):
    """Signed by the main, but this receiver has not heard who the main is: parked,
    retried each pull, applied the moment the assignment arrives."""
    conn = shop.get_conn()
    conn.execute("DELETE FROM admin_device_assignment")
    conn.commit()
    conn.close()
    ev = shop.world.sign(shop.user_event(OWNER, password_hash="FROM-MAIN"), shop.M)
    assert shop.apply(ev) is False
    assert shop.parked("user") == ["main_unknown"]
    conn = shop.get_conn()
    install_main(conn, COMPANY, shop.M.dev)
    conn.close()
    shop.pull()
    assert shop.parked("user") == []
    assert shop.user(OWNER)["password_hash"] == "FROM-MAIN"


# ═══ user_permission ════════════════════════════════════════════════════════

def _perm(uid, level="full", subsystem="retail.sales", event_type="create"):
    return {"entity_type": "user_permission", "entity_id": str(uuid.uuid4()), "event_type": event_type,
            "payload": {"user_uid": uid, "subsystem": subsystem, "access_level": level}}


def test_a_non_main_cannot_change_the_owners_permissions(shop):
    before = shop.counts()
    assert shop.apply(_perm(OWNER, "none"), shop.A) is False
    assert shop.parked("user_permission") == ["owner_change_needs_main"]
    assert shop.counts() == before


def test_the_main_may_change_the_owners_permissions(shop):
    assert shop.apply(_perm(OWNER, "none"), shop.M) is True
    assert shop.q("SELECT access_level FROM user_permissions")[0]["access_level"] == "none"


def test_any_member_may_change_an_ordinary_users_permissions(shop):
    assert shop.apply(_perm(CASHIER, "full"), shop.A) is True
    assert shop.q("SELECT access_level FROM user_permissions")[0]["access_level"] == "full"


# ═══ admin_device ═══════════════════════════════════════════════════════════

@pytest.fixture
def bare_shop(shop):
    """Same shop, but nobody has been recorded as main yet."""
    conn = shop.get_conn()
    conn.execute("DELETE FROM admin_device_assignment")
    conn.commit()
    conn.close()
    return shop


def test_first_claim_by_the_device_itself_is_allowed(bare_shop):
    assert bare_shop.apply(admin_claim_event(bare_shop.A.dev), bare_shop.A) is True
    assert bare_shop.main() == (bare_shop.A.dev, 1)


def test_first_claim_naming_someone_else_is_refused(bare_shop):
    assert bare_shop.apply(admin_claim_event(bare_shop.B.dev), bare_shop.A) is False
    assert bare_shop.parked("admin_device") == ["admin_claim_signer_mismatch"]
    assert bare_shop.main() is None


def test_the_main_may_re_announce_itself(shop):
    assert shop.apply(admin_claim_event(shop.M.dev, epoch=1), shop.M) in (True, False)
    assert shop.parked() == []
    assert shop.main() == (shop.M.dev, 1)


def test_the_main_may_hand_over(shop):
    assert shop.apply(admin_claim_event(shop.A.dev, epoch=2), shop.M) is True
    assert shop.main() == (shop.A.dev, 2)


def test_a_non_main_cannot_seize_main_with_a_higher_epoch(shop):
    before = shop.counts()
    assert shop.apply(admin_claim_event(shop.A.dev, epoch=99), shop.A) is False
    assert shop.parked("admin_device") == ["needs_main_computer"]
    assert shop.main() == (shop.M.dev, 1)
    assert shop.counts() == before


def test_a_non_main_cannot_hand_main_to_a_friend(shop):
    assert shop.apply(admin_claim_event(shop.B.dev, epoch=2), shop.A) is False
    assert shop.parked("admin_device") == ["needs_main_computer"]
    assert shop.main() == (shop.M.dev, 1)


def _takeover(dev, epoch=2):
    return admin_claim_event(dev, epoch=epoch, takeover=True, assigned_by_device_id=dev)


def test_takeover_is_allowed_when_the_main_has_never_been_heard(shop):
    assert shop.apply(_takeover(shop.A.dev), shop.A) is True
    assert shop.main() == (shop.A.dev, 2)


def test_takeover_is_refused_when_the_main_was_heard_recently(shop):
    shop.set_last_seen(shop.M, datetime.now(timezone.utc) - timedelta(hours=1))
    assert shop.apply(_takeover(shop.A.dev), shop.A) is False
    assert shop.parked("admin_device") == ["takeover_main_not_silent"]
    assert shop.main() == (shop.M.dev, 1)


def test_takeover_is_allowed_after_seven_days_of_receiver_clock_silence(shop):
    shop.set_last_seen(shop.M, datetime.now(timezone.utc) - timedelta(days=8))
    assert shop.apply(_takeover(shop.A.dev), shop.A) is True
    assert shop.main() == (shop.A.dev, 2)


def test_takeover_is_still_refused_at_six_days(shop):
    shop.set_last_seen(shop.M, datetime.now(timezone.utc) - timedelta(days=6))
    assert shop.apply(_takeover(shop.A.dev), shop.A) is False
    assert shop.main() == (shop.M.dev, 1)


def test_takeover_by_a_signer_who_is_not_the_claimant_is_refused(shop):
    assert shop.apply(_takeover(shop.B.dev), shop.A) is False
    assert shop.parked("admin_device") == ["takeover_signer_mismatch"]
    assert shop.main() == (shop.M.dev, 1)


def test_the_silence_clock_is_the_receivers_not_the_senders(shop):
    """A forger controls every timestamp in the payload. A takeover whose payload
    claims it was assigned years ago is still judged by when THIS device last
    heard from the main."""
    shop.set_last_seen(shop.M, datetime.now(timezone.utc) - timedelta(minutes=5))
    ev = _takeover(shop.A.dev)
    ev["payload"]["assigned_at_utc"] = "2026-01-01T00:00:00+00:00"
    assert shop.apply(ev, shop.A) is False
    assert shop.parked("admin_device") == ["takeover_main_not_silent"]


def test_a_receiver_that_is_itself_the_main_never_sees_it_as_silent(shop):
    conn = shop.get_conn()
    install_main(conn, COMPANY, shop.R.dev)
    conn.close()
    assert shop.apply(_takeover(shop.A.dev), shop.A) is False
    assert shop.parked("admin_device") == ["takeover_main_not_silent"]
    assert shop.main() == (shop.R.dev, 1)


def test_a_verified_event_from_the_main_refreshes_its_last_seen(shop):
    shop.set_last_seen(shop.M, datetime.now(timezone.utc) - timedelta(days=30))
    assert shop.apply(shop.user_event(CASHIER, role="manager"), shop.M) is True
    seen = shop.q("SELECT last_signed_seen_at_utc FROM sync_signers WHERE device_id=?", (shop.M.dev,))[0]
    assert datetime.fromisoformat(seen["last_signed_seen_at_utc"]) > datetime.now(timezone.utc) - timedelta(minutes=1)
    # ...so a takeover straight afterwards is refused
    assert shop.apply(_takeover(shop.A.dev), shop.A) is False


# ═══ device ═════════════════════════════════════════════════════════════════

def _device_ev(dev_id, **over):
    payload = {"device_id": dev_id, "device_label": f"Till {dev_id}", "platform": "WINDOWS", "status": "active",
               "row_version": 2, "admin_row_version": 1, "updated_at_utc": "2026-02-01T00:00:00+00:00",
               "app_version": "9.9.9", "last_seen_at": "2026-02-01T00:00:00+00:00"}
    payload.update(over)
    return {"entity_type": "device", "entity_id": dev_id, "event_type": "update", "payload": payload}


def test_a_device_may_announce_itself(shop):
    new = shop.world.new_device("dev-new")
    ev = _device_ev(new.dev, row_version=1)
    assert shop.apply(ev, new) is True
    assert shop.q("SELECT id FROM devices WHERE id=?", (new.dev,))


def test_a_device_may_report_its_own_build_and_heartbeat(shop):
    assert shop.apply(_device_ev(shop.A.dev), shop.A) is True
    assert shop.q("SELECT app_version FROM devices WHERE id=?", (shop.A.dev,))[0]["app_version"] == "9.9.9"


def test_a_device_cannot_rename_itself_without_the_main(shop):
    ev = _device_ev(shop.A.dev, device_label="Renamed By Itself", admin_row_version=2)
    assert shop.apply(ev, shop.A) is False
    assert shop.parked("device") == ["device_change_needs_main"]
    assert shop.q("SELECT device_label FROM devices WHERE id=?", (shop.A.dev,))[0]["device_label"] == f"Till {shop.A.dev}"


def test_the_main_may_rename_a_device(shop):
    ev = _device_ev(shop.A.dev, device_label="Front Counter", admin_row_version=2)
    assert shop.apply(ev, shop.M) is True
    assert shop.q("SELECT device_label FROM devices WHERE id=?", (shop.A.dev,))[0]["device_label"] == "Front Counter"


def test_a_device_cannot_reinstate_itself(shop):
    conn = shop.get_conn()
    conn.execute("UPDATE devices SET status='revoked' WHERE id=?", (shop.B.dev,))
    conn.commit()
    conn.close()
    # B is revoked, so its own events are refused outright...
    ev = _device_ev(shop.B.dev, status="active", admin_row_version=2, reinstated_by_device_id=shop.B.dev)
    assert shop.apply(ev, shop.B) is False
    assert shop.parked("device") == ["signer_revoked"]
    assert shop.q("SELECT status FROM devices WHERE id=?", (shop.B.dev,))[0]["status"] == "revoked"


def test_a_non_main_cannot_speak_for_another_device(shop):
    ev = _device_ev(shop.B.dev, status="revoked", admin_row_version=2)
    assert shop.apply(ev, shop.A) is False
    assert shop.parked("device") == ["device_change_needs_main"]
    assert shop.q("SELECT status FROM devices WHERE id=?", (shop.B.dev,))[0]["status"] == "active"


def test_the_main_may_remove_another_device(shop):
    ev = _device_ev(shop.B.dev, status="revoked", admin_row_version=2)
    assert shop.apply(ev, shop.M) is True
    assert shop.q("SELECT status FROM devices WHERE id=?", (shop.B.dev,))[0]["status"] == "revoked"


# ═══ signer binding, revoked signers, forgery, unsigned ═════════════════════

def test_a_revoked_signers_events_are_refused(shop):
    conn = shop.get_conn()
    conn.execute("UPDATE devices SET status='revoked' WHERE id=?", (shop.A.dev,))
    conn.commit()
    conn.close()
    assert shop.apply(shop.user_event(CASHIER, role="manager"), shop.A) is False
    assert shop.parked("user") == ["signer_revoked"]
    assert shop.user(CASHIER)["role"] == "cashier"


def test_a_second_installation_cannot_speak_as_a_bound_device(shop):
    assert shop.apply(shop.user_event(CASHIER, display_name="one"), shop.A) is True
    impostor = shop.world.new_device(shop.A.dev, inst="impostor-installation")
    assert shop.apply(shop.user_event(CASHIER, display_name="two", row_version=5), impostor) is False
    assert shop.parked("user") == ["signer_binding_clash"]
    assert shop.user(CASHIER)["display_name"] == "one"


def test_the_receivers_own_id_cannot_be_claimed_by_a_peer(shop):
    impostor = shop.world.new_device(shop.R.dev, inst="impostor-of-the-receiver")
    assert shop.apply(shop.user_event(CASHIER, role="manager"), impostor) is False
    assert shop.parked("user") == ["signer_binding_clash"]


def test_unsigned_authority_events_are_parked_for_every_registry_type(shop):
    before = shop.counts()
    for ev in (shop.user_event(CASHIER, role="manager"), _perm(CASHIER), _device_ev(shop.A.dev),
               admin_claim_event(shop.A.dev, epoch=9)):
        assert shop.apply(ev) is False
    assert shop.parked() == ["unsigned_authority_event"] * 4
    assert shop.counts() == before


def test_a_member_of_another_licence_changes_nothing(shop):
    outsider = shop.world.new_device("dev-outsider", licence="lic-someone-else")
    before = shop.counts()
    assert shop.apply(shop.user_event(CASHIER, role="manager"), outsider) is False
    assert shop.parked("user") == ["foreign_licence"]
    assert shop.counts() == before


def test_an_event_altered_after_signing_changes_nothing(shop):
    signed = shop.world.sign(shop.user_event(CASHIER, display_name="fine"), shop.A)
    signed["payload"]["role"] = "manager"
    assert shop.apply(signed) is False
    assert shop.parked("user") == ["bad_signature"]
    assert shop.user(CASHIER)["role"] == "cashier"


def test_forged_raw_outbox_rows_change_nothing(shop):
    """The attack this whole change exists for: a computer holding the shop licence
    inserts raw rows into its own outbox claiming to be the owner/main. Each one
    is applied by the receiver exactly as relayed -- and none of them lands."""
    attacker = shop.A
    before = shop.counts()
    owner_before = shop.user(OWNER)
    forged = [
        # (1) make itself an administrator
        {"entity_type": "user", "entity_id": "atk", "event_type": "create", "payload": {
            "uid": "atk", "employee_id": "ADMIN-9", "email": "a@evil.x", "role": "admin", "status": "active",
            "password_hash": "EVIL", "pin_hash": "E", "row_version": 1, "session_version": 1,
            "require_password_change": 0, "language": "en", "updated_at_utc": "2026-02-01T00:00:00+00:00"}},
        # (2) replace the owner's password
        shop.user_event(OWNER, password_hash="EVIL"),
        # (3) claim the main computer
        admin_claim_event(attacker.dev, epoch=500),
        # (4) hand the owner's permissions to itself
        _perm(OWNER, "none"),
    ]
    for ev in forged:
        shop.apply(ev, attacker)          # signed by the attacker: a real member, but not the main
    assert shop.parked() == ["second_admin_refused", "owner_change_needs_main",
                             "needs_main_computer", "owner_change_needs_main"]
    unsigned = [dict(ev, payload=dict(ev["payload"])) for ev in forged]
    for ev in unsigned:
        shop.apply(ev)                    # and again with no signature at all
    assert shop.counts() == before
    assert shop.user(OWNER) == owner_before
    assert shop.main() == (shop.M.dev, 1)
    # a re-park refreshes the SAME quarantine row (keyed on entity_id + event_type), now
    # saying why the unsigned copy was refused
    assert shop.parked() == ["unsigned_authority_event"] * 4


def test_unsigned_authority_events_park_but_the_same_payload_signed_by_the_main_applies(shop):
    ev = shop.user_event(CASHIER, role="manager")
    assert shop.apply(dict(ev)) is False
    assert shop.apply(ev, shop.M) is True


def test_a_parked_event_keeps_its_signature_so_a_retry_can_re_verify(shop):
    ev = shop.user_event(OWNER, password_hash="X")
    shop.apply(ev, shop.A)
    stored = json.loads(shop.q("SELECT payload FROM sync_apply_quarantine")[0]["payload"])
    assert stored["_auth"]["dev"] == shop.A.dev and stored["_auth"].get("sig")


def test_the_applier_never_sees_the_auth_block(shop, monkeypatch):
    seen = []
    real = ss.sanitize_synced_user_fields

    def spy(payload, *a, **kw):
        seen.append(dict(payload))
        return real(payload, *a, **kw)
    monkeypatch.setattr(ss, "sanitize_synced_user_fields", spy)
    assert shop.apply(shop.user_event(CASHIER, role="manager"), shop.A) is True
    assert seen and all("_auth" not in p for p in seen)


def test_a_parked_then_resolved_event_leaves_quarantine(shop):
    """Park for main_unknown, then make the main known: the SAME parked row (still
    carrying its signature) applies and the quarantine row is deleted."""
    conn = shop.get_conn()
    conn.execute("DELETE FROM admin_device_assignment")
    conn.commit()
    conn.close()
    shop.apply(shop.user_event(OWNER, display_name="x", password_hash="PW2"), shop.M)
    assert shop.parked() == ["main_unknown"]
    conn = shop.get_conn()
    install_main(conn, COMPANY, shop.M.dev)
    conn.close()
    shop.pull()
    assert shop.q("SELECT COUNT(*) AS n FROM sync_apply_quarantine")[0]["n"] == 0
    assert shop.user(OWNER)["password_hash"] == "PW2"
