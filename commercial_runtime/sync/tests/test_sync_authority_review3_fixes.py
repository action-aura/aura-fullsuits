"""AUDIT-033 fix round 2 -- the receiver gaps G1-G4 from review V3r, pinned in BOTH
directions (what must be refused AND what must still work).

`test_sync_authority_review3.py` carries the reviewer's own attacks verbatim;
this file adds the unit-level pins and the allow-halves the attacks cannot show:

  G1  strict base64 (all 16 spellings of one signature, `pk` too), the lease is
      inside the signed bytes, a v1-context event is refused, the liveness id
      is the hash of the DECODED bytes;
  G2  the quarantine key is the content of the whole stored payload; identical
      content moves only reason/detail; the junk bound counts verification
      failures only and never touches evidence or `main_unknown`;
  G3  old-shape fallback: an existing verified row yields only to a verified
      event from the same signer;
  G4  a user upsert never reaches another company's row.

Run (ONE FILE PER PYTEST PROCESS):
    pytest commercial_runtime/sync/tests/test_sync_authority_review3_fixes.py -q
"""
from __future__ import annotations

import base64
import copy
import hashlib
import json
import logging
import string
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from commercial_runtime.sync import authority
from commercial_runtime.sync import sync_service as ss
from commercial_runtime.sync.tests._signing import install_main
from commercial_runtime.sync.tests.test_sync_authority_rules import COMPANY, OWNER, CASHIER, Shop


@pytest.fixture
def shop(tmp_path, monkeypatch):
    ss._FLOOD_WARNED.clear()
    return Shop(tmp_path, monkeypatch)


def _signed(shop, signer=None, **over):
    return shop.world.sign(shop.user_event(CASHIER, display_name="x", **over), signer or shop.M)


def _verify(shop, ev):
    env = shop.svc._authority_env()
    return authority.verify_event(ev, trust_store=env.trust_store, own_licence=env.own_licence)


def _spellings(text):
    """Every base64 text (same length) that decodes to the same bytes."""
    raw = base64.b64decode(text)
    out = []
    for c in string.ascii_letters + string.digits + "+/":
        alt = text[:-3] + c + "=="
        try:
            if base64.b64decode(alt, validate=True) == raw:
                out.append(alt)
        except Exception:  # noqa: BLE001
            continue
    return out


def raw_apply(shop, ev):
    conn = shop.get_conn()
    try:
        r = shop.svc._apply_event(conn, ev, local_company_id=COMPANY)
        conn.commit()
        return r
    finally:
        conn.close()


def _drop_main(shop):
    conn = shop.get_conn()
    conn.execute("DELETE FROM admin_device_assignment")
    conn.commit()
    conn.close()


def _put_main(shop):
    conn = shop.get_conn()
    install_main(conn, COMPANY, shop.M.dev)
    conn.close()


def _park(shop, entity_id, reason, payload, *, entity_type="user"):
    conn = shop.get_conn()
    try:
        shop.svc._quarantine_apply_event(
            conn, {"entity_type": entity_type, "entity_id": entity_id, "event_type": "update",
                   "payload": payload}, reason=reason, detail="d")
        conn.commit()
    finally:
        conn.close()


def _rows(shop, entity_id=None):
    sql = "SELECT entity_id, reason, payload, auth_key FROM sync_apply_quarantine"
    if entity_id is None:
        return shop.q(sql + " ORDER BY rowid")
    return shop.q(sql + " WHERE entity_id=? ORDER BY rowid", (entity_id,))


# ═══ G1: strict base64, lease bound into the signed bytes ════════════════════

def test_the_canonical_signature_spelling_verifies(shop):
    assert isinstance(_verify(shop, _signed(shop)), authority.VerifiedSigner)


def test_each_of_the_16_noncanonical_signature_spellings_is_refused(shop):
    ev = _signed(shop)
    spellings = _spellings(ev["payload"]["_auth"]["sig"])
    assert len(spellings) == 16, len(spellings)       # the premise: 16 texts, one signature
    refused = 0
    for alt in spellings:
        copy_ev = copy.deepcopy(ev)
        copy_ev["payload"]["_auth"]["sig"] = alt
        verdict = _verify(shop, copy_ev)
        if alt == ev["payload"]["_auth"]["sig"]:
            assert isinstance(verdict, authority.VerifiedSigner)
            continue
        assert isinstance(verdict, authority.Refusal) and verdict.reason == authority.REASON_BAD_SIGNATURE, alt
        refused += 1
    assert refused == 15


def test_noncanonical_pk_spellings_are_refused(shop):
    ev = _signed(shop)
    pk = ev["payload"]["_auth"]["pk"]
    raw = base64.b64decode(pk)
    assert len(raw) == 32 and pk.endswith("=") and not pk.endswith("==")
    alts = []
    for c in string.ascii_letters + string.digits + "+/":
        alt = pk[:-2] + c + "="
        if alt != pk and base64.b64decode(alt, validate=True) == raw:
            alts.append(alt)
    assert alts, "no alternative pk spelling found"
    for alt in alts:
        copy_ev = copy.deepcopy(ev)
        copy_ev["payload"]["_auth"]["pk"] = alt
        verdict = _verify(shop, copy_ev)
        assert isinstance(verdict, authority.Refusal) and verdict.reason == authority.REASON_BAD_SIGNATURE, alt


def test_the_liveness_id_is_the_hash_of_the_decoded_signature_bytes(shop):
    ev = _signed(shop)
    verdict = _verify(shop, ev)
    raw = base64.b64decode(ev["payload"]["_auth"]["sig"])
    assert verdict.sig_sha256 == hashlib.sha256(raw).hexdigest()
    assert verdict.sig_sha256 != hashlib.sha256(ev["payload"]["_auth"]["sig"].encode()).hexdigest()


def test_the_lease_is_inside_the_signed_bytes(shop):
    """Swapping in a DIFFERENT genuine lease of the same signer (a newer one)
    breaks the event signature: the tbs names the lease's Owner signature."""
    ancient = shop.world.new_device(shop.M.dev, inst=shop.M.inst, key=shop.M.key,
                                    issued_at=datetime.now(timezone.utc) - timedelta(days=30))
    recent = shop.world.new_device(shop.M.dev, inst=shop.M.inst, key=shop.M.key,
                                   issued_at=datetime.now(timezone.utc) - timedelta(days=1))
    old = shop.world.sign(shop.user_event(CASHIER, display_name="old"), ancient)
    assert isinstance(_verify(shop, old), authority.VerifiedSigner)           # control: as signed
    swapped = copy.deepcopy(old)
    swapped["payload"]["_auth"]["lease"] = copy.deepcopy(recent.signer.lease)
    verdict = _verify(shop, swapped)
    assert isinstance(verdict, authority.Refusal) and verdict.reason == authority.REASON_BAD_SIGNATURE


def test_the_tbs_carries_the_lease_signature_and_the_v2_context(shop):
    ev = _signed(shop)
    tbs = json.loads(authority.build_tbs(ev, licence=shop.M.signer.licence, inst=shop.M.inst, dev=shop.M.dev))
    assert tbs["ctx"] == "aura-sync-authority/v2"
    assert tbs["lease_sig"] == shop.M.signer.lease["signature"] and tbs["lease_sig"]


def test_an_event_signed_under_the_v1_context_is_refused(shop, monkeypatch):
    monkeypatch.setattr(authority, "AUTH_CONTEXT", "aura-sync-authority/v1")
    old_context_event = _signed(shop)
    monkeypatch.undo()
    verdict = _verify(shop, old_context_event)
    assert isinstance(verdict, authority.Refusal) and verdict.reason == authority.REASON_BAD_SIGNATURE


# ═══ G2: the quarantine key and the hold queue ═══════════════════════════════

def test_the_key_is_the_content_of_the_whole_payload():
    base = {"uid": "u", "n": 1, "_auth": {"v": 1, "sig": "AAAA"}}
    same_other_order = {"_auth": {"sig": "AAAA", "v": 1}, "n": 1, "uid": "u"}
    assert authority.quarantine_auth_key(base) == authority.quarantine_auth_key(same_other_order)
    assert len(authority.quarantine_auth_key(base)) == 64
    for changed in (dict(base, n=2), dict(base, _auth={"v": 1, "sig": "AAAB"}), dict(base, extra=1)):
        assert authority.quarantine_auth_key(changed) != authority.quarantine_auth_key(base)


def test_an_unsigned_authority_payload_gets_a_content_key_and_other_events_keep_empty():
    unsigned = {"uid": "u", "n": 1}
    assert authority.quarantine_auth_key(unsigned, "user") != ""
    assert authority.quarantine_auth_key(unsigned, "user") != authority.quarantine_auth_key({"uid": "u", "n": 2}, "user")
    assert authority.quarantine_auth_key({"qty": 1}, "sale_item") == ""
    assert authority.quarantine_auth_key({"skey": "other"}, "retail_setting") == ""
    assert authority.quarantine_auth_key({"skey": "till_release_ack:x"}, "retail_setting") != ""


def test_a_copy_with_other_content_gets_its_own_row(shop):
    _park(shop, "e1", "main_unknown", {"uid": "e1", "v": "legit", "_auth": {"sig": "S"}})
    _park(shop, "e1", "bad_signature", {"uid": "e1", "v": "junk", "_auth": {"sig": "S"}})
    rows = _rows(shop, "e1")
    assert sorted(json.loads(r["payload"])["v"] for r in rows) == ["junk", "legit"]
    assert len({r["auth_key"] for r in rows}) == 2


def test_identical_content_updates_only_reason_and_never_the_stored_payload_text(shop):
    legit = {"uid": "e2", "v": "legit", "_auth": {"sig": "S", "v": 1}}
    _park(shop, "e2", "main_unknown", legit)
    first_text = _rows(shop, "e2")[0]["payload"]
    # the SAME content spelled with a different key order -> the same row
    _park(shop, "e2", "needs_main_computer", {"_auth": {"v": 1, "sig": "S"}, "v": "legit", "uid": "e2"})
    rows = _rows(shop, "e2")
    assert len(rows) == 1
    assert rows[0]["reason"] == "needs_main_computer"           # the reason moved ...
    assert rows[0]["payload"] == first_text                       # ... the stored payload did not


def test_a_non_authority_event_still_follows_its_latest_payload(shop):
    """The allow-half: a sale child carries nothing signed, so its parked payload
    keeps following the latest attempt (P2S L2)."""
    conn = shop.get_conn()
    for qty in (1, 2):
        shop.svc._quarantine_apply_event(
            conn, {"entity_type": "sale_item", "entity_id": "si-1", "event_type": "create",
                   "payload": {"qty": qty}}, reason="missing_parent:sale", detail="d")
    conn.commit()
    conn.close()
    rows = _rows(shop, "si-1")
    assert len(rows) == 1 and json.loads(rows[0]["payload"]) == {"qty": 2} and rows[0]["auth_key"] == ""


def test_evidence_is_never_downgraded_to_a_reason_a_later_event_may_clear(shop):
    payload = {"uid": "e3", "v": 1, "_auth": {"sig": "S"}}
    _park(shop, "e3", "owner_change_needs_main", payload)
    _park(shop, "e3", "main_unknown", payload)
    assert [r["reason"] for r in _rows(shop, "e3")] == ["owner_change_needs_main"]


def _junk_count(shop, entity_id):
    return len([r for r in _rows(shop, entity_id) if r["reason"] == "bad_signature"])


def test_junk_is_bounded_per_entity_and_logged_once(shop, caplog):
    cap = authority.MAX_PARKED_VERIFICATION_FAILURES_PER_ENTITY
    assert cap == 50
    with caplog.at_level(logging.WARNING, logger="commercial_runtime.sync.sync_service"):
        for i in range(cap + 25):
            _park(shop, "flood", "bad_signature", {"uid": "flood", "n": i})
    assert _junk_count(shop, "flood") == cap
    assert len([r for r in caplog.records if "evicting" in r.getMessage()]) == 1
    # Review V4r N2 (deliberate reversal of the old rule "keep the oldest, drop the
    # newest"): over the bound the OLDEST permanent junk is evicted to make room, so
    # a recoverable failure arriving late is never the row that vanishes. This test
    # can therefore no longer catch "the newest junk is the one dropped"; the
    # replacement rule is pinned in test_sync_authority_review4_fixes.py.
    kept = sorted(json.loads(r["payload"])["n"] for r in _rows(shop, "flood"))
    assert kept == list(range(25, 25 + cap))
    # allow-half: another entity is unaffected, and a row already held can still move
    _park(shop, "other", "bad_signature", {"uid": "other", "n": 1})
    assert _junk_count(shop, "other") == 1
    _park(shop, "flood", "unsigned_authority_event", {"uid": "flood", "n": 0})
    assert len(_rows(shop, "flood")) == cap


def test_the_bound_never_counts_or_drops_evidence_or_main_unknown(shop):
    cap = authority.MAX_PARKED_VERIFICATION_FAILURES_PER_ENTITY
    for i in range(cap + 10):
        _park(shop, "mix", "bad_signature", {"uid": "mix", "n": i})
    assert _junk_count(shop, "mix") == cap
    for i in range(cap + 10):
        _park(shop, "mix", "owner_change_needs_main", {"uid": "mix", "ev": i, "_auth": {"sig": f"S{i}"}})
        _park(shop, "mix", "main_unknown", {"uid": "mix", "mu": i, "_auth": {"sig": f"M{i}"}})
    reasons = [r["reason"] for r in _rows(shop, "mix")]
    assert reasons.count("owner_change_needs_main") == cap + 10
    assert reasons.count("main_unknown") == cap + 10
    assert reasons.count("bad_signature") == cap          # and evidence did not make room for more junk


# ═══ G3: old-shape fallback ══════════════════════════════════════════════════

def _to_old_shape(shop):
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


def _parked_hash(shop):
    rows = shop.q("SELECT payload FROM sync_apply_quarantine WHERE entity_type='user'")
    assert len(rows) == 1
    return json.loads(rows[0]["payload"])["password_hash"]


def test_old_shape_a_verified_row_yields_to_a_verified_event_of_the_same_signer(shop):
    """The allow-half of G3: the main's NEWER change replaces the main's parked one."""
    _to_old_shape(shop)
    _drop_main(shop)
    assert raw_apply(shop, shop.world.sign(shop.user_event(OWNER, password_hash="FIRST"), shop.M)) is False
    assert _parked_hash(shop) == "FIRST"
    assert raw_apply(shop, shop.world.sign(shop.user_event(OWNER, password_hash="SECOND"), shop.M)) is False
    assert _parked_hash(shop) == "SECOND"
    _put_main(shop)
    shop.pull()
    assert shop.user(OWNER)["password_hash"] == "SECOND"


def test_old_shape_a_row_that_does_not_verify_is_overwritable_by_a_verified_event(shop):
    _to_old_shape(shop)
    _drop_main(shop)
    junk = shop.user_event(OWNER, password_hash="JUNK")           # unsigned
    assert raw_apply(shop, junk) is False
    assert _parked_hash(shop) == "JUNK"
    assert raw_apply(shop, shop.world.sign(shop.user_event(OWNER, password_hash="REAL"), shop.M)) is False
    assert _parked_hash(shop) == "REAL"


def test_old_shape_a_verified_row_is_kept_against_another_verified_signer(shop):
    """A decides on the VERIFIED signer, never on `_auth.inst` text: a different
    member's perfectly valid event does not displace the main's parked one."""
    _to_old_shape(shop)
    _drop_main(shop)
    assert raw_apply(shop, shop.world.sign(shop.user_event(OWNER, password_hash="FROM-MAIN"), shop.M)) is False
    raw_apply(shop, shop.world.sign(shop.user_event(OWNER, password_hash="FROM-A", row_version=9), shop.A))
    assert _parked_hash(shop) == "FROM-MAIN"


# ═══ G4: a user upsert stays inside the company ═════════════════════════════

def _insert_other_company_user(shop, uid="other-owner", role="admin"):
    conn = shop.get_conn()
    conn.execute(
        "INSERT INTO users (id, company_id, employee_id, email, password_hash, role, status, "
        "require_password_change, session_version, language, uid, pin_hash, row_version, "
        "updated_at_utc, display_name) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (str(uuid.uuid4()), "company-other", "ADMIN-0001", f"{uid}@x.com", "HASH-OTHER", role,
         "active", 0, 1, "en", uid, "PIN-O", 1, "2026-01-01T00:00:00+00:00", "Other"))
    conn.commit()
    conn.close()


def test_a_user_event_for_another_companys_uid_is_parked_and_writes_nothing(shop):
    _insert_other_company_user(shop, role="cashier")              # not an owner: only the company check can stop it
    before = shop.user("other-owner")
    ev = shop.user_event("other-owner", display_name="hijacked", row_version=9)
    assert shop.apply(ev, shop.A) is False
    assert shop.parked("user") == ["foreign_company_user"]
    assert shop.user("other-owner") == before


def test_a_user_event_for_this_companys_uid_still_applies(shop):
    assert shop.apply(shop.user_event(CASHIER, display_name="same company"), shop.A) is True
    assert shop.user(CASHIER)["display_name"] == "same company"
    assert shop.parked() == []


def test_a_brand_new_uid_is_still_created(shop):
    ev = {"entity_type": "user", "entity_id": "new-u", "event_type": "create", "payload": {
        "uid": "new-u", "employee_id": "EMP-0100", "email": "n@x.com", "role": "cashier", "status": "active",
        "require_password_change": 0, "language": "en", "password_hash": "H", "pin_hash": "P",
        "row_version": 1, "updated_at_utc": "2026-02-01T00:00:00+00:00", "session_version": 1}}
    assert shop.apply(ev, shop.A) is True
    assert shop.user("new-u")["company_id"] == COMPANY
