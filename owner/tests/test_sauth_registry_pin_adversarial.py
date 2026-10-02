"""Copied verbatim from review V3o (tmp/sauth/V3o/test_v3o_registry_pin_adversarial.py),
credit to the reviewer. Round-2 deviations (S-AUTH Fix Crew D), each marked
"D-ADAPT" below, are SETUP ONLY, never an assertion: (1) the Q5 race hook patches
registry_pin._holder (renamed from _live_holder, which no longer means "live");
(2) the three tests that expect "the real computer gets the id" now get it the
only way the new contract allows, an explicit staff transfer, because two
different device keys cannot be told apart by Owner; (3) Q4 seeds the shop's
synced history (what a real main leaves behind) and backdates the main.

V3o -- independent adversarial review of AUDIT-033 finding #1, OWNER half.

Every test asserts the SAFE outcome: a failure means a real gap (or, where the
docstring says so, the accepted residual R1 measured as it really is).

Run from owner/ (copied into owner/tests/ of the review worktree so the
conftest fixtures apply), ONE pytest process at a time:
    OWNER_ENV=testing .venv-owner/Scripts/python.exe -m pytest tests/test_v3o_registry_pin_adversarial.py
"""
from __future__ import annotations

import base64
import json
import os
import sys
import threading
import uuid

import pytest

from tests.conftest import (
    build_activation_body, build_checkin_body, make_device_keypair, make_license, make_staff, public_key_b64,
)

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

RA = "11111111-1111-4111-8111-111111111111"   # the MAIN computer's registry id
RB = "22222222-2222-4222-8222-222222222222"   # the insider's own id
RL = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"   # an id with letters (for case / lookalike variants)


# ── helpers (same shape as the builder's file, nothing mocked) ──────────────

def _post(client, path, body):
    return client.post(path, data=json.dumps(body), content_type="application/json")


def _activate(client, key, full_key, label, **extra):
    return _post(client, "/api/licensing/v1/activations",
                 build_activation_body(key, full_key=full_key, installation_id=label, **extra))


def _checkin(client, key, installation_id, **extra):
    return _post(client, "/api/licensing/v1/check-ins", build_checkin_body(key, installation_id=installation_id, **extra))


def _payload(resp):
    assert resp.status_code == 200, (resp.status_code, resp.get_json())
    return resp.get_json()["signed_assertion"]["payload"]


def _iid(resp):
    assert resp.status_code == 200, (resp.status_code, resp.get_json())
    return resp.get_json()["installation_id"]


def _pin(app, installation_id):
    with app.app_context():
        from app.extensions import db_session
        from app.models.installations import Installation

        db_session.expire_all()
        return db_session.get(Installation, uuid.UUID(installation_id)).registry_device_id


def _events(app, installation_id, event_type):
    with app.app_context():
        from sqlalchemy import select

        from app.extensions import db_session
        from app.models.installations import ActivationEvent

        rows = db_session.execute(
            select(ActivationEvent).where(ActivationEvent.installation_id == uuid.UUID(installation_id),
                                          ActivationEvent.event_type == event_type)
        ).scalars().all()
        return [dict(e.safe_metadata or {}) for e in rows]


def _live_holders(app, license_id, rid):
    with app.app_context():
        from sqlalchemy import select

        from app.extensions import db_session
        from app.models.installations import Installation

        db_session.expire_all()
        return [str(i.id) for i in db_session.execute(
            select(Installation).where(Installation.license_id == license_id, Installation.registry_device_id == rid,
                                       Installation.status.not_in(("DEACTIVATED", "REPLACED")))
        ).scalars().all()]


def _with_installation(app, iid, fn):
    with app.app_context():
        from app.extensions import db_session
        from app.models.installations import Installation

        return fn(db_session.get(Installation, uuid.UUID(iid)))


def _seed_history(app, license_id, main_iid, rid, *, days_before_now=2):
    """D-ADAPT helper: a real main leaves its own registry `device` row in the relay
    log; backdate the main's installation so it predates that history (it WAS the
    id's original computer) while a computer activated now does not."""
    from datetime import datetime, timedelta, timezone

    with app.app_context():
        from app.extensions import db_session
        from app.models.installations import Installation
        from app.models.sync import SyncEvent

        now = datetime.now(timezone.utc)
        inst = db_session.get(Installation, uuid.UUID(main_iid))
        inst.created_at = now - timedelta(days=days_before_now + 1)
        db_session.add(SyncEvent(id=uuid.uuid4(), license_id=license_id, device_id=uuid.UUID(main_iid), entity_type="device",
                                 entity_id=uuid.UUID(rid), event_type="create", payload={},
                                 client_created_at=now - timedelta(days=days_before_now)))
        db_session.commit()


def _transfer(app, license_ids, rid, to_iid, actor):
    """D-ADAPT helper: the staff action that is the only way an id moves."""
    with app.app_context():
        from app.extensions import db_session
        from app.licensing_service.registry_pin import transfer_registry_device_id

        transfer_registry_device_id(db_session, registry_device_id=rid, to_installation_id=uuid.UUID(to_iid),
                                    actor_staff_user_id=actor, reason="V3o: the real replacement computer")


@pytest.fixture()
def shop(app, client, seeded, signing_key):
    actor = make_staff(app, f"v3o-{uuid.uuid4().hex[:6]}@example.com")
    license_id, full_key = make_license(app, actor, device_limit=5)
    return {"actor": actor, "license_id": license_id, "full_key": full_key}


# ═══ Q1: can a NEW installation of the same licence get the main's pinned id? ═══

def test_q1_an_unpinned_newer_installation_cannot_take_a_pinned_id_by_check_in(app, client, shop):
    a_key, b_key = make_device_keypair(), make_device_keypair()
    a_iid = _iid(_activate(client, a_key, shop["full_key"], "q1a-a", registry_device_id=RA))
    b_iid = _iid(_activate(client, b_key, shop["full_key"], "q1a-b"))          # B activates WITHOUT an id: unpinned
    assert "registry_device_id" not in _payload(_checkin(client, b_key, b_iid, registry_device_id=RA))
    assert _pin(app, b_iid) is None and _pin(app, a_iid) == RA
    assert len(_events(app, b_iid, "REGISTRY_DEVICE_ID_CLASH")) == 1
    # allow half: B can still pin its OWN id afterwards
    assert _payload(_checkin(client, b_key, b_iid, registry_device_id=RB))["registry_device_id"] == RB


def test_q1_reactivating_the_same_installation_with_the_mains_id_keeps_its_own_pin(app, client, shop):
    a_key, b_key = make_device_keypair(), make_device_keypair()
    _activate(client, a_key, shop["full_key"], "q1b-a", registry_device_id=RA)
    b_iid = _iid(_activate(client, b_key, shop["full_key"], "q1b-b", registry_device_id=RB))
    # same label (installation_label reuse)
    again = _activate(client, b_key, shop["full_key"], "q1b-b", registry_device_id=RA)
    assert _iid(again) == b_iid
    assert _payload(again)["registry_device_id"] == RB
    # fresh label, same device key (fingerprint-fallback reuse)
    fresh = _activate(client, b_key, shop["full_key"], "q1b-b-fresh-label", registry_device_id=RA)
    assert _iid(fresh) == b_iid
    assert _payload(fresh)["registry_device_id"] == RB
    assert _pin(app, b_iid) == RB
    assert _live_holders(app, shop["license_id"], RA) != [] and b_iid not in _live_holders(app, shop["license_id"], RA)


def test_q1_replace_device_key_on_an_unpinned_installation_does_not_open_the_mains_id(app, client, shop):
    a_key, b_key, b_new = make_device_keypair(), make_device_keypair(), make_device_keypair()
    _activate(client, a_key, shop["full_key"], "q1c-a", registry_device_id=RA)
    b_iid = _iid(_activate(client, b_key, shop["full_key"], "q1c-b"))
    with app.app_context():
        from app.licensing_service import device_identity

        device_identity.replace_device_key(device_identity.get_active_device_key(uuid.UUID(b_iid)), "ed25519",
                                           public_key_b64(b_new))
    assert "registry_device_id" not in _payload(_checkin(client, b_new, b_iid, registry_device_id=RA))
    assert _pin(app, b_iid) is None


def test_q1_a_suspended_main_or_a_main_whose_key_staff_revoked_still_holds_its_id(app, client, shop):
    a_key, b_key, c_key = make_device_keypair(), make_device_keypair(), make_device_keypair()
    a_iid = _iid(_activate(client, a_key, shop["full_key"], "q1d-a", registry_device_id=RA))
    from app.installations.services import transition_installation

    _with_installation(app, a_iid, lambda i: transition_installation(i, "SUSPENDED", shop["actor"], reason="t"))
    b = _activate(client, b_key, shop["full_key"], "q1d-b", registry_device_id=RA)
    assert "registry_device_id" not in _payload(b), "a SUSPENDED main must keep its id"
    # staff 'replace-device' (licensing_admin) = revoke the key, installation stays non-terminal
    _with_installation(app, a_iid, lambda i: transition_installation(i, "ACTIVE", shop["actor"], reason="t"))
    with app.app_context():
        from app.licensing_service import device_identity

        device_identity.revoke_device_key(device_identity.get_active_device_key(uuid.UUID(a_iid)))
    c = _activate(client, c_key, shop["full_key"], "q1d-c", registry_device_id=RA)
    assert "registry_device_id" not in _payload(c), "a keyless (revoked) main installation still holds its id"
    assert _live_holders(app, shop["license_id"], RA) == [a_iid]


def test_q1_recycling_your_own_slot_while_the_main_is_pinned_gets_nothing(app, client, shop):
    """Insider deactivates its own installation (device-signed) and comes back as a fresh one claiming RA."""
    a_key, b_key, b2_key = make_device_keypair(), make_device_keypair(), make_device_keypair()
    _activate(client, a_key, shop["full_key"], "q1e-a", registry_device_id=RA)
    b_iid = _iid(_activate(client, b_key, shop["full_key"], "q1e-b", registry_device_id=RB))
    body = build_checkin_body(b_key, installation_id=b_iid, idempotency_key=str(uuid.uuid4()))
    assert _post(client, "/api/licensing/v1/deactivations", body).status_code == 200
    fresh = _activate(client, b2_key, shop["full_key"], "q1e-b2", registry_device_id=RA)
    assert "registry_device_id" not in _payload(fresh)


# ═══ Q2: deactivate / replace / reactivate ══════════════════════════════════

def test_q2_a_released_holder_can_never_come_back_next_to_the_new_holder(app, client, shop):
    a_key, b_key = make_device_keypair(), make_device_keypair()
    a_iid = _iid(_activate(client, a_key, shop["full_key"], "q2a-a", registry_device_id=RA))
    from app.commercial_ops.device_slot_ops import release_device_slot

    _with_installation(app, a_iid, lambda i: release_device_slot(i, reason="moved", actor_staff_user_id=shop["actor"]))
    b_iid = _iid(_activate(client, b_key, shop["full_key"], "q2a-b", registry_device_id=RA))
    assert _pin(app, b_iid) is None          # D-ADAPT: a released holder's id stays reserved (R1)...
    _transfer(app, None, RA, b_iid, shop["actor"])   # ...until staff move it
    assert _pin(app, b_iid) == RA
    # A tries every way back: same label, fresh label (fingerprint), check-in
    for resp in (_activate(client, a_key, shop["full_key"], "q2a-a", registry_device_id=RA),
                 _activate(client, a_key, shop["full_key"], "q2a-a-2", registry_device_id=RA),
                 _checkin(client, a_key, a_iid, registry_device_id=RA)):
        assert resp.status_code in (400, 403, 409), (resp.status_code, resp.get_json())
    assert _live_holders(app, shop["license_id"], RA) == [b_iid]


def test_q2_staff_replacing_the_main_slot_does_not_hand_its_id_to_a_lurking_installation(app, client, shop):
    """Post-release, outside R1 as stated. An insider keeps one installation UNPINNED
    (it simply never sends an id). The main's computer dies; staff replace its slot
    (replace_device_slot -> REPLACED frees RA by design). The lurker's next check-in
    claims RA before the replacement computer (restored data, same RA) activates.
    SAFE outcome: the lurker does not get RA; the real replacement does."""
    a_key, lurk_key, a2_key = make_device_keypair(), make_device_keypair(), make_device_keypair()
    a_iid = _iid(_activate(client, a_key, shop["full_key"], "q2b-a", registry_device_id=RA))
    lurk_iid = _iid(_activate(client, lurk_key, shop["full_key"], "q2b-lurk"))         # never sends an id
    from app.commercial_ops.device_slot_ops import replace_device_slot

    _with_installation(app, a_iid, lambda i: replace_device_slot(i, reason="hardware died", actor_staff_user_id=shop["actor"]))
    lurk = _payload(_checkin(client, lurk_key, lurk_iid, registry_device_id=RA))
    a2_iid = _iid(_activate(client, a2_key, shop["full_key"], "q2b-a2", registry_device_id=RA))
    _transfer(app, None, RA, a2_iid, shop["actor"])      # D-ADAPT: staff hand the id to the replacement
    new_main = _payload(_checkin(client, a2_key, a2_iid, registry_device_id=RA))
    assert lurk.get("registry_device_id") != RA, "the lurker was attested as the main's computer"
    assert new_main.get("registry_device_id") == RA, "the real replacement main was refused its own id"


# ═══ Q3: normalisation ════════════════════════════════════════════════════════

_VARIANTS = {
    "upper": RL.upper(),
    "lead-space": " " + RL,
    "trail-space": RL + " ",
    "trail-newline": RL + "\n",
    "braces": "{" + RL + "}",
    "urn": "urn:uuid:" + RL,
    "no-dashes": RL.replace("-", ""),
    "cyrillic-a": RL.replace("a", "а", 1),
    "fullwidth-dash": RL.replace("-", "－", 1),
    "arabic-indic-digit": RL.replace("4", "٤", 1),
    "zero-width": RL[:8] + "​" + RL[8:],
    "bom": "﻿" + RL,
    "nfkd-fullwidth-a": RL.replace("a", "ａ", 1),
}


@pytest.mark.parametrize("variant", list(_VARIANTS.values()), ids=list(_VARIANTS.keys()))
def test_q3_no_variant_of_a_pinned_id_pins_or_slips_past_the_unique_index(app, client, shop, variant):
    a_key, b_key = make_device_keypair(), make_device_keypair()
    _activate(client, a_key, shop["full_key"], "q3-a", registry_device_id=RL)
    b = _activate(client, b_key, shop["full_key"], "q3-b", registry_device_id=variant)
    assert b.get_json()["result"] == "SUCCESS"
    assert "registry_device_id" not in _payload(b)
    assert _pin(app, _iid(b)) is None
    assert len(_events(app, _iid(b), "REGISTRY_DEVICE_ID_INVALID")) == 1


@pytest.mark.parametrize("variant", list(_VARIANTS.values()), ids=list(_VARIANTS.keys()))
def test_q3_no_variant_pins_even_when_nobody_holds_the_canonical_id(app, client, shop, variant):
    """If Owner pinned a variant V of the main's id X while X is unpinned, the
    main could later ALSO pin X (the index sees two different strings)."""
    b_key = make_device_keypair()
    b = _activate(client, b_key, shop["full_key"], "q3n-b", registry_device_id=variant)
    assert "registry_device_id" not in _payload(b)
    assert _pin(app, _iid(b)) is None


def _real_owner_trust_store(app, tmp_path):
    from app.licensing_service.signing import get_active_signing_key
    from commercial_runtime.licensing_contracts.trust_store import OwnerTrustStore

    with app.app_context():
        k = get_active_signing_key()
        kid, pub = k.key_id, k.public_key
    store = OwnerTrustStore(tmp_path / f"trust-{uuid.uuid4().hex[:6]}.json")
    store.bootstrap_from_anchor({"keys": [{"key_id": kid, "public_key": pub, "algorithm": "ed25519"}]})
    return store


def _signer(key, inst, dev, lease):
    from commercial_runtime.sync import authority

    return authority.LocalSigner(
        licence=lease["payload"]["license_public_id"], inst=inst, dev=dev, pk_b64=public_key_b64(key), lease=lease,
        sign_fn=lambda data: base64.b64encode(key.sign(data)).decode("ascii"))


def _user_event():
    return {"id": str(uuid.uuid4()), "entity_type": "user", "entity_id": str(uuid.uuid4()), "event_type": "update",
            "created_at": "2026-10-01T00:00:00Z", "payload": {"uid": "u1", "role": "owner", "row_version": 2}}


@pytest.mark.parametrize("claimed", [RL.upper(), " " + RL, "{" + RL + "}", RL.replace("-", ""),
                                     RL.replace("a", "а", 1), RL + "​"],
                         ids=["upper", "space", "braces", "no-dashes", "cyrillic", "zero-width"])
def test_q3_the_receiver_never_accepts_a_variant_of_the_attested_id(app, client, shop, tmp_path, claimed):
    from commercial_runtime.sync import authority

    key = make_device_keypair()
    act = _activate(client, key, shop["full_key"], "q3r", registry_device_id=RL)
    lease, inst = act.get_json()["signed_assertion"], _iid(act)
    store = _real_owner_trust_store(app, tmp_path)
    own = str(shop["license_id"])
    ok = authority.verify_event(authority.sign_event(_user_event(), _signer(key, inst, RL, lease)),
                                trust_store=store, own_licence=own)
    assert isinstance(ok, authority.VerifiedSigner) and ok.dev == RL       # allow half
    bad = authority.verify_event(authority.sign_event(_user_event(), _signer(key, inst, claimed, lease)),
                                 trust_store=store, own_licence=own)
    assert isinstance(bad, authority.Refusal) and bad.reason == authority.REASON_DEV_NOT_ATTESTED


# ═══ Q4: main never checked in after the upgrade; a BRAND-NEW installation claims it ═══

def test_q4_a_brand_new_installation_cannot_take_an_unpinned_mains_id(app, client, shop):
    """Expected to FAIL by design (R1 window). Measures whether R1 is wider than
    stated: R1 names 'installations that existed before this release ... an
    insider who upgrades and checks in'. Here the attacker is a computer
    ACTIVATED AFTER the release (any device with the licence key and a slot)."""
    a_key, b_key = make_device_keypair(), make_device_keypair()
    a_iid = _iid(_activate(client, a_key, shop["full_key"], "q4-a"))            # pre-release style main, unpinned
    _seed_history(app, shop["license_id"], a_iid, RA)   # D-ADAPT: the main's synced history names RA, from before B existed
    b = _activate(client, b_key, shop["full_key"], "q4-b", registry_device_id=RA)  # post-release brand-new install
    assert "registry_device_id" not in _payload(b), "a brand-new installation was attested as the main"
    assert _payload(_checkin(client, a_key, a_iid, registry_device_id=RA)).get("registry_device_id") == RA


# ═══ Extra: licence replacement re-opens every id ═══════════════════════════

def test_extra_replacing_the_licence_does_not_reopen_the_mains_id(app, client, shop):
    """Post-release, outside R1 as stated. Pins are unique per LICENCE. Staff
    replace the shop's licence (leaked key / expired licence -> replace_license):
    every computer must activate again on the new licence, as NEW installations,
    and the first one to claim RA there wins. SAFE outcome: an installation of the
    replacement licence cannot take an id that a live installation of the licence
    it replaced still holds, and the real main can."""
    from app.extensions import db_session
    from app.licensing.services import issue_license_key, replace_license
    from app.models.licensing import License

    a_key, b_key, a2_key = make_device_keypair(), make_device_keypair(), make_device_keypair()
    a_iid = _iid(_activate(client, a_key, shop["full_key"], "lr-a", registry_device_id=RA))
    assert _pin(app, a_iid) == RA
    with app.app_context():
        old = db_session.get(License, shop["license_id"])
        new = replace_license(old, shop["actor"])
        new, new_key = issue_license_key(new, app.config["LICENSE_PEPPER"], str(uuid.uuid4()), shop["actor"])
        new_id = new.id
    b = _activate(client, b_key, new_key, "lr-b", registry_device_id=RA)   # insider re-activates first
    a2 = _activate(client, a2_key, new_key, "lr-a2", registry_device_id=RA)  # the real main, second
    _transfer(app, None, RA, _iid(a2), shop["actor"])   # D-ADAPT: staff move the id to the real main (a different device key cannot be told from the insider's)
    a2 = _checkin(client, a2_key, _iid(a2), registry_device_id=RA)
    assert _payload(b).get("registry_device_id") != RA, "insider attested as the main on the replacement licence"
    assert _payload(a2).get("registry_device_id") == RA, "the real main lost its id on the replacement licence"
    assert str(new_id) != str(shop["license_id"])


# ═══ Q5: concurrent pins of the same id ═════════════════════════════════════

def _race(app, monkeypatch, calls):
    """Run each callable in its own thread (own scoped db session). Both threads
    run the REAL rule-4 holder query, then wait for each other, then flush: the
    exact interleaving the IntegrityError fallback exists for."""
    from app.licensing_service import registry_pin

    real = registry_pin._holder
    barrier = threading.Barrier(len(calls), timeout=30)
    seen = threading.local()

    def gated(installation, rid):
        holder = real(installation, rid)
        if not getattr(seen, "done", False):
            seen.done = True
            barrier.wait()
        return holder

    monkeypatch.setattr(registry_pin, "_holder", gated)
    results = [None] * len(calls)
    errors = []

    def run(i, fn):
        try:
            results[i] = fn(app.test_client())
        except Exception as exc:  # noqa: BLE001
            errors.append(repr(exc))

    threads = [threading.Thread(target=run, args=(i, fn)) for i, fn in enumerate(calls)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(60)
    monkeypatch.setattr(registry_pin, "_holder", real)   # the gate is for the race only
    assert not errors, errors
    return results


@pytest.mark.parametrize("mode", ["checkin+checkin", "activation+checkin"])
def test_q5_concurrent_pins_exactly_one_wins_and_the_loser_lease_is_clean(app, client, shop, monkeypatch, mode):
    a_key, b_key = make_device_keypair(), make_device_keypair()
    b_iid = _iid(_activate(client, b_key, shop["full_key"], "q5-b"))
    if mode == "checkin+checkin":
        a_iid = _iid(_activate(client, a_key, shop["full_key"], "q5-a"))
        first = lambda c: _checkin(c, a_key, a_iid, registry_device_id=RA)  # noqa: E731
    else:
        a_iid = None
        first = lambda c: _activate(c, a_key, shop["full_key"], "q5-a", registry_device_id=RA)  # noqa: E731
    second = lambda c: _checkin(c, b_key, b_iid, registry_device_id=RA)  # noqa: E731
    r1, r2 = _race(app, monkeypatch, [first, second])
    assert r1.status_code == 200 and r2.status_code == 200, (r1.status_code, r1.get_json(), r2.status_code, r2.get_json())
    a_iid = a_iid or r1.get_json()["installation_id"]
    named = [p.get("registry_device_id") for p in (_payload(r1), _payload(r2))]
    assert named.count(RA) == 1, f"exactly one lease may name RA, got {named}"
    holders = _live_holders(app, shop["license_id"], RA)
    assert len(holders) == 1
    winner_iid = holders[0]
    loser_iid = b_iid if winner_iid == a_iid else a_iid
    winner_resp = r1 if winner_iid == a_iid else r2
    assert _payload(winner_resp).get("registry_device_id") == RA
    assert _pin(app, loser_iid) is None
    assert len(_events(app, loser_iid, "REGISTRY_DEVICE_ID_CLASH")) == 1
    # and the loser's very next check-in is still clean (no stale in-memory pin got committed later)
    loser_key = b_key if loser_iid == b_iid else a_key
    assert "registry_device_id" not in _payload(_checkin(client, loser_key, loser_iid, registry_device_id=RA))


# ═══ Q6: different licence, same id ═════════════════════════════════════════

def test_q6_a_lease_from_another_licence_naming_the_id_is_refused_by_this_licences_receiver(app, client, shop, tmp_path):
    from commercial_runtime.sync import authority

    other_license_id, other_full_key = make_license(app, shop["actor"], device_limit=1)
    a_key, o_key = make_device_keypair(), make_device_keypair()
    a = _activate(client, a_key, shop["full_key"], "q6-a", registry_device_id=RA)
    o = _activate(client, o_key, other_full_key, "q6-o", registry_device_id=RA)
    assert _payload(o)["registry_device_id"] == RA           # allowed by design at Owner
    store = _real_owner_trust_store(app, tmp_path)
    own = str(shop["license_id"])
    foreign = authority.verify_event(
        authority.sign_event(_user_event(), _signer(o_key, _iid(o), RA, o.get_json()["signed_assertion"])),
        trust_store=store, own_licence=own)
    assert isinstance(foreign, authority.Refusal) and foreign.reason == authority.REASON_FOREIGN_LICENCE
    # the same lease cannot be dressed up as licence L1 either (licence is inside the Owner signature)
    lease = json.loads(json.dumps(o.get_json()["signed_assertion"]))
    lease["payload"]["license_public_id"] = own
    forged_event = authority.sign_event(_user_event(), _signer(o_key, _iid(o), RA, lease))
    # (a) warm lease cache (keyed by signature, not payload): the cached AUTHENTIC payload wins -> still foreign
    forged = authority.verify_event(forged_event, trust_store=store, own_licence=own)
    assert isinstance(forged, authority.Refusal), forged
    # (b) cold cache: the Owner signature itself must reject the edited licence
    authority._LEASE_CACHE.clear()
    forged_cold = authority.verify_event(forged_event, trust_store=store, own_licence=own)
    assert isinstance(forged_cold, authority.Refusal) and forged_cold.reason == authority.REASON_UNTRUSTED_LEASE, forged_cold
    # allow half: L1's own main verifies on the L1 receiver
    mine = authority.verify_event(
        authority.sign_event(_user_event(), _signer(a_key, _iid(a), RA, a.get_json()["signed_assertion"])),
        trust_store=store, own_licence=own)
    assert isinstance(mine, authority.VerifiedSigner) and mine.dev == RA
    assert str(other_license_id) != own
