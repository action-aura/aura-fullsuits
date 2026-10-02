"""AUDIT-033 / review V3o G3: the history guard.

An installation activated AFTER the id already appeared in the shop's synced
history cannot be the id's original computer, so it is never auto-pinned.
Allow half: no history, history that starts after the installation, history
inside the 10-minute clock-skew margin. Deny half: history older than the margin
(both `device` and `admin_device` events), on the licence chain."""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from tests.conftest import build_activation_body, build_checkin_body, make_device_keypair, make_license, make_staff

RA = "11111111-1111-4111-8111-111111111111"
T0 = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)


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
    return resp.get_json()["installation_id"]


def _pin(app, iid):
    with app.app_context():
        from app.extensions import db_session
        from app.models.installations import Installation

        db_session.expire_all()
        return db_session.get(Installation, uuid.UUID(iid)).registry_device_id


def _conflicts(app, iid):
    with app.app_context():
        from sqlalchemy import select

        from app.extensions import db_session
        from app.models.installations import ActivationEvent

        return [dict(e.safe_metadata) for e in db_session.execute(select(ActivationEvent).where(
            ActivationEvent.installation_id == uuid.UUID(iid),
            ActivationEvent.event_type == "REGISTRY_DEVICE_ID_HISTORY_CONFLICT")).scalars().all()]


def _audit_count(app, iid):
    with app.app_context():
        from sqlalchemy import select

        from app.extensions import db_session
        from app.models.audit import AuditLog

        return len(db_session.execute(select(AuditLog).where(
            AuditLog.entity_public_id == iid, AuditLog.action_code == "REGISTRY_DEVICE_ID_HISTORY_CONFLICT")).scalars().all())


def _set_created(app, iid, when):
    with app.app_context():
        from app.extensions import db_session
        from app.models.installations import Installation

        db_session.get(Installation, uuid.UUID(iid)).created_at = when
        db_session.commit()


def _event(app, license_id, *, etype, rid, at, via="entity"):
    with app.app_context():
        from app.extensions import db_session
        from app.models.sync import SyncEvent

        db_session.add(SyncEvent(
            id=uuid.uuid4(), license_id=license_id, device_id=uuid.uuid4(), entity_type=etype,
            entity_id=uuid.UUID(rid) if via == "entity" else uuid.uuid4(), event_type="create",
            payload={"device_id": rid} if via == "payload" else {}, client_created_at=at))
        db_session.commit()


@pytest.fixture()
def shop(app, client, seeded, signing_key):
    actor = make_staff(app, f"hist-{uuid.uuid4().hex[:6]}@example.com")
    license_id, full_key = make_license(app, actor, device_limit=5)
    return {"actor": actor, "license_id": license_id, "full_key": full_key}


def _unpinned(client, app, shop, label="h-1"):
    key = make_device_keypair()
    iid = _iid(_activate(client, key, shop["full_key"], label))
    _set_created(app, iid, T0)
    return key, iid


def test_no_history_means_the_id_pins(app, client, shop):
    key, iid = _unpinned(client, app, shop)
    assert _payload(_checkin(client, key, iid, registry_device_id=RA))["registry_device_id"] == RA
    assert _conflicts(app, iid) == []


def test_history_that_starts_after_the_installation_still_pins(app, client, shop):
    key, iid = _unpinned(client, app, shop)
    _event(app, shop["license_id"], etype="device", rid=RA, at=T0 + timedelta(hours=1))
    assert _payload(_checkin(client, key, iid, registry_device_id=RA))["registry_device_id"] == RA


@pytest.mark.parametrize("minutes,pins", [(9, True), (11, False)], ids=["inside-margin-pins", "outside-margin-refused"])
def test_the_skew_margin_edge(app, client, shop, minutes, pins):
    key, iid = _unpinned(client, app, shop)
    _event(app, shop["license_id"], etype="device", rid=RA, at=T0 - timedelta(minutes=minutes))
    got = _payload(_checkin(client, key, iid, registry_device_id=RA))
    assert (got.get("registry_device_id") == RA) is pins
    assert (_pin(app, iid) == RA) is pins
    assert len(_conflicts(app, iid)) == (0 if pins else 1)


@pytest.mark.parametrize("via", ["entity", "payload"], ids=["device-event", "admin_device-event"])
def test_old_history_refuses_the_pin_records_and_audits_but_never_fails_the_request(app, client, shop, via):
    key, iid = _unpinned(client, app, shop)
    _event(app, shop["license_id"], etype="device" if via == "entity" else "admin_device", rid=RA,
           at=T0 - timedelta(days=3), via=via)
    resp = _checkin(client, key, iid, registry_device_id=RA)
    assert resp.status_code == 200 and "registry_device_id" not in _payload(resp)
    assert _pin(app, iid) is None
    (meta,) = _conflicts(app, iid)
    assert meta["requested"] == RA and meta["first_seen_at"].startswith((T0 - timedelta(days=3)).strftime("%Y-%m-%d"))
    assert _audit_count(app, iid) == 1


def test_history_of_a_different_id_or_an_unrelated_licence_does_not_block(app, client, shop):
    key, iid = _unpinned(client, app, shop)
    other_license, _ = make_license(app, shop["actor"], device_limit=1)
    _event(app, shop["license_id"], etype="device", rid="22222222-2222-4222-8222-222222222222", at=T0 - timedelta(days=3))
    _event(app, other_license, etype="device", rid=RA, at=T0 - timedelta(days=3))
    assert _payload(_checkin(client, key, iid, registry_device_id=RA))["registry_device_id"] == RA


def test_history_on_the_replaced_licence_of_the_chain_blocks(app, client, shop):
    from app.extensions import db_session
    from app.licensing.services import issue_license_key, replace_license
    from app.models.licensing import License

    with app.app_context():
        new = replace_license(db_session.get(License, shop["license_id"]), shop["actor"])
        new, new_key = issue_license_key(new, app.config["LICENSE_PEPPER"], str(uuid.uuid4()), shop["actor"])
    key = make_device_keypair()
    iid = _iid(_activate(client, key, new_key, "h-new"))
    _set_created(app, iid, T0)
    _event(app, shop["license_id"], etype="device", rid=RA, at=T0 - timedelta(days=3))     # OLD licence's history
    assert "registry_device_id" not in _payload(_checkin(client, key, iid, registry_device_id=RA))
    assert _pin(app, iid) is None


def test_activation_path_is_guarded_too(app, client, shop):
    """A brand-new installation (created now) claiming at activation, after history exists."""
    _event(app, shop["license_id"], etype="device", rid=RA, at=datetime.now(timezone.utc) - timedelta(days=3))
    resp = _activate(client, make_device_keypair(), shop["full_key"], "h-act", registry_device_id=RA)
    assert resp.status_code == 200 and "registry_device_id" not in _payload(resp)
    assert len(_conflicts(app, _iid(resp))) == 1
