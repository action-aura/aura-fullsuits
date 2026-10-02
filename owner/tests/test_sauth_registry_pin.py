"""AUDIT-033 S-AUTH fix round (verifier V finding #1): Owner pins the registry
device id per installation and signs it into the lease.

Every assertion below is about what a real device would receive over the real
HTTP routes, never about a helper's return value: the lease payload itself is
read back out of the signed response."""
from __future__ import annotations

import json
import uuid

import pytest

from tests.conftest import (
    build_activation_body, build_checkin_body, make_device_keypair, make_license, make_staff, public_key_b64,
)

RA = "11111111-1111-4111-8111-111111111111"
RB = "22222222-2222-4222-8222-222222222222"


def _post(client, path, body):
    return client.post(path, data=json.dumps(body), content_type="application/json")


def _activate(client, key, full_key, label, **extra):
    resp = _post(client, "/api/licensing/v1/activations", build_activation_body(key, full_key=full_key, installation_id=label, **extra))
    return resp


def _checkin(client, key, installation_id, **extra):
    return _post(client, "/api/licensing/v1/check-ins", build_checkin_body(key, installation_id=installation_id, **extra))


def _payload(resp):
    assert resp.status_code == 200, resp.get_json()
    return resp.get_json()["signed_assertion"]["payload"]


def _events(app, installation_id, event_type=None):
    with app.app_context():
        from sqlalchemy import select

        from app.extensions import db_session
        from app.models.installations import ActivationEvent

        rows = db_session.execute(
            select(ActivationEvent).where(ActivationEvent.installation_id == uuid.UUID(installation_id))
        ).scalars().all()
        return [(e.event_type, e.result, dict(e.safe_metadata or {})) for e in rows if event_type in (None, e.event_type)]


def _audit_actions(app, installation_id):
    with app.app_context():
        from sqlalchemy import select

        from app.extensions import db_session
        from app.models.audit import AuditLog

        rows = db_session.execute(select(AuditLog).where(AuditLog.entity_public_id == installation_id)).scalars().all()
        return [r.action_code for r in rows]


def _pin(app, installation_id):
    with app.app_context():
        from app.extensions import db_session
        from app.models.installations import Installation

        return db_session.get(Installation, uuid.UUID(installation_id)).registry_device_id


@pytest.fixture()
def shop(app, client, seeded, signing_key):
    actor = make_staff(app, f"pin-{uuid.uuid4().hex[:6]}@example.com")
    license_id, full_key = make_license(app, actor, device_limit=3)
    return {"actor": actor, "license_id": license_id, "full_key": full_key}


def test_old_client_gets_a_lease_without_the_key_even_after_a_pin(app, client, shop):
    key = make_device_keypair()
    act = _activate(client, key, shop["full_key"], "old-1")
    assert "registry_device_id" not in _payload(act)  # old client: byte-for-byte the lease it always got
    iid = act.get_json()["installation_id"]
    assert _pin(app, iid) is None
    assert _events(app, iid, "REGISTRY_DEVICE_ID_PINNED") == []

    # Pinned later by a newer client; an old-style check-in must STILL not get the key.
    assert _payload(_checkin(client, key, iid, registry_device_id=RA))["registry_device_id"] == RA
    assert "registry_device_id" not in _payload(_checkin(client, key, iid))
    assert _pin(app, iid) == RA


def test_activation_with_an_id_pins_it_and_the_lease_carries_it(app, client, shop):
    key = make_device_keypair()
    act = _activate(client, key, shop["full_key"], "new-1", registry_device_id=RA)
    assert _payload(act)["registry_device_id"] == RA
    iid = act.get_json()["installation_id"]
    assert _pin(app, iid) == RA
    assert [e[0] for e in _events(app, iid, "REGISTRY_DEVICE_ID_PINNED")] == ["REGISTRY_DEVICE_ID_PINNED"]
    assert "REGISTRY_DEVICE_ID_PINNED" in _audit_actions(app, iid)


def test_checkin_pins_an_unpinned_installation_and_the_same_id_again_is_idempotent(app, client, shop):
    key = make_device_keypair()
    iid = _activate(client, key, shop["full_key"], "upg-1").get_json()["installation_id"]
    assert _payload(_checkin(client, key, iid, registry_device_id=RA))["registry_device_id"] == RA
    assert _payload(_checkin(client, key, iid, registry_device_id=RA))["registry_device_id"] == RA
    assert _pin(app, iid) == RA
    assert len(_events(app, iid, "REGISTRY_DEVICE_ID_PINNED")) == 1, "the second claim must not re-pin or re-log"
    assert _events(app, iid, "REGISTRY_DEVICE_ID_CHANGE_REFUSED") == []


def test_a_different_id_never_replaces_the_pin(app, client, shop):
    key = make_device_keypair()
    iid = _activate(client, key, shop["full_key"], "imm-1", registry_device_id=RA).get_json()["installation_id"]
    resp = _checkin(client, key, iid, registry_device_id=RB)
    assert _payload(resp)["registry_device_id"] == RA, "the lease keeps naming the ORIGINAL pin"
    assert _pin(app, iid) == RA
    refused = _events(app, iid, "REGISTRY_DEVICE_ID_CHANGE_REFUSED")
    assert len(refused) == 1 and refused[0][1] == "FAILURE"
    assert refused[0][2] == {"requested": RB, "pinned": RA}


def test_a_second_live_installation_cannot_claim_a_pinned_id_but_still_activates(app, client, shop):
    a_key, b_key = make_device_keypair(), make_device_keypair()
    a_iid = _activate(client, a_key, shop["full_key"], "clash-a", registry_device_id=RA).get_json()["installation_id"]

    b_act = _activate(client, b_key, shop["full_key"], "clash-b", registry_device_id=RA)
    assert b_act.get_json()["result"] == "SUCCESS", "Owner never bricks licensing over a clash"
    assert "registry_device_id" not in _payload(b_act), "the squatter gets NO attestation"
    b_iid = b_act.get_json()["installation_id"]
    assert _pin(app, b_iid) is None
    assert _pin(app, a_iid) == RA, "the first pin is untouched"
    clash = _events(app, b_iid, "REGISTRY_DEVICE_ID_CLASH")
    assert len(clash) == 1 and clash[0][1] == "FAILURE" and clash[0][2]["held_by_installation"] == a_iid
    assert "REGISTRY_DEVICE_ID_CLASH" in _audit_actions(app, b_iid)

    # A check-in re-claim stays refused too, and the real owner of the id is unaffected.
    assert "registry_device_id" not in _payload(_checkin(client, b_key, b_iid, registry_device_id=RA))
    assert _payload(_checkin(client, a_key, a_iid, registry_device_id=RA))["registry_device_id"] == RA


def test_another_licence_may_use_the_same_id(app, client, shop):
    other_license_id, other_key_text = make_license(app, shop["actor"], device_limit=1)
    a_key, o_key = make_device_keypair(), make_device_keypair()
    _activate(client, a_key, shop["full_key"], "xl-a", registry_device_id=RA)
    other = _activate(client, o_key, other_key_text, "xl-o", registry_device_id=RA)
    assert _payload(other)["registry_device_id"] == RA
    assert _events(app, other.get_json()["installation_id"], "REGISTRY_DEVICE_ID_CLASH") == []


def test_a_deactivated_holder_keeps_the_id_reserved(app, client, shop):
    # REVERSED in the V3o round (G2). It used to assert that a DEACTIVATED holder
    # FREES the id; that freeing is the defect (an installation lurking beside
    # the main claimed the id the moment staff released the main's slot). This
    # test can therefore no longer catch "a terminal holder frees its id"; that
    # direction is now the failure. The allow half (the id still moves, by an
    # explicit staff transfer) is in test_sauth_registry_pin_transfer.py.
    a_key, b_key = make_device_keypair(), make_device_keypair()
    a_iid = _activate(client, a_key, shop["full_key"], "free-a", registry_device_id=RA).get_json()["installation_id"]
    with app.app_context():
        from app.commercial_ops.device_slot_ops import release_device_slot
        from app.extensions import db_session
        from app.models.installations import Installation

        release_device_slot(db_session.get(Installation, uuid.UUID(a_iid)), reason="test", actor_staff_user_id=shop["actor"])
    b_act = _activate(client, b_key, shop["full_key"], "free-b", registry_device_id=RA)
    assert "registry_device_id" not in _payload(b_act)
    assert _pin(app, b_act.get_json()["installation_id"]) is None
    assert _pin(app, a_iid) == RA
    assert len(_events(app, b_act.get_json()["installation_id"], "REGISTRY_DEVICE_ID_CLASH")) == 1


@pytest.mark.parametrize(
    "bad", ["AAAAAAAA-AAAA-4AAA-8AAA-AAAAAAAAAAAA", "not-a-uuid", RA.replace("-", ""), "x" * 65, 12345, ["a"]],
    ids=["uppercase", "garbage", "no-dashes", "too-long", "int", "list"],
)
def test_a_malformed_id_is_recorded_as_invalid_and_never_pinned(app, client, shop, bad):
    key = make_device_keypair()
    act = _activate(client, key, shop["full_key"], "bad-1", registry_device_id=bad)
    assert act.get_json()["result"] == "SUCCESS"
    assert "registry_device_id" not in _payload(act)
    iid = act.get_json()["installation_id"]
    assert _pin(app, iid) is None
    assert len(_events(app, iid, "REGISTRY_DEVICE_ID_INVALID")) == 1


def test_replacing_the_device_key_keeps_the_pin(app, client, shop):
    old_key, new_key = make_device_keypair(), make_device_keypair()
    iid = _activate(client, old_key, shop["full_key"], "rot-1", registry_device_id=RA).get_json()["installation_id"]
    with app.app_context():
        from app.extensions import db_session
        from app.licensing_service import device_identity

        current = device_identity.get_active_device_key(uuid.UUID(iid))
        device_identity.replace_device_key(current, "ed25519", public_key_b64(new_key))
    assert _pin(app, iid) == RA
    resp = _checkin(client, new_key, iid, registry_device_id=RA)
    assert _payload(resp)["registry_device_id"] == RA
    # ...and the retired key can no longer speak for it.
    assert _checkin(client, old_key, iid, registry_device_id=RA).status_code == 400


def test_tampering_with_the_id_after_the_device_signed_is_rejected(app, client, shop):
    key = make_device_keypair()
    body = build_activation_body(key, full_key=shop["full_key"], installation_id="tamper-1", registry_device_id=RA)
    forged = {**body, "registry_device_id": RB}  # an on-path attacker swaps the id, keeps the signature
    resp = _post(client, "/api/licensing/v1/activations", forged)
    assert resp.status_code == 400 and resp.get_json()["reason_code"] == "INVALID_SIGNATURE"

    iid = _activate(client, key, shop["full_key"], "tamper-2").get_json()["installation_id"]
    ci = build_checkin_body(key, installation_id=iid, registry_device_id=RA)
    resp = _post(client, "/api/licensing/v1/check-ins", {**ci, "registry_device_id": RB})
    assert resp.status_code == 400 and resp.get_json()["reason_code"] == "INVALID_SIGNATURE"
    assert _pin(app, iid) is None


def test_the_lease_still_verifies_with_the_new_field(app, client, shop):
    """The field is inside the Owner-signed payload: changing it must break the signature."""
    key = make_device_keypair()
    envelope = _activate(client, key, shop["full_key"], "sig-1", registry_device_id=RA).get_json()["signed_assertion"]
    with app.app_context():
        from app.licensing_service.assertions import verify_assertion

        assert verify_assertion(envelope)[0] is True
        envelope["payload"]["registry_device_id"] = RB
        assert verify_assertion(envelope)[0] is False
