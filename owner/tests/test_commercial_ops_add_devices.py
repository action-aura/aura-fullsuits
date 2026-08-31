"""Tests for `add_devices()` (docs/owner/packages-and-issuance-design.md §C.2
-- "Add devices" in one act, the highest-value Owner change identified in
that design doc). Follows the exact fixture/style conventions of
test_commercial_ops_device_slot_ops.py (pure service-layer calls inside
app.app_context()) and test_phase6_activation_protocol.py (real HTTP
activation flow, real Ed25519 signatures) for the one test that must prove
the real enforcement path, not just the service function in isolation.
"""
from __future__ import annotations

import json

import pytest

from tests.conftest import build_activation_body, make_device_keypair, make_license, make_staff


def _activate(client, body):
    return client.post("/api/licensing/v1/activations", data=json.dumps(body), content_type="application/json")


def _windows_platform_id(app):
    from app.extensions import db_session
    from app.models.catalog import Platform

    return db_session.query(Platform).filter_by(platform_code="WINDOWS").first().id


# -- input validation ---------------------------------------------------

def test_add_devices_validates_positive_count_and_required_reason(app, seeded):
    staff_id = make_staff(app, "ad1@example.com")
    with app.app_context():
        from app.commercial_ops.device_slot_ops import DeviceSlotError, add_devices
        from app.extensions import db_session
        from app.models.licensing import License

        lic_id, _ = make_license(app, staff_id, device_limit=1)
        lic = db_session.get(License, lic_id)

        with pytest.raises(DeviceSlotError):
            add_devices(lic, additional_devices=0, reason="x", actor_staff_user_id=staff_id)
        with pytest.raises(DeviceSlotError):
            add_devices(lic, additional_devices=-1, reason="x", actor_staff_user_id=staff_id)
        with pytest.raises(DeviceSlotError):
            add_devices(lic, additional_devices=1, reason="", actor_staff_user_id=staff_id)
        with pytest.raises(DeviceSlotError):
            add_devices(lic, additional_devices=1, reason="   ", actor_staff_user_id=staff_id)


# -- raises the limit, mirrors the subscription --------------------------

def test_add_devices_raises_device_limit_and_mirrors_subscription(app, seeded):
    staff_id = make_staff(app, "ad2@example.com")
    with app.app_context():
        from app.commercial_ops.device_slot_ops import add_devices
        from app.extensions import db_session
        from app.models.licensing import License
        from app.models.subscriptions import Subscription

        lic_id, _ = make_license(app, staff_id, device_limit=2)
        lic = db_session.get(License, lic_id)
        subscription_id = lic.subscription_id

        updated = add_devices(lic, additional_devices=3, reason="Customer bought 3 more tills", actor_staff_user_id=staff_id)
        assert updated.device_limit == 5

        # Re-fetch from the DB, not the in-memory object, to prove the write
        # was actually persisted (M1's exact failure mode: a mutation that
        # only touches the in-memory row and never commits would still pass
        # an assertion against `updated`/`lic`, but not against a fresh read).
        db_session.expire_all()
        refreshed_license = db_session.get(License, lic_id)
        assert refreshed_license.device_limit == 5
        refreshed_subscription = db_session.get(Subscription, subscription_id)
        assert refreshed_subscription.device_allowance == 5


# -- never lowers below what's already deployed --------------------------

def test_add_devices_refuses_lowering_below_active_count(app, seeded):
    staff_id = make_staff(app, "ad3@example.com")
    with app.app_context():
        from app.commercial_ops.device_slot_ops import DeviceSlotError, add_devices
        from app.extensions import db_session
        from app.installations.services import register_installation
        from app.licensing.services import transition_license
        from app.models.licensing import License

        lic_id, _ = make_license(app, staff_id, device_limit=1)
        lic = db_session.get(License, lic_id)
        transition_license(lic, "ACTIVE", staff_id)
        platform_id = _windows_platform_id(app)
        # 3 slot-consuming installations against a device_limit of 1 -- an
        # already over-limit license (the kind apply_renewal_request()'s own
        # silent-decrease path can create; scan_over_limit_licenses() would
        # flag it but never fixes it). A small +1 add is not enough to
        # reach 3, so it must be refused outright, not silently applied.
        for label in ("adl-a", "adl-b", "adl-c"):
            inst = register_installation(
                {"customer_id": lic.customer_id, "license_id": lic.id, "product_id": lic.product_id,
                 "platform_id": platform_id, "installation_label": label},
                staff_id,
            )
            inst.status = "ACTIVE"
        db_session.commit()

        with pytest.raises(DeviceSlotError):
            add_devices(lic, additional_devices=1, reason="Not enough to cover usage", actor_staff_user_id=staff_id)

        db_session.expire_all()
        refreshed = db_session.get(License, lic_id)
        assert refreshed.device_limit == 1  # unchanged -- the refusal did not partially apply


# -- audit trail -----------------------------------------------------------

def test_add_devices_writes_audit_record_naming_actor_and_reason(app, seeded):
    staff_id = make_staff(app, "ad4@example.com")
    with app.app_context():
        from app.commercial_ops.device_slot_ops import add_devices
        from app.extensions import db_session
        from app.models.audit import AuditLog
        from app.models.licensing import License

        lic_id, _ = make_license(app, staff_id, device_limit=1)
        lic = db_session.get(License, lic_id)

        add_devices(lic, additional_devices=2, reason="Customer purchased 2 additional tills", actor_staff_user_id=staff_id)

        rows = db_session.query(AuditLog).filter_by(action_code="LICENSE_DEVICES_ADDED", entity_public_id=str(lic_id)).all()
        assert len(rows) == 1
        row = rows[0]
        assert row.actor_staff_user_id == staff_id
        assert row.reason == "Customer purchased 2 additional tills"
        assert row.before_state_redacted == {"device_limit": 1}
        assert row.after_state_redacted == {"device_limit": 3, "additional_devices": 2}


# -- idempotency, matching CommercialOperationsIdempotencyKey convention -----

def test_add_devices_idempotent_retry_does_not_double_add(app, seeded):
    staff_id = make_staff(app, "ad5@example.com")
    with app.app_context():
        from app.commercial_ops.device_slot_ops import add_devices
        from app.extensions import db_session
        from app.models.audit import AuditLog
        from app.models.licensing import License

        lic_id, _ = make_license(app, staff_id, device_limit=1)
        lic = db_session.get(License, lic_id)

        first = add_devices(
            lic, additional_devices=2, reason="Customer purchased 2 tills", actor_staff_user_id=staff_id,
            idempotency_key="add-devices-retry-1",
        )
        assert first.device_limit == 3

        # Simulates a client retry (e.g. a network timeout on the first
        # response) -- must return the already-applied result, not add again.
        second = add_devices(
            lic, additional_devices=2, reason="Customer purchased 2 tills", actor_staff_user_id=staff_id,
            idempotency_key="add-devices-retry-1",
        )
        assert second.id == first.id
        assert second.device_limit == 3

        db_session.expire_all()
        refreshed = db_session.get(License, lic_id)
        assert refreshed.device_limit == 3
        rows = db_session.query(AuditLog).filter_by(action_code="LICENSE_DEVICES_ADDED", entity_public_id=str(lic_id)).all()
        assert len(rows) == 1  # not audited twice


# -- real enforcement path: unblocks a device that hit DEVICE_LIMIT_REACHED --

def test_add_devices_unblocks_activation_after_device_limit_reached(app, client, seeded, signing_key):
    actor_id = make_staff(app, "ad6@example.com")
    license_id, full_key = make_license(app, actor_id, device_limit=1)

    private_key_1 = make_device_keypair()
    resp1 = _activate(client, build_activation_body(private_key_1, full_key=full_key, installation_id="ad6-dev-a"))
    assert resp1.status_code == 200

    private_key_2 = make_device_keypair()
    body2 = build_activation_body(private_key_2, full_key=full_key, installation_id="ad6-dev-b")
    resp2 = _activate(client, body2)
    assert resp2.status_code == 400
    assert resp2.get_json()["reason_code"] == "DEVICE_LIMIT_REACHED"

    with app.app_context():
        from app.commercial_ops.device_slot_ops import add_devices
        from app.extensions import db_session
        from app.models.licensing import License

        lic = db_session.get(License, license_id)
        add_devices(lic, additional_devices=1, reason="Customer bought a second till", actor_staff_user_id=actor_id)

    # Same device retries activation (fresh request envelope -- a real retry
    # generates a new nonce/idempotency_key/timestamp, not a byte-identical
    # replay of body2, which the activation endpoint's own replay/idempotency
    # cache would just answer from cache rather than re-evaluating). No code
    # change, no re-issuance, no waiting for a check-in cycle: activation
    # always reads License.device_limit live (SELECT ... FOR UPDATE in the
    # same request), so the very next attempt after add_devices() commits
    # succeeds.
    body3 = build_activation_body(private_key_2, full_key=full_key, installation_id="ad6-dev-b")
    resp3 = _activate(client, body3)
    assert resp3.status_code == 200
    assert resp3.get_json()["reason_code"] == "ACTIVATION_APPROVED"
