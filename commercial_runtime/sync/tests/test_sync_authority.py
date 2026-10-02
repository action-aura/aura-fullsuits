"""AUDIT-033 / S-AUTH, D1 + D2 -- the signature, the lease check, the binding.

Pure verification (`authority.verify_event`) with real Ed25519 keys and real
Owner-signed leases (see _signing.py), then the signer-binding table in a REAL
registry.db at v10.

Every guard here was MUTATION-PROVEN (see the crew's progress.md for the red /
green lines): break the guard, watch exactly these tests go red, restore.

Run (ONE FILE PER PYTEST PROCESS):
    pytest commercial_runtime/sync/tests/test_sync_authority.py -q
"""
from __future__ import annotations

import base64
import json
import sqlite3
import sys
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from commercial_runtime.identity import registry_db
from commercial_runtime.sync import authority
from commercial_runtime.sync.tests._signing import LICENCE, SigningWorld, make_lease

COMPANY = "company-auth"


@pytest.fixture
def world(tmp_path):
    return SigningWorld(tmp_path)


def _event(entity_type="user", payload=None, **over):
    ev = {"id": str(uuid.uuid4()), "entity_type": entity_type, "entity_id": str(uuid.uuid4()),
          "event_type": "update", "created_at": "2026-10-01T00:00:00Z",
          "payload": payload if payload is not None else {"uid": "u1", "role": "cashier", "row_version": 2}}
    ev.update(over)
    return ev


def _verify(world, ev, **kw):
    return authority.verify_event(ev, trust_store=kw.get("trust_store", world.trust_store),
                                  own_licence=kw.get("own_licence", LICENCE))


# ── which events are authority events ───────────────────────────────────────

@pytest.mark.parametrize("entity_type", ["user", "user_permission", "device", "admin_device",
                                         "till_allocation", "doc_series", "branch_alias"])
def test_authority_types_are_authority_events(entity_type):
    assert authority.is_authority_event({"entity_type": entity_type, "payload": {}}) is True


def test_only_release_acks_are_authority_among_retail_settings():
    ack = {"entity_type": "retail_setting", "payload": {"skey": "till_release_ack:abc"}}
    other = {"entity_type": "retail_setting", "payload": {"skey": "currency"}}
    assert authority.is_authority_event(ack) is True
    assert authority.is_authority_event(other) is False


@pytest.mark.parametrize("entity_type", ["sale", "product", "category", "inventory_movement", "gl_journal"])
def test_business_events_are_not_authority_events(entity_type):
    assert authority.is_authority_event({"entity_type": entity_type, "payload": {}}) is False


def test_garbage_is_not_an_authority_event():
    assert authority.is_authority_event(None) is False
    assert authority.is_authority_event({"entity_type": ["user"]}) is False


# ── sign / verify ───────────────────────────────────────────────────────────

def test_round_trip_derives_the_signer_from_the_signature(world):
    dev = world.new_device()
    signed = world.sign(_event(), dev)
    verdict = _verify(world, signed)
    assert isinstance(verdict, authority.VerifiedSigner)
    assert (verdict.dev, verdict.inst, verdict.fingerprint) == (dev.dev, dev.inst, dev.fingerprint)


# ── the registry device id must be attested by the Owner-signed lease ────────
# AUDIT-033 fix round (verifier V finding #1): `_auth.dev` is a field the signer
# chooses, so the Owner-pinned `registry_device_id` in the lease must equal it.

def test_a_lease_that_attests_nothing_cannot_vouch_for_any_device_id(world):
    dev = world.new_device(attest=False)
    verdict = _verify(world, world.sign(_event(), dev))
    assert isinstance(verdict, authority.Refusal) and verdict.reason == authority.REASON_DEV_NOT_ATTESTED


def test_a_lease_that_attests_another_id_cannot_vouch_for_this_one(world):
    insider = world.new_device("dev-insider", attest="dev-the-main")
    verdict = _verify(world, world.sign(_event(), insider))     # signs as dev-insider, lease names dev-the-main
    assert isinstance(verdict, authority.Refusal) and verdict.reason == authority.REASON_DEV_NOT_ATTESTED
    assert "dev-the-main" in verdict.detail and "dev-insider" in verdict.detail


def test_a_lease_attesting_an_empty_string_is_not_an_attestation(world):
    dev = world.new_device("dev-x", attest="")
    # attest="" builds a lease WITHOUT the key (make_lease adds it only when truthy-given)
    assert "registry_device_id" not in dev.signer.lease["payload"]
    verdict = _verify(world, world.sign(_event(), dev))
    assert isinstance(verdict, authority.Refusal) and verdict.reason == authority.REASON_DEV_NOT_ATTESTED


def test_an_attested_event_verifies_and_carries_the_lease_issue_time_and_signature_id(world):
    """The allow half: the attested id is accepted, and the verdict exposes what
    the binding and liveness rules need."""
    dev = world.new_device("dev-attested")
    signed = world.sign(_event(), dev)
    verdict = _verify(world, signed)
    assert isinstance(verdict, authority.VerifiedSigner) and verdict.dev == "dev-attested"
    assert verdict.lease_issued_at == datetime.fromisoformat(dev.signer.lease["payload"]["issued_at"])
    import hashlib
    # Review V3r G1: the liveness id is the hash of the DECODED 64 signature bytes.
    # This used to pin sha256 of the base64 TEXT, which has 16 spellings; this
    # assertion alone can no longer tell a regression back to hashing the text from
    # the right answer for a canonical signature (they differ, but only the
    # re-spelling tests in test_sync_authority_review3.py -- and the unit pin in
    # test_sync_authority_review3_fixes.py -- exercise a NON-canonical text).
    assert verdict.sig_sha256 == hashlib.sha256(base64.b64decode(signed["payload"]["_auth"]["sig"])).hexdigest()


def test_signing_does_not_mutate_the_input_and_is_idempotent(world):
    dev = world.new_device()
    ev = _event()
    before = json.dumps(ev, sort_keys=True)
    signed = world.sign(ev, dev)
    assert json.dumps(ev, sort_keys=True) == before
    assert "_auth" in signed["payload"]
    assert world.sign(signed, dev) is signed          # already ours and valid: unchanged


def test_resigning_replaces_a_signature_that_no_longer_verifies(world):
    dev = world.new_device()
    signed = world.sign(_event(), dev)
    tampered = dict(signed, payload=dict(signed["payload"], role="admin"))
    resigned = world.sign(tampered, dev)
    assert resigned is not tampered
    assert isinstance(_verify(world, resigned), authority.VerifiedSigner)


def test_tampered_payload_field_is_refused(world):
    dev = world.new_device()
    signed = world.sign(_event(), dev)
    signed["payload"]["role"] = "admin"
    verdict = _verify(world, signed)
    assert isinstance(verdict, authority.Refusal) and verdict.reason == authority.REASON_BAD_SIGNATURE


@pytest.mark.parametrize("field,value", [("entity_type", "device"), ("event_type", "create"),
                                         ("entity_id", "another-entity")])
def test_tampered_event_envelope_is_refused(world, field, value):
    signed = world.sign(_event(), world.new_device())
    signed[field] = value
    verdict = _verify(world, signed)
    assert isinstance(verdict, authority.Refusal) and verdict.reason == authority.REASON_BAD_SIGNATURE


def test_event_id_and_created_at_are_not_signed(world):
    """Relays re-render both. If either were signed, every relayed event would fail."""
    signed = world.sign(_event(), world.new_device())
    signed["id"] = "re-rendered-by-relay"
    signed["created_at"] = "2030-01-01T00:00:00Z"
    assert isinstance(_verify(world, signed), authority.VerifiedSigner)


def test_entity_id_case_is_normalised(world):
    ev = _event(entity_id="ABC-DEF")
    signed = world.sign(ev, world.new_device())
    signed["entity_id"] = "abc-def"
    assert isinstance(_verify(world, signed), authority.VerifiedSigner)


def test_an_integral_float_and_the_same_int_sign_alike_but_a_real_change_does_not(world):
    """A relay that stores payloads as jsonb or re-serialises them may hand back
    `2` for `2.0`. That hop must not break a signature -- and a genuinely
    different number must still break it."""
    signed = world.sign(_event(payload={"uid": "u1", "epoch": 2.0, "ratio": 1.5}), world.new_device())
    signed["payload"]["epoch"] = 2
    assert isinstance(_verify(world, signed), authority.VerifiedSigner)
    signed["payload"]["ratio"] = 1.6
    assert isinstance(_verify(world, signed), authority.Refusal)


def test_tampered_dev_is_refused(world):
    """Claiming to speak as the main computer by editing `dev` breaks the signature."""
    signed = world.sign(_event(), world.new_device())
    signed["payload"]["_auth"]["dev"] = "the-main-computer"
    verdict = _verify(world, signed)
    assert isinstance(verdict, authority.Refusal) and verdict.reason == authority.REASON_BAD_SIGNATURE


def test_tampered_inst_is_refused_by_the_lease(world):
    signed = world.sign(_event(), world.new_device())
    signed["payload"]["_auth"]["inst"] = "someone-elses-installation"
    verdict = _verify(world, signed)
    assert isinstance(verdict, authority.Refusal) and verdict.reason == authority.REASON_UNTRUSTED_LEASE


def test_a_member_of_another_licence_is_refused(world):
    outsider = world.new_device(licence="lic-some-other-shop")
    verdict = _verify(world, world.sign(_event(), outsider))
    assert isinstance(verdict, authority.Refusal) and verdict.reason == authority.REASON_FOREIGN_LICENCE


def test_a_lease_naming_another_key_is_refused(world):
    """A stolen lease paired with the attacker's own key."""
    victim = world.new_device()
    attacker = world.new_device(lease_fingerprint=victim.fingerprint)
    verdict = _verify(world, world.sign(_event(), attacker))
    assert isinstance(verdict, authority.Refusal) and verdict.reason == authority.REASON_UNTRUSTED_LEASE


def test_a_lease_not_signed_by_a_trusted_owner_is_refused(world):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    forged = world.new_device(owner_key=Ed25519PrivateKey.generate())
    verdict = _verify(world, world.sign(_event(), forged))
    assert isinstance(verdict, authority.Refusal) and verdict.reason == authority.REASON_UNTRUSTED_LEASE


def test_an_expired_lease_still_proves_identity(world):
    """Expiry is entitlement, not identity: an event signed under a lease that has
    since lapsed (a till pulling after a week offline) must still verify."""
    old = world.new_device(issued_at=datetime.now(timezone.utc) - timedelta(days=10))
    assert datetime.fromisoformat(old.signer.lease["payload"]["expires_at"]) < datetime.now(timezone.utc)
    assert isinstance(_verify(world, world.sign(_event(), old)), authority.VerifiedSigner)


def test_a_not_yet_valid_lease_is_still_judged_at_its_own_issue_time(world):
    future = world.new_device(issued_at=datetime.now(timezone.utc) + timedelta(days=3))
    assert isinstance(_verify(world, world.sign(_event(), future)), authority.VerifiedSigner)


def test_sender_schema_version_added_after_signing_does_not_break_verification(world):
    signed = world.sign(_event(), world.new_device())
    signed["payload"]["_sender_schema_version"] = 43
    assert isinstance(_verify(world, signed), authority.VerifiedSigner)


def test_unsigned_authority_event_is_refused_as_unsigned(world):
    verdict = _verify(world, _event())
    assert isinstance(verdict, authority.Refusal) and verdict.reason == authority.REASON_UNSIGNED


@pytest.mark.parametrize("mangle", [
    lambda a: a.update(v=2),
    lambda a: a.update(pk="not base64!!"),
    lambda a: a.update(pk=base64.b64encode(b"short").decode()),
    lambda a: a.update(sig="@@@@"),
    lambda a: a.pop("lease"),
    lambda a: a.update(lease="a string"),
])
def test_malformed_auth_blocks_are_refused_never_raised(world, mangle):
    signed = world.sign(_event(), world.new_device())
    mangle(signed["payload"]["_auth"])
    verdict = _verify(world, signed)
    assert isinstance(verdict, authority.Refusal)
    assert verdict.reason in (authority.REASON_BAD_SIGNATURE, authority.REASON_UNTRUSTED_LEASE)


def test_no_trust_store_or_no_own_licence_parks_instead_of_trusting(world):
    signed = world.sign(_event(), world.new_device())
    assert _verify(world, signed, trust_store=None).reason == authority.REASON_UNTRUSTED_LEASE
    assert _verify(world, signed, own_licence="").reason == authority.REASON_NO_LOCAL_LICENCE


def test_a_revoked_owner_key_stops_verifying_even_if_cached(world):
    """The lease cache must not outlive the trust store's decision."""
    dev = world.new_device()
    signed = world.sign(_event(), dev)
    assert isinstance(_verify(world, signed), authority.VerifiedSigner)       # primes the cache
    world.trust_store.revoke_locally("owner-1")
    verdict = _verify(world, signed)
    assert isinstance(verdict, authority.Refusal) and verdict.reason == authority.REASON_UNTRUSTED_LEASE


def test_prepare_only_mode_gives_an_unsigned_auth_and_the_bytes_kotlin_signs(world):
    """Android: Python builds `_auth` + the tbs bytes, Kotlin signs, the result verifies."""
    phone = world.new_device(prepare_only=True)
    ev = _event()
    assert world.sign(ev, phone)["payload"]["_auth"].get("sig") is None
    prepared, tbs = authority.prepare_unsigned_auth(ev, phone.signer)
    assert "sig" not in prepared["payload"]["_auth"]
    assert isinstance(_verify(world, prepared), authority.Refusal)             # no signature yet
    prepared["payload"]["_auth"]["sig"] = base64.b64encode(phone.key.sign(tbs)).decode()
    assert isinstance(_verify(world, prepared), authority.VerifiedSigner)


def test_strip_auth_drops_only_the_auth_block():
    assert authority.strip_auth({"a": 1, "_auth": {"x": 1}, "_sender_schema_version": 3}) == \
        {"a": 1, "_sender_schema_version": 3}


# ── signer binding (registry v10) ───────────────────────────────────────────

@pytest.fixture
def reg(tmp_path, monkeypatch):
    db_dir = tmp_path / "database"
    monkeypatch.setattr(registry_db, "_db_dir", str(db_dir))
    monkeypatch.setattr(registry_db, "DB_PATH", str(db_dir / "registry.db"))
    registry_db.init_registry_db()
    conn = registry_db.get_conn()
    yield conn
    conn.close()


def _signer(dev, inst, fp="f" * 64):
    return authority.VerifiedSigner(dev=dev, inst=inst, fingerprint=fp, licence=LICENCE)


def test_registry_is_at_v10_and_has_the_table(reg):
    assert registry_db.REGISTRY_SCHEMA_VERSION == 10
    assert reg.execute("PRAGMA user_version").fetchone()[0] == 10
    cols = [r[1] for r in reg.execute("PRAGMA table_info(sync_signers)").fetchall()]
    # AUDIT-033 fix round: the unreleased v10 grew `lease_issued_at_utc` (newest
    # Owner lease wins a rebind). The assertion is the same exact-list check, so a
    # column added or dropped without a decision still fails here.
    assert cols == ["device_id", "company_id", "installation_id", "key_fingerprint",
                    "first_bound_at_utc", "last_signed_seen_at_utc", "lease_issued_at_utc"]


def test_first_binding_wins_and_is_recorded(reg):
    assert authority.bind_signer(reg, COMPANY, _signer("dev-a", "inst-a")) is None
    row = reg.execute("SELECT installation_id, key_fingerprint FROM sync_signers WHERE device_id='dev-a'").fetchone()
    assert tuple(row) == ("inst-a", "f" * 64)


def test_the_same_signer_again_is_fine_and_binding_never_refreshes_last_seen(reg):
    """REPLACES `..._and_refreshes_last_seen` (AUDIT-033 fix round, finding #6).
    That test pinned that merely binding an already-bound signer refreshed
    `last_signed_seen_at_utc`; refreshing on a bare binding is exactly the replay
    hole, so the intended behaviour is now the opposite and the refresh moved to
    `record_liveness` (tested below). What this test can no longer catch is a
    binding that forgets to refresh -- by design."""
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    authority.bind_signer(reg, COMPANY, _signer("dev-a", "inst-a"), now=t0)
    assert authority.bind_signer(reg, COMPANY, _signer("dev-a", "inst-a"), now=t0 + timedelta(days=3)) is None
    first, last = reg.execute(
        "SELECT first_bound_at_utc, last_signed_seen_at_utc FROM sync_signers").fetchone()
    assert first.startswith("2026-01-01")
    assert last.startswith("1970-01-01"), f"binding is not proof of life, yet last_seen={last}"


def test_a_peer_binding_starts_with_the_epoch_sentinel_but_a_self_bind_stamps_now(reg, world):
    me = world.new_device("dev-me")
    now = datetime(2026, 3, 1, tzinfo=timezone.utc)
    authority.ensure_self_bound(reg, COMPANY, me.signer, now=now)
    authority.bind_signer(reg, COMPANY, _signer("dev-peer", "inst-peer"), now=now)
    seen = dict(reg.execute("SELECT device_id, last_signed_seen_at_utc FROM sync_signers").fetchall())
    assert seen["dev-me"].startswith("2026-03-01") and seen["dev-peer"].startswith("1970-01-01")
    # a returning main must not make itself look alive just by booting again
    authority.ensure_self_bound(reg, COMPANY, me.signer, now=now + timedelta(days=30))
    assert reg.execute("SELECT last_signed_seen_at_utc FROM sync_signers WHERE device_id='dev-me'"
                       ).fetchone()[0].startswith("2026-03-01")


def _lease_signer(dev, inst, issued, fp="f" * 64):
    return authority.VerifiedSigner(dev=dev, inst=inst, fingerprint=fp, licence=LICENCE,
                                    lease_issued_at=issued)


def test_a_newer_owner_lease_moves_a_device_id_to_its_new_installation_and_resets_liveness(reg):
    """Owner re-pinned the id (staff deactivated the squatter): the newest lease
    naming it wins, and the new holder has not been heard yet."""
    t0 = datetime(2026, 5, 1, tzinfo=timezone.utc)
    authority.bind_signer(reg, COMPANY, _lease_signer("dev-a", "inst-squatter", t0), now=t0)
    authority.record_liveness(reg, authority.VerifiedSigner(
        dev="dev-a", inst="inst-squatter", fingerprint="f" * 64, licence=LICENCE,
        lease_issued_at=t0, sig_sha256="s1"), now=t0 + timedelta(hours=1))
    assert authority.bind_signer(reg, COMPANY, _lease_signer("dev-a", "inst-real", t0 + timedelta(days=2)),
                                 now=t0 + timedelta(days=2)) is None
    row = reg.execute("SELECT installation_id, first_bound_at_utc, last_signed_seen_at_utc, lease_issued_at_utc "
                      "FROM sync_signers WHERE device_id='dev-a'").fetchone()
    assert row[0] == "inst-real"
    assert row[1].startswith("2026-05-03") and row[2].startswith("1970-01-01") and row[3].startswith("2026-05-03")


@pytest.mark.parametrize("offset_days", [0, -3])
def test_an_older_or_equal_lease_cannot_take_a_bound_device_id_back(reg, offset_days):
    t0 = datetime(2026, 5, 10, tzinfo=timezone.utc)
    authority.bind_signer(reg, COMPANY, _lease_signer("dev-a", "inst-first", t0), now=t0)
    clash = authority.bind_signer(reg, COMPANY, _lease_signer("dev-a", "inst-other", t0 + timedelta(days=offset_days)))
    assert clash is not None and clash.reason == authority.REASON_BINDING_CLASH
    assert reg.execute("SELECT installation_id FROM sync_signers WHERE device_id='dev-a'").fetchone()[0] == "inst-first"


def test_a_signer_with_no_lease_time_can_never_rebind(reg):
    authority.bind_signer(reg, COMPANY, _lease_signer("dev-a", "inst-first", datetime(2026, 5, 10, tzinfo=timezone.utc)))
    clash = authority.bind_signer(reg, COMPANY, _signer("dev-a", "inst-other"))
    assert clash is not None and clash.reason == authority.REASON_BINDING_CLASH


def test_a_row_bound_before_the_lease_stamp_existed_can_be_rebound_by_a_newer_lease(reg):
    """A row migrated from before the fix has `lease_issued_at_utc` NULL."""
    reg.execute("INSERT INTO sync_signers (device_id, company_id, installation_id, key_fingerprint, "
                "first_bound_at_utc, last_signed_seen_at_utc) VALUES ('dev-a', ?, 'inst-old', ?, ?, ?)",
                (COMPANY, "f" * 64, "2026-01-01T00:00:00.000000+00:00", "2026-01-01T00:00:00.000000+00:00"))
    assert authority.bind_signer(reg, COMPANY, _lease_signer("dev-a", "inst-new", datetime(2026, 6, 1, tzinfo=timezone.utc))) is None
    assert reg.execute("SELECT installation_id FROM sync_signers WHERE device_id='dev-a'").fetchone()[0] == "inst-new"


def test_an_installation_bound_to_one_device_cannot_claim_another_even_with_a_newer_lease(reg):
    """Owner never re-pins an installation, so a second id for it is not an Owner fact."""
    t0 = datetime(2026, 5, 1, tzinfo=timezone.utc)
    authority.bind_signer(reg, COMPANY, _lease_signer("dev-x", "inst-a", t0))
    clash = authority.bind_signer(reg, COMPANY, _lease_signer("dev-y", "inst-a", t0 + timedelta(days=9)))
    assert clash is not None and clash.reason == authority.REASON_BINDING_CLASH
    assert reg.execute("SELECT COUNT(*) FROM sync_signers").fetchone()[0] == 1


def test_key_rotation_under_the_same_installation_updates_the_fingerprint_and_only_moves_the_lease_forward(reg):
    t0 = datetime(2026, 5, 1, tzinfo=timezone.utc)
    authority.bind_signer(reg, COMPANY, _lease_signer("dev-a", "inst-a", t0, "1" * 64))
    authority.bind_signer(reg, COMPANY, _lease_signer("dev-a", "inst-a", t0 + timedelta(days=4), "2" * 64))
    authority.bind_signer(reg, COMPANY, _lease_signer("dev-a", "inst-a", t0 + timedelta(days=1), "3" * 64))
    fp, lease = reg.execute("SELECT key_fingerprint, lease_issued_at_utc FROM sync_signers").fetchone()
    assert fp == "3" * 64                         # the fingerprint always follows the verified key
    assert lease.startswith("2026-05-05"), lease   # but an OLDER lease never moves the stamp back


# ── liveness: record_liveness is the only thing that refreshes last_seen ──────

def _live(dev="dev-a", inst="inst-a", sig="sig-1", issued=None):
    return authority.VerifiedSigner(dev=dev, inst=inst, fingerprint="f" * 64, licence=LICENCE,
                                    lease_issued_at=issued, sig_sha256=sig)


def _last_seen(reg, dev="dev-a"):
    return reg.execute("SELECT last_signed_seen_at_utc FROM sync_signers WHERE device_id=?", (dev,)).fetchone()[0]


def test_a_new_signature_under_a_recent_lease_refreshes_liveness_once(reg):
    now = datetime(2026, 7, 1, tzinfo=timezone.utc)
    issued = now - timedelta(hours=2)
    authority.bind_signer(reg, COMPANY, _live(issued=issued), now=now - timedelta(days=9))
    assert authority.record_liveness(reg, _live(issued=issued), now=now) is True
    assert _last_seen(reg).startswith("2026-07-01")
    later = now + timedelta(days=3)
    assert authority.record_liveness(reg, _live(issued=issued), now=later) is False       # the same signature
    assert _last_seen(reg).startswith("2026-07-01")
    assert authority.record_liveness(reg, _live(sig="sig-2", issued=issued), now=later) is True   # a new one
    assert _last_seen(reg).startswith("2026-07-04")


@pytest.mark.parametrize("issued_days_ago,expected", [(13, True), (15, False), (20, False)])
def test_a_lease_older_than_the_window_is_not_proof_of_life(reg, issued_days_ago, expected):
    now = datetime(2026, 7, 1, tzinfo=timezone.utc)
    issued = now - timedelta(days=issued_days_ago)
    authority.bind_signer(reg, COMPANY, _live(issued=issued), now=now)
    assert authority.record_liveness(reg, _live(issued=issued), now=now) is expected
    assert _last_seen(reg).startswith("2026-07-01" if expected else "1970-01-01")


def test_no_lease_time_or_no_signature_id_is_never_proof_of_life(reg):
    now = datetime(2026, 7, 1, tzinfo=timezone.utc)
    authority.bind_signer(reg, COMPANY, _live(issued=now), now=now)
    assert authority.record_liveness(reg, _live(issued=None), now=now) is False
    assert authority.record_liveness(reg, _live(sig="", issued=now), now=now) is False
    assert _last_seen(reg).startswith("1970-01-01")


def test_liveness_for_an_unbound_signer_is_recorded_but_moves_nothing(reg):
    now = datetime(2026, 7, 1, tzinfo=timezone.utc)
    assert authority.record_liveness(reg, _live(issued=now), now=now) is True     # signature counted ...
    assert reg.execute("SELECT COUNT(*) FROM sync_signers").fetchone()[0] == 0    # ... no row invented


def test_old_seen_signatures_are_pruned_so_the_table_stays_bounded(reg):
    now = datetime(2026, 7, 1, tzinfo=timezone.utc)
    authority.bind_signer(reg, COMPANY, _live(issued=now - timedelta(days=14)), now=now)
    old_issued = now - timedelta(days=14)
    authority.record_liveness(reg, _live(sig="old", issued=old_issued), now=now)
    # 2 days later the same signature's lease is 16 days old: past the 15-day prune bound
    authority.record_liveness(reg, _live(sig="new", issued=now + timedelta(days=1)), now=now + timedelta(days=2))
    ids = {r[0] for r in reg.execute("SELECT sig_sha256 FROM sync_signed_seen").fetchall()}
    assert ids == {"new"}, ids


def test_a_second_installation_cannot_speak_as_a_bound_device(reg):
    authority.bind_signer(reg, COMPANY, _signer("dev-a", "inst-a"))
    clash = authority.bind_signer(reg, COMPANY, _signer("dev-a", "inst-evil"))
    assert clash is not None and clash.reason == authority.REASON_BINDING_CLASH
    assert reg.execute("SELECT installation_id FROM sync_signers WHERE device_id='dev-a'").fetchone()[0] == "inst-a"


def test_a_bound_installation_cannot_also_speak_as_another_device(reg):
    authority.bind_signer(reg, COMPANY, _signer("dev-a", "inst-a"))
    clash = authority.bind_signer(reg, COMPANY, _signer("dev-b", "inst-a"))
    assert clash is not None and clash.reason == authority.REASON_BINDING_CLASH
    assert reg.execute("SELECT COUNT(*) FROM sync_signers").fetchone()[0] == 1


def test_key_rotation_for_the_same_installation_updates_the_fingerprint(reg):
    authority.bind_signer(reg, COMPANY, _signer("dev-a", "inst-a", "1" * 64))
    assert authority.bind_signer(reg, COMPANY, _signer("dev-a", "inst-a", "2" * 64)) is None
    assert reg.execute("SELECT key_fingerprint FROM sync_signers WHERE device_id='dev-a'").fetchone()[0] == "2" * 64


def test_self_pre_bind_stops_a_peer_claiming_this_devices_id(reg, world):
    me = world.new_device()
    assert authority.ensure_self_bound(reg, COMPANY, me.signer) is True
    assert authority.ensure_self_bound(reg, COMPANY, me.signer) is True         # idempotent
    clash = authority.bind_signer(reg, COMPANY, _signer(me.dev, "inst-attacker"))
    assert clash is not None and clash.reason == authority.REASON_BINDING_CLASH


def test_binding_table_survives_a_registry_without_the_migration(tmp_path):
    """A hand-built registry (no sync_signers) must not raise out of the apply loop."""
    conn = sqlite3.connect(str(tmp_path / "bare.db"))
    assert authority.bind_signer(conn, COMPANY, _signer("dev-a", "inst-a")) is None
    conn.close()


# ── local_signer_or_none (the real DPAPI path) ──────────────────────────────

@pytest.mark.skipif(sys.platform != "win32", reason="DPAPI is Windows-only")
class TestLocalSigner:
    def _build(self, tmp_path, world, *, lease_for_other_key=False, with_lease=True, with_device=True,
               attest="dev-real"):
        # `attest` (AUDIT-033 fix round): what Owner pinned into the lease. The
        # default names the local device id, i.e. a normal, pinned computer; None
        # models an un-upgraded one. Every pre-existing case keeps meaning what it
        # meant because it builds the normal, attested computer.
        from commercial_runtime.licensing_contracts.device_identity import (
            WindowsDpapiDeviceIdentityProvider, fingerprint_of)
        from commercial_runtime.licensing_contracts.state_machine import LicenseState as LS
        from commercial_runtime.licensing_contracts.state_repository import (
            LICENSING_SCHEMA_VERSION, LicenseStateRecord, LicenseStateRepository)
        app = tmp_path / "app"
        provider = WindowsDpapiDeviceIdentityProvider(app / "licensing")
        provider.generate_new_key()
        fp = fingerprint_of(base64.b64decode(provider.get_public_key_b64()))
        if with_device:
            (app / "device").mkdir(parents=True)
            (app / "device" / "local_device.json").write_text(json.dumps({"device_uuid": "dev-real"}))
        if with_lease:
            lease = make_lease(world.owner_key, inst="inst-real",
                               fingerprint=("0" * 64) if lease_for_other_key else fp,
                               registry_device_id=attest)
            repo = LicenseStateRepository(app / "database" / "subsystems" / "licensing.db")
            repo.save(LicenseStateRecord(
                licensing_schema_version=LICENSING_SCHEMA_VERSION, product_code="AURA_RETAIL",
                platform="WINDOWS", current_state=LS.ACTIVE_ONLINE.value,
                assertion_envelope_json=json.dumps(lease)))
        return app

    def test_builds_a_signer_whose_events_verify(self, tmp_path, world):
        app = self._build(tmp_path, world)
        signer = authority.local_signer_or_none(app)
        assert signer is not None and (signer.dev, signer.inst, signer.licence) == ("dev-real", "inst-real", LICENCE)
        assert isinstance(_verify(world, authority.sign_event(_event(), signer)), authority.VerifiedSigner)

    def test_prepare_only_signer_has_no_sign_fn(self, tmp_path, world):
        """The Android layout: Kotlin wrote the public key file, Python never holds the key."""
        app = self._build(tmp_path, world)
        pk = (app / "licensing")
        from commercial_runtime.licensing_contracts.device_identity import WindowsDpapiDeviceIdentityProvider
        (pk / "device_public_key.txt").write_text(WindowsDpapiDeviceIdentityProvider(pk).get_public_key_b64())
        signer = authority.local_signer_or_none(app, prepare_only=True)
        assert signer is not None and signer.sign_fn is None
        assert "sig" not in authority.sign_event(_event(), signer)["payload"]["_auth"]

    def test_prepare_only_without_a_kotlin_key_file_is_none(self, tmp_path, world):
        assert authority.local_signer_or_none(self._build(tmp_path, world), prepare_only=True) is None

    @pytest.mark.parametrize("kw", [{"with_lease": False}, {"lease_for_other_key": True}, {"with_device": False}])
    def test_none_when_it_cannot_sign(self, tmp_path, world, kw):
        assert authority.local_signer_or_none(self._build(tmp_path, world, **kw)) is None

    def test_none_for_an_empty_directory(self, tmp_path):
        assert authority.local_signer_or_none(tmp_path / "nothing") is None

    @pytest.mark.parametrize("attest", [None, "dev-someone-else"])
    def test_none_when_the_lease_does_not_attest_this_computers_id(self, tmp_path, world, attest, caplog):
        """AUDIT-033 fix round (finding #1): a lease that names nothing, or another
        id, cannot be used to sign -- every receiver would refuse the event, so the
        computer sends it unsigned and says why once."""
        import logging
        authority._UNATTESTED_WARNED.clear()
        app = self._build(tmp_path, world, attest=attest)
        with caplog.at_level(logging.WARNING, logger=authority.logger.name):
            assert authority.local_signer_or_none(app) is None
            assert authority.local_signer_or_none(app) is None
        warnings = [r for r in caplog.records if "does not attest" in r.getMessage()]
        assert len(warnings) == 1, [r.getMessage() for r in caplog.records]

    def test_a_signer_is_built_when_the_lease_attests_this_computers_id(self, tmp_path, world):
        app = self._build(tmp_path, world, attest="dev-real")
        signer = authority.local_signer_or_none(app)
        assert signer is not None and signer.dev == "dev-real"
        assert isinstance(_verify(world, authority.sign_event(_event(), signer)), authority.VerifiedSigner)
