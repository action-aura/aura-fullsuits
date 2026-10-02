"""AUDIT-033 / review V3o R4: the staff transfer is the ONLY way a pinned registry
id changes installation. Service + `flask licensing transfer-registry-id`.

Allow half: moves, audits, the new holder is then attested and the old one is not.
Deny half: no reason, other licence chain, target pinned elsewhere, no holder."""
from __future__ import annotations

import json
import uuid

import pytest

from tests.conftest import build_activation_body, build_checkin_body, make_device_keypair, make_license, make_staff

RA = "11111111-1111-4111-8111-111111111111"
RB = "22222222-2222-4222-8222-222222222222"


def _post(client, path, body):
    return client.post(path, data=json.dumps(body), content_type="application/json")


def _activate(client, key, full_key, label, **extra):
    return _post(client, "/api/licensing/v1/activations", build_activation_body(key, full_key=full_key, installation_id=label, **extra))


def _checkin(client, key, iid, **extra):
    return _post(client, "/api/licensing/v1/check-ins", build_checkin_body(key, installation_id=iid, **extra))


def _payload(resp):
    assert resp.status_code == 200, resp.get_json()
    return resp.get_json()["signed_assertion"]["payload"]


def _iid(resp):
    assert resp.status_code == 200, resp.get_json()
    return resp.get_json()["installation_id"]


def _pin(app, iid):
    with app.app_context():
        from app.extensions import db_session
        from app.models.installations import Installation

        db_session.expire_all()
        return db_session.get(Installation, uuid.UUID(iid)).registry_device_id


def _events(app, iid, event_type):
    with app.app_context():
        from sqlalchemy import select

        from app.extensions import db_session
        from app.models.installations import ActivationEvent

        rows = db_session.execute(select(ActivationEvent).where(
            ActivationEvent.installation_id == uuid.UUID(iid), ActivationEvent.event_type == event_type)).scalars().all()
        return [(e.result, e.originating_staff_user_id, dict(e.safe_metadata)) for e in rows]


def _audit(app, iid, action):
    with app.app_context():
        from sqlalchemy import select

        from app.extensions import db_session
        from app.models.audit import AuditLog

        return db_session.execute(select(AuditLog).where(
            AuditLog.entity_public_id == iid, AuditLog.action_code == action)).scalars().all()


def _transfer(app, rid, to_iid, actor, reason):
    with app.app_context():
        from app.extensions import db_session
        from app.licensing_service.registry_pin import transfer_registry_device_id

        transfer_registry_device_id(db_session, registry_device_id=rid, to_installation_id=uuid.UUID(to_iid),
                                    actor_staff_user_id=actor, reason=reason)


def _code(app, rid, to_iid, actor, reason):
    from app.licensing_service.registry_pin import RegistryTransferError

    with pytest.raises(RegistryTransferError) as ei:
        _transfer(app, rid, to_iid, actor, reason)
    return ei.value.code


@pytest.fixture()
def shop(app, client, seeded, signing_key):
    actor = make_staff(app, f"xfer-{uuid.uuid4().hex[:6]}@example.com")
    license_id, full_key = make_license(app, actor, device_limit=5)
    return {"actor": actor, "license_id": license_id, "full_key": full_key}


def _two(client, shop):
    a_key, b_key = make_device_keypair(), make_device_keypair()
    a_iid = _iid(_activate(client, a_key, shop["full_key"], "x-a", registry_device_id=RA))
    b_iid = _iid(_activate(client, b_key, shop["full_key"], "x-b"))
    return a_key, b_key, a_iid, b_iid


def test_transfer_moves_the_id_audits_it_and_changes_who_is_attested(app, client, shop):
    a_key, b_key, a_iid, b_iid = _two(client, shop)
    _transfer(app, RA, b_iid, shop["actor"], "  main computer replaced  ")
    assert _pin(app, a_iid) is None and _pin(app, b_iid) == RA
    for iid in (a_iid, b_iid):
        (result, staff, meta), = _events(app, iid, "REGISTRY_ID_TRANSFERRED")
        assert result == "SUCCESS" and staff == shop["actor"]
        assert meta == {"registry_device_id": RA, "from_installation": a_iid, "to_installation": b_iid,
                        "reason": "main computer replaced"}
    (row,) = _audit(app, b_iid, "REGISTRY_ID_TRANSFERRED")
    assert row.actor_staff_user_id == shop["actor"] and row.reason == "main computer replaced"
    # the receivers' view: the new holder is attested, the old one now clashes
    assert _payload(_checkin(client, b_key, b_iid, registry_device_id=RA))["registry_device_id"] == RA
    assert "registry_device_id" not in _payload(_checkin(client, a_key, a_iid, registry_device_id=RA))


@pytest.mark.parametrize("reason", ["", "   ", None])
def test_transfer_without_a_reason_is_refused_and_changes_nothing(app, client, shop, reason):
    _, _, a_iid, b_iid = _two(client, shop)
    assert _code(app, RA, b_iid, shop["actor"], reason) == "REASON_REQUIRED"
    assert _pin(app, a_iid) == RA and _pin(app, b_iid) is None
    assert _events(app, b_iid, "REGISTRY_ID_TRANSFERRED") == [] and _audit(app, b_iid, "REGISTRY_ID_TRANSFERRED") == []


def test_transfer_across_two_unrelated_licences_is_refused(app, client, shop):
    a_key = make_device_keypair()
    a_iid = _iid(_activate(client, a_key, shop["full_key"], "x-a", registry_device_id=RA))
    _, other_key = make_license(app, shop["actor"], device_limit=2)
    o_iid = _iid(_activate(client, make_device_keypair(), other_key, "x-o"))
    assert _code(app, RA, o_iid, shop["actor"], "why") == "HOLDER_ON_A_DIFFERENT_LICENCE_CHAIN"
    assert _pin(app, a_iid) == RA and _pin(app, o_iid) is None


def test_transfer_to_a_target_pinned_to_another_id_is_refused(app, client, shop):
    _, b_key, a_iid, b_iid = _two(client, shop)
    assert _payload(_checkin(client, b_key, b_iid, registry_device_id=RB))["registry_device_id"] == RB
    assert _code(app, RA, b_iid, shop["actor"], "why") == "TARGET_PINNED_TO_A_DIFFERENT_ID"
    assert _pin(app, a_iid) == RA and _pin(app, b_iid) == RB


def test_transfer_with_no_holder_to_the_holder_itself_or_to_nothing_is_refused(app, client, shop):
    _, _, a_iid, b_iid = _two(client, shop)
    assert _code(app, RB, b_iid, shop["actor"], "why") == "NO_CURRENT_HOLDER"
    assert _code(app, RA, a_iid, shop["actor"], "why") == "TARGET_ALREADY_HOLDS_ID"
    assert _code(app, RA, str(uuid.uuid4()), shop["actor"], "why") == "TARGET_NOT_FOUND"
    assert _code(app, "NOT-A-UUID", b_iid, shop["actor"], "why") == "REGISTRY_DEVICE_ID_INVALID"
    assert _pin(app, a_iid) == RA


def test_transfer_works_across_a_licence_replacement_chain(app, client, shop):
    """The G1 shape: holder on the replaced licence, target on its replacement."""
    from app.extensions import db_session
    from app.licensing.services import issue_license_key, replace_license
    from app.models.licensing import License

    a_iid = _iid(_activate(client, make_device_keypair(), shop["full_key"], "x-a", registry_device_id=RA))
    with app.app_context():
        new = replace_license(db_session.get(License, shop["license_id"]), shop["actor"])
        new, new_key = issue_license_key(new, app.config["LICENSE_PEPPER"], str(uuid.uuid4()), shop["actor"])
    b_iid = _iid(_activate(client, make_device_keypair(), new_key, "x-b", registry_device_id=RA))
    assert _pin(app, b_iid) is None          # chain-wide reservation: no auto-take
    _transfer(app, RA, b_iid, shop["actor"], "licence replaced; this is the same shop's main")
    assert _pin(app, a_iid) is None and _pin(app, b_iid) == RA


# -- the Flask CLI -----------------------------------------------------------

def _cli(app, *args):
    return app.test_cli_runner().invoke(args=["licensing", "transfer-registry-id", *args])


def test_cli_transfers_and_refuses(app, client, shop):
    _, _, a_iid, b_iid = _two(client, shop)
    with app.app_context():
        from app.extensions import db_session
        from app.models.staff import StaffUser

        email = db_session.get(StaffUser, shop["actor"]).email
    refused = _cli(app, "--registry-device-id", RA, "--to", b_iid, "--actor", email, "--reason", "   ")
    assert refused.exit_code != 0 and "REASON_REQUIRED" in refused.output
    assert _cli(app, "--registry-device-id", RA, "--to", b_iid, "--actor", "nobody@example.com", "--reason", "x").exit_code != 0
    assert _cli(app, "--registry-device-id", RA, "--to", b_iid, "--actor", email).exit_code != 0   # --reason missing
    assert _pin(app, a_iid) == RA and _pin(app, b_iid) is None
    ok = _cli(app, "--registry-device-id", RA, "--to", b_iid, "--actor", email, "--reason", "main replaced")
    assert ok.exit_code == 0, ok.output
    assert _pin(app, a_iid) is None and _pin(app, b_iid) == RA
    assert len(_audit(app, b_iid, "REGISTRY_ID_TRANSFERRED")) == 1
