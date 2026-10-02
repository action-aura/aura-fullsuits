"""AUDIT-033 S-AUTH -- independent review V4r (third round, RECEIVER half), against
a9e5dcb6 (fix round 2: G1-G4 from review V3r).

Every test asserts the SAFE outcome: a failing test is a real gap. The attacker is
a licensed member computer (shop.A / shop.B) holding its own valid key and its own
Owner lease (which names only its own id). Run from the worktree root, ONE FILE
PER PYTEST PROCESS:

    PYTHONPATH=<wt> python -m pytest <this file> -q -p no:cacheprovider
"""
from __future__ import annotations

import base64
import copy
import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from commercial_runtime.licensing_contracts.trust_store import TrustedKey
from commercial_runtime.sync import authority
from commercial_runtime.sync import sync_service as ss
from commercial_runtime.sync.tests._signing import install_main, make_lease, _raw_pub
from commercial_runtime.sync.tests.test_sync_authority_rules import Shop, COMPANY, OWNER, CASHIER


@pytest.fixture
def shop(tmp_path, monkeypatch):
    ss._FLOOD_WARNED.clear()
    authority._LEASE_CACHE.clear()
    return Shop(tmp_path, monkeypatch)


# ── helpers ──────────────────────────────────────────────────────────────────

def _now():
    return datetime.now(timezone.utc)


def _verify(shop, ev):
    env = shop.svc._authority_env()
    return authority.verify_event(ev, trust_store=env.trust_store, own_licence=env.own_licence)


def raw_apply(shop, ev, company=COMPANY):
    conn = shop.get_conn()
    try:
        r = shop.svc._apply_event(conn, ev, local_company_id=company)
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


def _last_seen(shop, dev):
    rows = shop.q("SELECT last_signed_seen_at_utc FROM sync_signers WHERE device_id=?", (dev,))
    return rows[0]["last_signed_seen_at_utc"] if rows else None


def _signer_as(attacker, as_dev, lease):
    key = attacker.key
    return authority.LocalSigner(
        licence=attacker.signer.licence, inst=attacker.inst, dev=as_dev, pk_b64=attacker.signer.pk_b64,
        lease=lease, sign_fn=lambda data: base64.b64encode(key.sign(data)).decode("ascii"))


def _main_event(shop, signer=None, **over):
    over.setdefault("display_name", f"m-{uuid.uuid4().hex[:6]}")
    return shop.world.sign(shop.user_event(CASHIER, **over), signer or shop.M)


def _is_refused(verdict, *reasons):
    return isinstance(verdict, authority.Refusal) and (not reasons or verdict.reason in reasons)


# ═══ 1. G1: the lease inside the signed bytes ════════════════════════════════

def test_g1_a_member_cannot_attach_the_mains_lease_to_its_own_event_posing_as_main(shop):
    """A re-signs with ITS key, names the main's id, and carries the MAIN's genuine,
    fresh lease (so the lease-age window would be the main's). The lease pins the
    main's key fingerprint, so it cannot vouch for A's key."""
    stale = _now() - timedelta(days=8)
    shop.set_last_seen(shop.M, stale)
    before = _last_seen(shop, shop.M.dev)
    ev = authority.sign_event(shop.user_event(CASHIER, display_name="posing"),
                              _signer_as(shop.A, shop.M.dev, copy.deepcopy(shop.M.signer.lease)))
    assert _is_refused(_verify(shop, ev), authority.REASON_UNTRUSTED_LEASE)
    raw_apply(shop, ev)
    assert _last_seen(shop, shop.M.dev) == before
    assert shop.user(CASHIER)["display_name"] != "posing"


def test_g1_a_member_naming_the_mains_inst_and_lease_with_its_own_key_is_refused(shop):
    """Isolates the fingerprint pin: inst, dev and lease are ALL the main's, only the
    key is the attacker's. (The two neighbouring tests are also stopped by the
    installation-id pin, so they cannot see this guard alone.)"""
    key = shop.A.key
    signer = authority.LocalSigner(
        licence=shop.A.signer.licence, inst=shop.M.inst, dev=shop.M.dev, pk_b64=shop.A.signer.pk_b64,
        lease=copy.deepcopy(shop.M.signer.lease),
        sign_fn=lambda data: base64.b64encode(key.sign(data)).decode("ascii"))
    shop.set_last_seen(shop.M, _now() - timedelta(days=8))
    before = _last_seen(shop, shop.M.dev)
    ev = authority.sign_event(shop.user_event(CASHIER, display_name="posing2"), signer)
    assert _is_refused(_verify(shop, ev), authority.REASON_UNTRUSTED_LEASE)
    raw_apply(shop, ev)
    assert _last_seen(shop, shop.M.dev) == before
    assert shop.user(CASHIER)["display_name"] != "posing2"


def test_g1_a_member_cannot_attach_the_mains_lease_under_its_own_id(shop):
    ev = authority.sign_event(shop.user_event(CASHIER, display_name="x"),
                              _signer_as(shop.A, shop.A.dev, copy.deepcopy(shop.M.signer.lease)))
    assert _is_refused(_verify(shop, ev), authority.REASON_UNTRUSTED_LEASE)


def test_g1_a_member_re_signing_under_its_own_older_lease_verifies_but_proves_no_life(shop):
    """Legitimate (its own lease) -- but a 30-day-old lease must not refresh liveness,
    and must not move the stored lease stamp backwards."""
    a_old = shop.world.new_device(shop.A.dev, inst=shop.A.inst, key=shop.A.key,
                                  issued_at=_now() - timedelta(days=30))
    assert raw_apply(shop, shop.world.sign(shop.user_event(CASHIER, display_name="fresh"), shop.A)) is True
    stamp_after_fresh = shop.q("SELECT lease_issued_at_utc, last_signed_seen_at_utc AS seen "
                               "FROM sync_signers WHERE device_id=?", (shop.A.dev,))[0]
    ev = shop.world.sign(shop.user_event(CASHIER, display_name="old-lease"), a_old)
    verdict = _verify(shop, ev)
    assert isinstance(verdict, authority.VerifiedSigner)            # its own lease: legitimate
    assert raw_apply(shop, ev) is True
    row = shop.q("SELECT lease_issued_at_utc, last_signed_seen_at_utc AS seen FROM sync_signers "
                 "WHERE device_id=?", (shop.A.dev,))[0]
    assert row["lease_issued_at_utc"] == stamp_after_fresh["lease_issued_at_utc"]   # no regression
    assert row["seen"] == stamp_after_fresh["seen"]                 # an old lease is not life


def test_g1_a_re_spelled_lease_signature_breaks_the_event_and_proves_no_life(shop):
    ev = _main_event(shop)
    shop.set_last_seen(shop.M, _now() - timedelta(days=8))
    before = _last_seen(shop, shop.M.dev)
    lease_sig = ev["payload"]["_auth"]["lease"]["signature"]
    raw = base64.b64decode(lease_sig)
    alts = []
    for c in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/":
        alt = lease_sig[:-3] + c + "=="
        if alt != lease_sig and base64.b64decode(alt, validate=True) == raw:
            alts.append(alt)
    assert alts
    for alt in alts:
        e = copy.deepcopy(ev)
        e["payload"]["_auth"]["lease"]["signature"] = alt
        assert _is_refused(_verify(shop, e)), alt
        raw_apply(shop, e)
    assert _last_seen(shop, shop.M.dev) == before


def test_g1_an_old_main_event_with_its_lease_payload_redated_proves_no_life(shop):
    """The lease CACHE is keyed by the lease signature, not its payload: with the
    cache warm, a re-dated lease payload returns the CACHED (true) payload; cold,
    the Owner signature fails. Either way, no fresh issued_at reaches liveness."""
    m_old = shop.world.new_device(shop.M.dev, inst=shop.M.inst, key=shop.M.key,
                                  issued_at=_now() - timedelta(days=30))
    ev = shop.world.sign(shop.user_event(CASHIER, display_name="ancient"), m_old)
    assert isinstance(_verify(shop, ev), authority.VerifiedSigner)  # warms the cache
    shop.set_last_seen(shop.M, _now() - timedelta(days=8))
    before = _last_seen(shop, shop.M.dev)
    redated = copy.deepcopy(ev)
    lp = redated["payload"]["_auth"]["lease"]["payload"]
    lp["issued_at"] = _now().isoformat()
    lp["not_before"] = (_now() - timedelta(minutes=5)).isoformat()
    lp["expires_at"] = (_now() + timedelta(hours=24)).isoformat()
    warm = _verify(shop, redated)
    assert not (isinstance(warm, authority.VerifiedSigner)
                and warm.lease_issued_at is not None and _now() - warm.lease_issued_at < timedelta(days=14))
    raw_apply(shop, redated)
    authority._LEASE_CACHE.clear()
    assert _is_refused(_verify(shop, redated), authority.REASON_UNTRUSTED_LEASE)
    raw_apply(shop, redated)
    assert _last_seen(shop, shop.M.dev) == before


_L = 2 ** 252 + 27742317777372353535851937790883648493


def test_g1_an_s_plus_l_malleated_signature_is_refused_and_proves_no_life(shop):
    """Ed25519 malleability: S' = S + L also satisfies the group equation unless the
    verifier insists S < L. A fresh-looking signature for a captured main event
    would defeat the 'seen before' liveness check."""
    ev = _main_event(shop)
    raw = base64.b64decode(ev["payload"]["_auth"]["sig"])
    s = int.from_bytes(raw[32:], "little") + _L
    assert s < 2 ** 256
    mal = copy.deepcopy(ev)
    mal["payload"]["_auth"]["sig"] = base64.b64encode(raw[:32] + s.to_bytes(32, "little")).decode("ascii")
    assert _is_refused(_verify(shop, mal), authority.REASON_BAD_SIGNATURE)
    raw_apply(shop, ev)                                  # the original counts once
    shop.set_last_seen(shop.M, _now() - timedelta(days=8))
    before = _last_seen(shop, shop.M.dev)
    raw_apply(shop, mal)
    assert _last_seen(shop, shop.M.dev) == before


# ═══ 2. strict base64 on sig and pk ══════════════════════════════════════════

def _variants(text):
    out = {
        "trailing_newline": text + "\n",
        "trailing_crlf": text + "\r\n",
        "leading_space": " " + text,
        "inner_newline": text[:20] + "\n" + text[20:],
        "no_padding": text.rstrip("="),
        "extra_padding": text + "=",
        "urlsafe": text.replace("+", "-").replace("/", "_"),
        "fullwidth_digit": text.replace("A", "Ａ", 1) if "A" in text else text + "Ａ",
    }
    return {k: v for k, v in out.items() if v != text}


def _event_whose_sig_has_plus_or_slash(shop):
    for i in range(200):
        ev = _main_event(shop, display_name=f"p{i}")
        sig = ev["payload"]["_auth"]["sig"]
        if "+" in sig or "/" in sig:
            return ev
    raise AssertionError("no signature with + or / in 200 tries")


@pytest.mark.parametrize("field", ["sig", "pk"])
def test_strict_b64_refuses_every_non_canonical_shape(shop, field):
    ev = _event_whose_sig_has_plus_or_slash(shop) if field == "sig" else _main_event(shop)
    assert isinstance(_verify(shop, ev), authority.VerifiedSigner)   # control
    for name, alt in _variants(ev["payload"]["_auth"][field]).items():
        e = copy.deepcopy(ev)
        e["payload"]["_auth"][field] = alt
        assert _is_refused(_verify(shop, e), authority.REASON_BAD_SIGNATURE), (field, name, alt)


# ═══ 3. G2: the content key ══════════════════════════════════════════════════

_TRICKY = [
    {"uid": "u", "n": 1.5, "_auth": {"v": 1, "sig": "S"}},
    {"uid": "u", "n": 2.0, "_auth": {"v": 1, "sig": "S"}},
    {"uid": "u", "name": "\udc80lone", "_auth": {"v": 1, "sig": "S"}},
    {"uid": "u", "name": "José", "_auth": {"v": 1, "sig": "S"}},
    {"uid": "u", "big": 2 ** 80, "neg0": -0.0, "_auth": {"v": 1}},
    {"uid": "u", "deep": json.loads("[" * 30 + "]" * 30)},
    {"uid": "u", "nan": float("nan")},
]


@pytest.mark.parametrize("payload", _TRICKY, ids=range(len(_TRICKY)))
def test_g2_the_key_survives_the_json_round_trip_the_retry_loop_makes(payload):
    """The retry loop rebuilds the event from the STORED json text and re-parks it;
    a key that changed across that round trip would add a new row every pull."""
    stored = json.loads(json.dumps(payload, default=str))
    assert authority.quarantine_auth_key(payload, "user") == authority.quarantine_auth_key(stored, "user")


def test_g2_retrying_a_tricky_parked_event_never_grows_the_queue(shop):
    for i, extra in enumerate(({"n": 1.5}, {"display_name": "\udc80x"}, {"deep": json.loads("[" * 30 + "]" * 30)})):
        ev = shop.world.sign(shop.user_event(OWNER, password_hash=f"P{i}"), shop.A)
        ev["payload"].update(extra)                       # body changed after signing -> bad_signature
        shop.pull([ev])
    n0 = len(shop.q("SELECT 1 FROM sync_apply_quarantine"))
    for _ in range(3):
        shop.pull([])
    assert len(shop.q("SELECT 1 FROM sync_apply_quarantine")) == n0 == 3


def test_g2_a_deeply_nested_authority_event_never_wedges_the_pull(shop):
    """A poison event must park, not raise out of apply_pull_result (an exception
    there rolls back the batch and the cursor never moves)."""
    deep = json.loads("[" * 400 + "]" * 400)
    ev = shop.world.sign(shop.user_event(CASHIER, display_name="d"), shop.A)
    ev["payload"]["deep"] = deep
    shop.pull([ev])                                      # must not raise
    shop.pull([])
    assert shop.q("SELECT COUNT(*) AS n FROM sync_apply_quarantine")[0]["n"] == 1


# ═══ 4. the flood bound ══════════════════════════════════════════════════════

def test_flood_unverifiable_junk_spread_over_entity_ids_is_still_bounded(shop):
    """The bound is per (entity_type, entity_id), and the entity id is the
    attacker's to choose. Safe outcome: SOME bound applies to unverifiable junk
    as a whole (any number below what was sent)."""
    sent = 300
    good = shop.world.sign(shop.user_event(OWNER, password_hash="X"), shop.A)
    conn = shop.get_conn()
    try:
        for i in range(sent):
            e = copy.deepcopy(good)
            e["entity_id"] = e["payload"]["uid"] = f"junk-{i}"
            shop.svc._apply_event(conn, e, local_company_id=COMPANY)
        conn.commit()
    finally:
        conn.close()
    held = shop.q("SELECT COUNT(*) AS n FROM sync_apply_quarantine WHERE reason='bad_signature'")[0]["n"]
    assert held < sent, f"all {held} unverifiable junk rows were kept: the per-entity bound bounds nothing"


def _evidence_pass_seconds(tmp_path, monkeypatch, n):
    import time
    s = Shop(tmp_path / f"n{n}", monkeypatch)
    conn = s.get_conn()
    try:
        for i in range(n):        # member A, owner-critical change -> owner_change_needs_main (evidence)
            s.svc._apply_event(conn, s.world.sign(s.user_event(OWNER, password_hash=f"EVIL-{i}"), s.A),
                               local_company_id=COMPANY)
        conn.commit()
    finally:
        conn.close()
    assert len(s.q("SELECT 1 FROM sync_apply_quarantine")) == n
    t = time.perf_counter()
    s.pull([])                    # one pull = one retry pass of the whole queue
    return time.perf_counter() - t


def test_flood_evidence_on_one_entity_retry_cost_grows_linearly_not_quadratically(tmp_path, monkeypatch):
    """Evidence rows are unbounded by design, and every pull retries all of them in
    the pull's write transaction. Each re-park re-reads EVERY parked row of the
    same (entity_id, event_type) with its payload, so one entity's pile costs
    O(n^2) per pull (measured: 500 -> 12s, 1000 -> 54s, 2000 -> 246s per pass).
    Safe outcome: 8x the rows costs under 12x the time (linear ~8x; measured
    here at a9e5dcb6: 100 -> 0.48s, 400 -> 2.96s, 800 -> 8.86s, i.e. x18)."""
    ss._FLOOD_WARNED.clear()
    small = _evidence_pass_seconds(tmp_path, monkeypatch, 100)
    big = _evidence_pass_seconds(tmp_path, monkeypatch, 800)
    assert big < 12 * small, f"100 rows: {small:.2f}s, 800 rows: {big:.2f}s (x{big / small:.1f})"


def _main_signer_under_new_owner_key(shop, owner2):
    lease = make_lease(owner2, key_id="owner-2", inst=shop.M.inst, fingerprint=shop.M.fingerprint,
                       registry_device_id=shop.M.dev)
    key = shop.M.key
    return authority.LocalSigner(licence=shop.M.signer.licence, inst=shop.M.inst, dev=shop.M.dev,
                                 pk_b64=shop.M.signer.pk_b64, lease=lease,
                                 sign_fn=lambda d: base64.b64encode(key.sign(d)).decode("ascii"))


def _trust(shop, owner2):
    shop.world.trust_store._keys["owner-2"] = TrustedKey(
        key_id="owner-2", public_key_b64=base64.b64encode(_raw_pub(owner2)).decode("ascii"),
        algorithm="ed25519", status="ACTIVE", source="MANIFEST")
    authority._LEASE_CACHE.clear()


@pytest.mark.parametrize("prefill", [0, 50])
def test_flood_junk_prefill_cannot_make_a_legit_event_vanish(shop, prefill):
    """Owner rotates its signing key; this receiver has not admitted the new key
    yet, so the MAIN's real owner-password change parks as `untrusted_lease` (a
    verification failure) until it does. An insider who first parks 50 junk rows
    for the owner's uid makes the bound DROP the main's event instead -- the pull
    cursor moves on and it never applies. prefill=0 is the control."""
    owner2 = Ed25519PrivateKey.generate()
    junk_src = shop.world.sign(shop.user_event(OWNER, password_hash="J"), shop.A)
    for i in range(prefill):
        j = copy.deepcopy(junk_src)
        j["payload"]["password_hash"] = f"JUNK-{i}"      # altered after signing -> bad_signature
        shop.pull([j])
    real = authority.sign_event(shop.user_event(OWNER, password_hash="REAL-NEW"),
                                _main_signer_under_new_owner_key(shop, owner2))
    shop.pull([real])
    _trust(shop, owner2)                                 # the receiver catches up with Owner
    shop.pull([])
    assert shop.user(OWNER)["password_hash"] == "REAL-NEW"


# ═══ 5. G3: old-shape fallback ═══════════════════════════════════════════════

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


@pytest.mark.parametrize("old_shape", [False, True])
def test_g3_a_replayed_older_main_event_cannot_roll_back_the_parked_newer_one(shop, old_shape):
    """Same verified signer, OLDER content: the main changed the owner's password
    twice while this receiver did not know the main; an insider replays the FIRST
    (captured from the relay) after the SECOND is parked. old_shape=False is the
    keyed-table control."""
    if old_shape:
        _to_old_shape(shop)
    _drop_main(shop)
    first = shop.world.sign(shop.user_event(OWNER, password_hash="FIRST", row_version=2), shop.M)
    second = shop.world.sign(shop.user_event(OWNER, password_hash="SECOND", row_version=3), shop.M)
    assert raw_apply(shop, second) is False
    raw_apply(shop, first)                               # the replay
    _put_main(shop)
    shop.pull([])
    assert shop.user(OWNER)["password_hash"] == "SECOND"


@pytest.mark.parametrize("old_shape", [False, True])
def test_g3_junk_of_another_entity_type_with_the_same_id_cannot_overwrite_a_verified_row(shop, old_shape):
    """The old-shape primary key is (entity_id, event_type) -- no entity_type -- and
    the G3 verifier re-verifies the PARKED row under the INCOMING event's identity.
    A main-signed device rename parked as main_unknown, then junk sent as a `user`
    event whose entity_id is that device id: the parked row does not verify as a
    `user`, so the guard steps aside and the junk replaces it."""
    if old_shape:
        _to_old_shape(shop)
    _drop_main(shop)
    rename = shop.world.sign({"entity_type": "device", "entity_id": shop.B.dev, "event_type": "update",
                              "payload": {"device_id": shop.B.dev, "device_label": "Back Office",
                                          "platform": "WINDOWS", "status": "active", "row_version": 2,
                                          "admin_row_version": 2, "updated_at_utc": "2026-02-01T00:00:00+00:00",
                                          "app_version": "9.9.9", "last_seen_at": "2026-02-01T00:00:00+00:00"}},
                             shop.M)
    assert raw_apply(shop, rename) is False
    junk = shop.world.sign(shop.user_event(CASHIER, display_name="junk"), shop.A)
    junk["entity_id"] = shop.B.dev
    junk["payload"]["display_name"] = "altered"           # bad_signature
    raw_apply(shop, junk)
    _put_main(shop)
    shop.pull([])
    assert shop.q("SELECT device_label FROM devices WHERE id=?", (shop.B.dev,))[0]["device_label"] == "Back Office"


def test_g3_old_shape_unverifiable_copy_of_a_verified_row_cannot_overwrite_it(shop):
    _to_old_shape(shop)
    _drop_main(shop)
    legit = shop.world.sign(shop.user_event(OWNER, password_hash="LEGIT"), shop.M)
    assert raw_apply(shop, legit) is False
    junk = copy.deepcopy(legit)
    junk["payload"]["password_hash"] = "JUNK"
    raw_apply(shop, junk)
    stored = json.loads(shop.q("SELECT payload FROM sync_apply_quarantine")[0]["payload"])
    assert stored["password_hash"] == "LEGIT"


# ═══ 6. G4: company scoping ══════════════════════════════════════════════════

def _insert_other_company_user(shop, uid, role):
    conn = shop.get_conn()
    conn.execute(
        "INSERT INTO users (id, company_id, employee_id, email, password_hash, role, status, "
        "require_password_change, session_version, language, uid, pin_hash, row_version, "
        "updated_at_utc, display_name) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (str(uuid.uuid4()), "company-other", f"E-{uid}", f"{uid}@x.com", "HASH-OTHER", role,
         "active", 0, 1, "en", uid, "PIN-O", 1, "2026-01-01T00:00:00+00:00", "Other"))
    conn.commit()
    conn.close()


def _foreign_event(uid, **over):
    payload = {"uid": uid, "employee_id": f"E-{uid}", "email": f"{uid}@x.com", "role": "admin",
               "status": "active", "language": "en", "password_hash": "HASH-OTHER", "pin_hash": "PIN-O",
               "session_version": 1, "display_name": "Other", "require_password_change": 0,
               "row_version": 9, "updated_at_utc": "2026-02-01T00:00:00+00:00"}
    payload.update(over)
    return {"entity_type": "user", "entity_id": uid, "event_type": "update", "payload": payload}


def _foreign_row(shop, uid):
    return shop.q("SELECT * FROM users WHERE uid=?", (uid,))[0]


def test_g4_harmless_looking_edit_of_another_companys_owner_writes_nothing(shop):
    _insert_other_company_user(shop, "other-owner", "admin")
    before = _foreign_row(shop, "other-owner")
    assert shop.apply(_foreign_event("other-owner", display_name="hijacked", row_version=1), shop.A) is False
    assert _foreign_row(shop, "other-owner") == before
    # (parked by the rule table as second_admin_refused before G4 is reached: a
    # foreign uid is never in this company's admin set, so the harmless path that
    # writes display_name/language is unreachable for it)
    assert len(shop.parked("user")) == 1


def test_g4_even_this_shops_main_cannot_write_another_companys_user(shop):
    _insert_other_company_user(shop, "other-owner", "admin")
    before = _foreign_row(shop, "other-owner")
    assert shop.apply(_foreign_event("other-owner", password_hash="MAIN-SAYS"), shop.M) is False
    assert _foreign_row(shop, "other-owner") == before


def test_g4_no_local_company_id_still_never_writes_another_companys_user(shop):
    _insert_other_company_user(shop, "other-cashier", "cashier")
    before = _foreign_row(shop, "other-cashier")
    raw_apply(shop, shop.world.sign(_foreign_event("other-cashier", role="cashier", password_hash="PWN"), shop.A),
              company=None)
    assert _foreign_row(shop, "other-cashier") == before


def test_g4_a_uid_in_no_company_cannot_be_minted_as_an_owner_by_a_member(shop):
    before = shop.counts()
    ev = _foreign_event("brand-new-owner")
    ev["event_type"] = "create"
    assert shop.apply(ev, shop.A) is False
    assert shop.counts() == before


def test_g4_a_uid_in_no_company_is_created_in_this_company_as_staff(shop):
    ev = _foreign_event("brand-new-cashier", role="cashier", row_version=1)
    ev["event_type"] = "create"
    assert shop.apply(ev, shop.A) is True
    assert _foreign_row(shop, "brand-new-cashier")["company_id"] == COMPANY


def _perm(uid, level="full", subsystem="retail.sales", event_type="create"):
    return {"entity_type": "user_permission", "entity_id": str(uuid.uuid4()), "event_type": event_type,
            "payload": {"user_uid": uid, "subsystem": subsystem, "access_level": level}}


def _perms_of(shop, uid):
    return shop.q("SELECT up.subsystem, up.access_level FROM user_permissions up JOIN users u "
                  "ON u.id = up.user_id WHERE u.uid=? ORDER BY up.subsystem", (uid,))


def test_g4_sibling_a_member_cannot_grant_permissions_to_another_companys_user(shop):
    """G4's twin on `user_permission`: the rule protects only THIS company's owners
    (`_local_admin_uids` is company-scoped) and the applier resolves the uid with
    no company filter (`_local_id_by_uid(conn, "users", uid)`)."""
    _insert_other_company_user(shop, "other-cashier", "cashier")
    before = _perms_of(shop, "other-cashier")
    shop.apply(_perm("other-cashier", "full", "cash.approve"), shop.A)
    assert _perms_of(shop, "other-cashier") == before


def test_g4_sibling_a_member_cannot_revoke_another_companys_owner_permissions(shop):
    _insert_other_company_user(shop, "other-owner", "admin")
    conn = shop.get_conn()
    uid_row = conn.execute("SELECT id FROM users WHERE uid='other-owner'").fetchone()
    conn.execute("INSERT INTO user_permissions (id, user_id, subsystem, access_level) VALUES (?,?,?,?)",
                 (str(uuid.uuid4()), uid_row[0], "retail.sales", "full"))
    conn.commit()
    conn.close()
    before = _perms_of(shop, "other-owner")
    shop.apply(_perm("other-owner", subsystem="retail.sales", event_type="delete"), shop.A)
    assert _perms_of(shop, "other-owner") == before


def test_g4_sibling_allow_half_this_companys_staff_permissions_still_apply(shop):
    assert shop.apply(_perm(CASHIER, "full", "retail.sales"), shop.A) is True
    assert _perms_of(shop, CASHIER) == [{"subsystem": "retail.sales", "access_level": "full"}]
