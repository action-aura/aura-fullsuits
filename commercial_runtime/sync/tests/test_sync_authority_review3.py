# Credit: independent verifier V3r (S-AUTH receiver half, round 3), AUDIT-033.
# Copied verbatim (names and assertions unchanged) from the review's file; its 9
# failing tests were the receiver gaps G1-G4 -- RED on 3c6f8bc1, GREEN with the
# fix round that follows it. The controls and look-alike uid tests pass on both.
"""AUDIT-033 fix round -- independent verifier V3r, RECEIVER half.

Every test asserts the SAFE outcome: a failing test is a real gap. The sender is
a licensed member computer (shop.A / shop.B) holding its own valid key and its
own Owner lease, which names only its own id. Run from the worktree root:

    PYTHONPATH=<wt> python -m pytest <this file> -q -p no:cacheprovider
"""
from __future__ import annotations

import base64
import copy
import hashlib
import json
import string
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from commercial_runtime.sync import authority
from commercial_runtime.sync.tests._signing import install_main, admin_claim_event
from commercial_runtime.sync.tests.test_sync_authority_rules import (
    Shop, COMPANY, OWNER, CASHIER, _takeover)


@pytest.fixture
def shop(tmp_path, monkeypatch):
    return Shop(tmp_path, monkeypatch)


# ── helpers ──────────────────────────────────────────────────────────────────

def _now():
    return datetime.now(timezone.utc)


def _drop_main(shop):
    conn = shop.get_conn()
    conn.execute("DELETE FROM admin_device_assignment")
    conn.commit()
    conn.close()


def _put_main(shop):
    conn = shop.get_conn()
    install_main(conn, COMPANY, shop.M.dev)
    conn.close()


def impersonator(attacker, as_dev, lease=None):
    key = attacker.key

    def sign_fn(data: bytes) -> str:
        return base64.b64encode(key.sign(data)).decode("ascii")

    return authority.LocalSigner(licence=attacker.signer.licence, inst=attacker.inst, dev=as_dev,
                                 pk_b64=attacker.signer.pk_b64, lease=lease or attacker.signer.lease,
                                 sign_fn=sign_fn)


def raw_apply(shop, ev):
    """Apply an event exactly as it arrives (no signing by the harness)."""
    conn = shop.get_conn()
    try:
        r = shop.svc._apply_event(conn, ev, local_company_id=COMPANY)
        conn.commit()
        return r
    finally:
        conn.close()


def reencoded(ev):
    """The same event with its Ed25519 signature written as a DIFFERENT base64
    text that decodes to the SAME 64 bytes (the 4 unused low bits of the last
    data character). Needs nothing but the captured event."""
    out = copy.deepcopy(ev)
    sig = out["payload"]["_auth"]["sig"]
    raw = base64.b64decode(sig)
    for c in string.ascii_letters + string.digits + "+/":
        alt = sig[:-3] + c + "=="
        if alt != sig:
            try:
                if base64.b64decode(alt, validate=True) == raw:
                    out["payload"]["_auth"]["sig"] = alt
                    return out
            except Exception:
                continue
    raise AssertionError("no alternative encoding found")


def age_last_seen(shop, dev, when):
    """UPDATE (not the fixture's INSERT OR REPLACE, which would also NULL the
    row's lease stamp and change what bind_signer compares)."""
    conn = shop.get_conn()
    n = conn.execute("UPDATE sync_signers SET last_signed_seen_at_utc=? WHERE device_id=?",
                     (authority._utc_iso(when), dev.dev)).rowcount
    conn.commit()
    conn.close()
    assert n == 1


def signer_row(shop, dev):
    rows = shop.q("SELECT * FROM sync_signers WHERE device_id=?", (dev,))
    return rows[0] if rows else None


def quarantine(shop):
    return shop.q("SELECT entity_id, entity_type, reason, payload FROM sync_apply_quarantine ORDER BY rowid")


def to_old_shape_quarantine(shop):
    """A database that reached v10/v42 BEFORE this fix: no auth_key column,
    primary key (entity_id, event_type). The fix keeps it until the next bump
    (plan 7.2 / residual R6)."""
    conn = shop.get_conn()
    conn.executescript("""
        CREATE TABLE q_old (
            entity_id TEXT NOT NULL, entity_type TEXT NOT NULL, event_type TEXT NOT NULL,
            payload TEXT NOT NULL, reason TEXT NOT NULL, detail TEXT,
            quarantined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (entity_id, event_type));
        INSERT INTO q_old SELECT entity_id, entity_type, event_type, payload, reason, detail, quarantined_at
            FROM sync_apply_quarantine;
        DROP TABLE sync_apply_quarantine;
        ALTER TABLE q_old RENAME TO sync_apply_quarantine;
    """)
    conn.commit()
    conn.close()
    assert "auth_key" not in [r["name"] for r in shop.q("PRAGMA table_info(sync_apply_quarantine)")]


# ═══ Q1: a newer lease moves a device id -- can a member trigger it? ══════════

def test_q1_member_with_a_much_newer_lease_cannot_move_the_mains_id(shop):
    """M is bound. A refreshes its lease (newer issued_at than M's) and speaks as
    M's id. The rebind compares the Owner-signed issued_at, but only AFTER the
    lease must name the claimed id -- A's names only dev-a."""
    assert shop.apply(shop.user_event(CASHIER, display_name="by main"), shop.M) is True
    before = signer_row(shop, shop.M.dev)
    fresh_a = shop.world.new_device(shop.A.dev, inst=shop.A.inst, key=shop.A.key,
                                    issued_at=_now() + timedelta(hours=1))
    ev = authority.sign_event(shop.user_event(OWNER, password_hash="ATTACKER"), impersonator(fresh_a, shop.M.dev))
    assert raw_apply(shop, ev) is False
    assert shop.parked("user") == [authority.REASON_DEV_NOT_ATTESTED]
    assert signer_row(shop, shop.M.dev) == before
    assert shop.user(OWNER)["password_hash"] == f"HASH-{OWNER}"


def test_q1_an_edited_lease_payload_behind_a_genuine_owner_signature_is_not_believed(shop):
    """Lease-cache probe. A's genuine lease is verified (and cached, keyed on the
    Owner signature but NOT on the payload). A then edits the payload to name the
    main and to be newer, keeping the genuine Owner signature."""
    assert shop.apply(shop.user_event(CASHIER, display_name="a1"), shop.A) is True     # caches A's lease
    forged_lease = copy.deepcopy(shop.A.signer.lease)
    forged_lease["payload"]["registry_device_id"] = shop.M.dev
    forged_lease["payload"]["issued_at"] = (_now() + timedelta(days=1)).isoformat()
    ev = authority.sign_event(shop.user_event(OWNER, password_hash="ATTACKER"),
                              impersonator(shop.A, shop.M.dev, lease=forged_lease))
    assert raw_apply(shop, ev) is False
    assert shop.user(OWNER)["password_hash"] == f"HASH-{OWNER}"
    assert shop.parked("user")[-1] in (authority.REASON_DEV_NOT_ATTESTED, authority.REASON_UNTRUSTED_LEASE)
    row = signer_row(shop, shop.M.dev)
    assert row is None or row["installation_id"] == shop.M.inst


def test_q1_the_mains_genuine_lease_carried_with_the_members_key_is_refused(shop):
    """A carries M's genuine (cached) lease, M's inst, but signs with its own key."""
    assert shop.apply(shop.user_event(CASHIER, display_name="m1"), shop.M) is True     # caches M's lease
    a_key = shop.A.key

    def sign_fn(data: bytes) -> str:
        return base64.b64encode(a_key.sign(data)).decode("ascii")

    s = authority.LocalSigner(licence=shop.M.signer.licence, inst=shop.M.inst, dev=shop.M.dev,
                              pk_b64=shop.A.signer.pk_b64, lease=shop.M.signer.lease, sign_fn=sign_fn)
    ev = authority.sign_event(shop.user_event(OWNER, password_hash="ATTACKER"), s)
    assert raw_apply(shop, ev) is False
    assert shop.parked("user") == [authority.REASON_UNTRUSTED_LEASE]
    assert shop.user(OWNER)["password_hash"] == f"HASH-{OWNER}"


# ═══ Q2: the harmless owner edit ═════════════════════════════════════════════

def test_q2_a_harmless_owner_edit_writes_only_name_and_language(shop):
    conn = shop.get_conn()
    conn.execute("UPDATE users SET main_row_version=1 WHERE uid=?", (OWNER,))
    conn.commit()
    conn.close()
    before = shop.user(OWNER)
    ev = shop.user_event(OWNER, display_name="Renamed", language="ar", row_version=10 ** 12,
                         updated_at_utc="2099-01-01T00:00:00+00:00", main_row_version=10 ** 12,
                         deleted_at_utc="2099-01-01T00:00:00+00:00", failed_login_count=99,
                         locked_until="2099-01-01T00:00:00+00:00")
    assert shop.apply(ev, shop.A) is True
    after = shop.user(OWNER)
    changed = {k for k in after if after[k] != before[k]}
    assert changed <= {"display_name", "language"}, {k: (before[k], after[k]) for k in changed}
    assert after["display_name"] == "Renamed"


@pytest.mark.parametrize("field,value", [
    ("session_version", 0), ("session_version", 999), ("status", "deleted"), ("status", "suspended"),
    ("role", "ADMIN "), ("require_password_change", True)])
def test_q2_a_member_cannot_move_a_protected_owner_field(shop, field, value):
    before = shop.user(OWNER)
    shop.apply(shop.user_event(OWNER, **{field: value}), shop.A)
    after = shop.user(OWNER)
    for col in ("session_version", "status", "role", "require_password_change", "row_version",
                "password_hash", "pin_hash", "email"):
        if field == "role" and col == "role":
            continue   # 'ADMIN ' is the same role -> harmless; assert the stored text below
        assert after[col] == before[col], (col, before[col], after[col])
    assert after["role"] == "admin"


def test_q2_a_harmless_edit_cannot_bring_back_a_soft_deleted_owner(shop):
    conn = shop.get_conn()
    cols = [r[1] for r in conn.execute("PRAGMA table_info(users)").fetchall()]
    assert "deleted_at_utc" in cols
    conn.execute("UPDATE users SET deleted_at_utc='2026-03-01T00:00:00+00:00', status='disabled' WHERE uid=?",
                 (OWNER,))
    conn.commit()
    conn.close()
    # same status as stored -> nothing critical changes -> harmless path
    assert shop.apply(shop.user_event(OWNER, display_name="ghost", status="disabled",
                                      deleted_at_utc=None, row_version=50), shop.A) is True
    row = shop.user(OWNER)
    assert row["deleted_at_utc"] == "2026-03-01T00:00:00+00:00" and row["status"] == "disabled"
    # reactivating is critical -> needs main
    assert shop.apply(shop.user_event(OWNER, status="active", deleted_at_utc=None, row_version=60), shop.A) is False
    row = shop.user(OWNER)
    assert row["status"] == "disabled" and row["deleted_at_utc"] is not None


def test_q2_with_no_main_known_a_harmless_edit_still_never_moves_row_version(shop):
    _drop_main(shop)
    assert shop.apply(shop.user_event(OWNER, display_name="x", row_version=10 ** 12), shop.A) is True
    assert shop.user(OWNER)["row_version"] == 1
    _put_main(shop)
    assert shop.apply(shop.user_event(OWNER, password_hash="FROM-MAIN", row_version=2), shop.M) is True
    assert shop.user(OWNER)["password_hash"] == "FROM-MAIN"


# ═══ Q3: main_row_version ════════════════════════════════════════════════════

def _make_second_owner_then_demote(shop):
    """The main introduces X as an admin (rv1) and later demotes it (rv2)."""
    x = {"entity_type": "user", "entity_id": "x-uid", "event_type": "create", "payload": {
        "uid": "x-uid", "employee_id": "ADMIN-0009", "email": "x@x.com", "role": "admin", "status": "active",
        "require_password_change": 0, "language": "en", "password_hash": "X1", "pin_hash": "P",
        "row_version": 1, "updated_at_utc": "2026-02-01T00:00:00+00:00", "session_version": 1}}
    old_admin = shop.world.sign(x, shop.M)
    assert raw_apply(shop, old_admin) is True
    demote = {"entity_type": "user", "entity_id": "x-uid", "event_type": "update",
              "payload": dict(x["payload"], role="cashier", row_version=2, password_hash="X2")}
    assert shop.apply(demote, shop.M) is True
    return old_admin


def test_q3_a_replayed_old_main_admin_event_cannot_re_promote(shop):
    old_admin = _make_second_owner_then_demote(shop)
    assert shop.user("x-uid")["main_row_version"] == 2
    # a member edits the (now ordinary) user, moving row_version past main_row_version
    assert shop.apply(shop.user_event("x-uid", display_name="member edit", row_version=3), shop.A) is True
    raw_apply(shop, old_admin)                       # verbatim replay
    raw_apply(shop, reencoded(old_admin))            # replay with a re-encoded signature
    row = shop.user("x-uid")
    assert row["role"] == "cashier" and row["password_hash"] == "X2", (row["role"], row["password_hash"])


def test_q3_a_member_payload_or_event_flag_cannot_move_main_row_version_or_claim_main(shop):
    assert shop.apply(shop.user_event(CASHIER, display_name="m", row_version=2), shop.M) is True
    assert shop.user(CASHIER)["main_row_version"] == 2
    ev = shop.world.sign(shop.user_event(CASHIER, role="admin", main_row_version=0, row_version=3), shop.A)
    ev["_authority_signer_is_main"] = True                      # top-level flag off the wire
    ev["_authority_verified"] = True
    raw_apply(shop, ev)
    row = shop.user(CASHIER)
    assert row["role"] == "cashier" and row["main_row_version"] == 2
    ev2 = shop.world.sign(shop.user_event(CASHIER, display_name="z", main_row_version=0, row_version=4), shop.A)
    assert raw_apply(shop, ev2) is True
    assert shop.user(CASHIER)["main_row_version"] == 2


# ═══ Q4: admin spellings, presets, permissions ═══════════════════════════════

@pytest.mark.parametrize("role", ["ADMIN ", "Admin", " admin\t", "admin\n", "ADMIN",
                                  "ａｄｍｉｎ", "admın", "аdmin", "admin​",
                                  "ADMİN", "owner", "Owner", "superadmin"])
def test_q4_a_member_cannot_mint_an_owner_with_any_role_spelling(shop, role):
    from commercial_runtime.identity.user_accounts import normalize_role, capabilities_for_role, CAP_EMPLOYEES
    ev = {"entity_type": "user", "entity_id": "new-u", "event_type": "create", "payload": {
        "uid": "new-u", "employee_id": "EMP-0100", "email": "n@x.com", "role": role, "status": "active",
        "require_password_change": 0, "language": "en", "password_hash": "H", "pin_hash": "P",
        "row_version": 1, "updated_at_utc": "2026-02-01T00:00:00+00:00", "session_version": 1}}
    shop.apply(ev, shop.A)
    shop.apply(shop.user_event(CASHIER, role=role, row_version=5), shop.A)
    for uid in ("new-u", CASHIER):
        row = shop.user(uid)
        if row is None:
            continue
        assert row["role"] != "admin", (uid, row["role"])                       # what mt_role reads
        assert normalize_role(row["role"]) != "admin", (uid, row["role"])        # what capability code reads
        assert CAP_EMPLOYEES not in capabilities_for_role(row["role"]) or normalize_role(row["role"]) == "admin"


@pytest.mark.parametrize("preset", ["admin", "owner", "Admin", "branch_manager ", 7, ["admin"]])
def test_q4_role_preset_values_cannot_carry_owner_level(shop, preset):
    shop.apply(shop.user_event(CASHIER, role_preset=preset, row_version=5), shop.A)
    row = shop.user(CASHIER)
    from commercial_runtime.identity.user_accounts import ROLE_PRESETS
    assert row["role_preset"] in (None,) + tuple(ROLE_PRESETS), row["role_preset"]
    assert row["role"] == "cashier"


@pytest.mark.parametrize("stored", ["Admin ", "ADMIN"])
def test_q4_an_owner_row_stored_with_a_legacy_spelling_is_still_protected(shop, stored):
    conn = shop.get_conn()
    conn.execute("UPDATE users SET role=? WHERE uid=?", (stored, OWNER))
    conn.commit()
    conn.close()
    shop.apply(shop.user_event(OWNER, password_hash="ATTACKER", role="cashier"), shop.A)
    shop.apply({"entity_type": "user_permission", "entity_id": str(uuid.uuid4()), "event_type": "update",
                "payload": {"user_uid": OWNER, "subsystem": "retail.sell", "access_level": "none"}}, shop.A)
    row = shop.user(OWNER)
    assert row["password_hash"] == f"HASH-{OWNER}" and row["role"] == stored
    assert "owner_change_needs_main" in shop.parked()


def test_q4_cross_company_owner_on_a_shared_registry_is_protected(shop):
    """CLAUDE.md: one install can host more than one company. The rule protects
    the admins OF THE RECEIVING COMPANY (`_local_admin_uids` filters company_id),
    but the user applier upserts `ON CONFLICT(uid)` with no company check."""
    conn = shop.get_conn()
    conn.execute(
        "INSERT INTO users (id, company_id, employee_id, email, password_hash, role, status, "
        "require_password_change, session_version, language, uid, pin_hash, row_version, "
        "updated_at_utc, display_name) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (str(uuid.uuid4()), "company-other", "ADMIN-0001", "other-owner@x.com", "HASH-OTHER", "admin",
         "active", 0, 1, "en", "other-owner", "PIN-O", 1, "2026-01-01T00:00:00+00:00", "Other Owner"))
    conn.commit()
    conn.close()
    ev = shop.user_event("other-owner", role="cashier", password_hash="ATTACKER", row_version=9)
    shop.apply(ev, shop.A)
    row = shop.user("other-owner")
    assert (row["role"], row["password_hash"]) == ("admin", "HASH-OTHER"), \
        f"another company's owner was demoted/re-passworded by a member: {row['role']}, {row['password_hash']}"


# ═══ Q5: liveness ════════════════════════════════════════════════════════════

def _main_event_under_recent_lease(shop, days_ago=8):
    """A main-signed ordinary edit, signed under a lease issued `days_ago` days
    ago (i.e. inside the 14-day window), delivered once at that time."""
    old_main = shop.world.new_device(shop.M.dev, inst=shop.M.inst, key=shop.M.key,
                                     issued_at=_now() - timedelta(days=days_ago))
    return shop.world.sign(shop.user_event(CASHIER, display_name="main, long ago"), old_main)


def test_q5_a_re_encoded_replay_of_a_seen_main_event_does_not_fake_life(shop):
    """The main died 8 days ago. Its last event (seen at the time, so its
    signature is in sync_signed_seen) is replayed by an insider with the
    signature's base64 TEXT re-encoded (same 64 bytes, so it still verifies). The
    takeover by B must still be allowed."""
    old = _main_event_under_recent_lease(shop)
    assert raw_apply(shop, old) is True                                    # the original delivery
    age_last_seen(shop, shop.M, _now() - timedelta(days=8))                 # ... 8 days of silence
    raw_apply(shop, reencoded(old))                                        # the insider's replay
    last = signer_row(shop, shop.M.dev)["last_signed_seen_at_utc"]
    assert shop.apply(_takeover(shop.B.dev), shop.B) is True, \
        f"replay refreshed the dead main's last-seen to {last}; parked={shop.parked()}"


def test_q5_the_same_replay_sent_to_the_main_itself_cannot_keep_it_from_stepping_down(shop):
    """Receiver IS the main (own echoes refresh its own row)."""
    shop.svc._authority_env_provider = shop.world.provider(shop.M)
    old = _main_event_under_recent_lease(shop)
    assert raw_apply(shop, old) is True
    age_last_seen(shop, shop.M, _now() - timedelta(days=8))
    raw_apply(shop, reencoded(old))
    assert shop.apply(_takeover(shop.B.dev), shop.B) is True, f"parked={shop.parked()}"


def test_q5_a_member_cannot_touch_the_mains_liveness_row(shop):
    assert shop.apply(shop.user_event(CASHIER, display_name="m"), shop.M) is True
    age_last_seen(shop, shop.M, _now() - timedelta(hours=1))
    before = signer_row(shop, shop.M.dev)
    # forged as the main; forged takeover; a stale-lease rebind attempt; A's own events
    raw_apply(shop, authority.sign_event(shop.user_event(CASHIER, display_name="f"), impersonator(shop.A, shop.M.dev)))
    old_lease_m = shop.world.new_device(shop.M.dev, inst="squat-inst", issued_at=_now() - timedelta(days=30))
    shop.apply(shop.user_event(CASHIER, display_name="g", row_version=7), old_lease_m)
    shop.apply(shop.user_event(CASHIER, display_name="h", row_version=8), shop.A)
    shop.pull()
    assert signer_row(shop, shop.M.dev) == before
    assert shop.apply(_takeover(shop.A.dev), shop.A) is False                # main is alive: refused


# ═══ Q6: quarantine key ══════════════════════════════════════════════════════

def _park_legit_main_owner_change(shop):
    _drop_main(shop)
    legit = shop.world.sign(shop.user_event(OWNER, password_hash="FROM-MAIN"), shop.M)
    assert raw_apply(shop, legit) is False
    assert shop.parked("user") == ["main_unknown"]
    return legit


@pytest.mark.parametrize("tamper", ["payload", "auth_version"])
def test_q6_copying_the_legit_signature_onto_junk_cannot_overwrite_the_parked_event(shop, tamper):
    """auth_key = sha256(_auth.sig TEXT). The signature is public (it travels on
    the relay), so an insider can copy it verbatim onto a junk payload: same key,
    the junk fails verification and the UPSERT replaces the legit row's payload."""
    legit = _park_legit_main_owner_change(shop)
    junk = copy.deepcopy(legit)
    if tamper == "payload":
        junk["payload"]["password_hash"] = "JUNK"
    else:
        junk["payload"]["_auth"]["v"] = 2
    raw_apply(shop, junk)
    _put_main(shop)
    shop.pull()
    assert shop.user(OWNER)["password_hash"] == "FROM-MAIN", \
        f"the parked legitimate main change was destroyed (quarantine={[(r['reason'], json.loads(r['payload']).get('password_hash')) for r in quarantine(shop)]})"


def test_q6_old_shape_copying_the_legit_signature_cannot_overwrite_the_parked_event(shop):
    to_old_shape_quarantine(shop)
    legit = _park_legit_main_owner_change(shop)
    junk = copy.deepcopy(legit)
    junk["payload"]["password_hash"] = "JUNK"
    raw_apply(shop, junk)
    _put_main(shop)
    shop.pull()
    assert shop.user(OWNER)["password_hash"] == "FROM-MAIN", \
        f"old shape: parked legit change destroyed ({[r['reason'] for r in quarantine(shop)]})"


def test_q6_old_shape_a_member_naming_the_mains_inst_cannot_overwrite_the_parked_event(shop):
    """R6 says 'between two DIFFERENT signers the first parked signed event is
    kept'. The guard compares the UNVERIFIED `_auth.inst`; the insider writes the
    main's inst into its own signed junk."""
    to_old_shape_quarantine(shop)
    _park_legit_main_owner_change(shop)
    junk = shop.world.sign(shop.user_event(OWNER, password_hash="JUNK", row_version=9), shop.A)
    junk["payload"]["_auth"]["inst"] = shop.M.inst
    raw_apply(shop, junk)
    _put_main(shop)
    shop.pull()
    assert shop.user(OWNER)["password_hash"] == "FROM-MAIN", \
        f"old shape: R6 guard bypassed ({[r['reason'] for r in quarantine(shop)]})"


def test_q6_flooding_the_quarantine_never_evicts_the_parked_legit_event_or_evidence(shop):
    forged = shop.user_event(CASHIER, display_name="x")  # warm-up: bind A
    shop.apply(forged, shop.A)
    shop.apply(shop.user_event(OWNER, password_hash="EVIDENCE-HASH", row_version=3), shop.A)
    assert shop.parked("user") == ["owner_change_needs_main"]
    _park_legit_main_owner_change_keep_evidence(shop)
    for i in range(150):
        shop.apply(shop.user_event(OWNER, password_hash=f"J{i}", row_version=100 + i), shop.A)   # signed junk
        raw_apply(shop, {"entity_type": "user", "entity_id": OWNER, "event_type": "update",
                         "payload": {"uid": OWNER, "n": i}})                                      # unsigned junk
        raw_apply(shop, reencoded(shop.world.sign(
            shop.user_event(OWNER, password_hash="EVIDENCE-HASH", row_version=3), shop.A)))     # variants
    reasons = [(r["reason"], json.loads(r["payload"]).get("password_hash")) for r in quarantine(shop)]
    assert ("owner_change_needs_main", "EVIDENCE-HASH") in reasons
    assert ("main_unknown", "FROM-MAIN") in reasons
    _put_main(shop)
    shop.pull()
    assert shop.user(OWNER)["password_hash"] == "FROM-MAIN"
    assert ("owner_change_needs_main", "EVIDENCE-HASH") in [
        (r["reason"], json.loads(r["payload"]).get("password_hash")) for r in quarantine(shop)]


def _park_legit_main_owner_change_keep_evidence(shop):
    _drop_main(shop)
    legit = shop.world.sign(shop.user_event(OWNER, password_hash="FROM-MAIN"), shop.M)
    assert raw_apply(shop, legit) is False
    return legit


def test_q6_a_member_cannot_erase_parked_evidence_by_rewriting_its_payload(shop):
    """A's forged owner change is parked as evidence. A re-sends the same
    signature text onto a different payload: the evidence row's PAYLOAD (what a
    human reads in sync-review) must not be replaced."""
    ev = shop.world.sign(shop.user_event(OWNER, password_hash="EVIDENCE-HASH", row_version=3), shop.A)
    assert raw_apply(shop, ev) is False
    cover = copy.deepcopy(ev)
    cover["payload"]["password_hash"] = f"HASH-{OWNER}"
    cover["payload"]["display_name"] = "nothing to see"
    raw_apply(shop, cover)
    rows = [(r["reason"], json.loads(r["payload"]).get("password_hash")) for r in quarantine(shop)]
    assert ("owner_change_needs_main", "EVIDENCE-HASH") in rows, rows


# ═══ controls (prove the failing tests' setups are right) and the lease swap ══

def test_q5_control_a_verbatim_replay_does_not_fake_life(shop):
    """CONTROL for the re-encoding test: identical setup, the replay is sent
    VERBATIM. Must pass -- so the only thing that differs in the failing test is
    the base64 spelling of the same 64 signature bytes."""
    old = _main_event_under_recent_lease(shop)
    assert raw_apply(shop, old) is True
    age_last_seen(shop, shop.M, _now() - timedelta(days=8))
    raw_apply(shop, copy.deepcopy(old))
    assert shop.apply(_takeover(shop.B.dev), shop.B) is True, f"parked={shop.parked()}"


def test_q5_an_old_main_event_carrying_the_mains_newer_lease_does_not_fake_life(shop):
    """The lease is NOT inside the signed bytes (`build_tbs` excludes `_auth`).
    An insider takes an OLD main event (signed under a 30-day-old lease, so it
    was never counted in `sync_signed_seen` -- `record_liveness` returns before
    the INSERT for an old lease, and prune drops counted rows past 15 days) and
    swaps in the main's most recent lease, copied off any recent main event.
    The lease-age window then measures the swapped-in lease, not the event."""
    ancient = shop.world.new_device(shop.M.dev, inst=shop.M.inst, key=shop.M.key,
                                    issued_at=_now() - timedelta(days=30))
    old = shop.world.sign(shop.user_event(CASHIER, display_name="main, a month ago"), ancient)
    assert raw_apply(shop, old) is True                                   # delivered a month ago
    recent = _main_event_under_recent_lease(shop, days_ago=8)            # last words, 8 days ago
    assert raw_apply(shop, recent) is True
    age_last_seen(shop, shop.M, _now() - timedelta(days=8))              # ... 8 days of silence
    swapped = copy.deepcopy(old)
    swapped["payload"]["_auth"]["lease"] = copy.deepcopy(recent["payload"]["_auth"]["lease"])
    raw_apply(shop, swapped)                                             # the insider's replay
    last = signer_row(shop, shop.M.dev)["last_signed_seen_at_utc"]
    assert shop.apply(_takeover(shop.B.dev), shop.B) is True, \
        f"lease-swapped replay refreshed the dead main's last-seen to {last}; parked={shop.parked()}"


def test_q6_control_the_parked_legit_change_applies_when_left_alone(shop):
    """CONTROL for the signature-copy tests: same setup, no junk."""
    _park_legit_main_owner_change(shop)
    _put_main(shop)
    shop.pull()
    assert shop.user(OWNER)["password_hash"] == "FROM-MAIN", quarantine(shop)


def test_q6_control_evidence_row_without_a_cover_copy(shop):
    ev = shop.world.sign(shop.user_event(OWNER, password_hash="EVIDENCE-HASH", row_version=3), shop.A)
    assert raw_apply(shop, ev) is False
    rows = [(r["reason"], json.loads(r["payload"]).get("password_hash")) for r in quarantine(shop)]
    assert rows == [("owner_change_needs_main", "EVIDENCE-HASH")], rows


@pytest.mark.parametrize("variant", ["OWNER-UID", " owner-uid", "owner-uid ", "owner-uid​"])
def test_q4_a_lookalike_owner_uid_cannot_reach_the_owner_row(shop, variant):
    """The rule's set membership is exact-text; the applier's ON CONFLICT(uid)
    uses the column's collation. If they disagreed, a lookalike uid would pass
    the rule as 'not the owner' and still hit the owner's row."""
    target = variant.replace("OWNER-UID", OWNER.upper()).replace("owner-uid", OWNER)
    assert target != OWNER
    ev = shop.user_event(OWNER, role="cashier", password_hash="ATTACKER", row_version=9)
    ev["entity_id"] = target
    ev["payload"]["uid"] = target
    ev["payload"]["email"] = f"look{abs(hash(variant))}@x.com"
    ev["payload"]["employee_id"] = "EMP-0777"
    shop.apply(ev, shop.A)
    row = shop.user(OWNER)
    assert (row["role"], row["password_hash"]) == ("admin", f"HASH-{OWNER}"), (row["role"], row["password_hash"])
