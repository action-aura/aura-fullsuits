"""Part Y CHECK-IN: authenticated, no license key required after activation."""
from __future__ import annotations

import json

from tests.conftest import build_activation_body, build_checkin_body, make_device_keypair, make_license, make_staff


def _activate(client, body):
    return client.post("/api/licensing/v1/activations", data=json.dumps(body), content_type="application/json")


def _checkin(client, body):
    return client.post("/api/licensing/v1/check-ins", data=json.dumps(body), content_type="application/json")


def _do_activation(app, client, actor_id, device_limit=1):
    license_id, full_key = make_license(app, actor_id, device_limit=device_limit)
    private_key = make_device_keypair()
    resp = _activate(client, build_activation_body(private_key, full_key=full_key, installation_id="ci-dev"))
    assert resp.status_code == 200
    installation_id = resp.get_json()["installation_id"]
    return license_id, installation_id, private_key


def test_valid_checkin_no_license_key_required(app, client, seeded, signing_key):
    actor_id = make_staff(app, "ci1@example.com")
    _, installation_id, private_key = _do_activation(app, client, actor_id)

    body = build_checkin_body(private_key, installation_id=installation_id)
    assert "license_key" not in body
    resp = _checkin(client, body)
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["reason_code"] == "CHECK_IN_ACCEPTED"
    assert "signed_assertion" in data


def test_checkin_embeds_active_emergency_extension_without_mutating_stored_policy(app, client, seeded, signing_key):
    # Phase 8V-P6 (Part E): the real functional wiring fix. A genuine,
    # active EmergencyExtension for this installation's subscription must
    # appear in the signed check-in assertion's offline_policy fields --
    # and the stored OfflinePolicy row itself must remain untouched (proving
    # this is a per-request override, not a global mutation that could leak
    # to another license sharing the same policy code).
    actor_id = make_staff(app, "ci-ext@example.com")
    license_id, installation_id, private_key = _do_activation(app, client, actor_id)

    with app.app_context():
        from app.commercial_ops.emergency_extensions import create_emergency_extension
        from app.extensions import db_session
        from app.licensing_service.offline_policy import get_policy_for_license, seed_default_offline_policy
        from app.models.licensing import License

        seed_default_offline_policy()
        lic = db_session.get(License, license_id)
        stored_policy_before = get_policy_for_license(lic)
        assert stored_policy_before.emergency_extension_until is None

        ext = create_emergency_extension(
            subscription=lic.subscription, reason="Phase 8V-P6 regression test", duration_hours=1,
            actor_staff_user_id=actor_id, incident_reference="INC-P6-TEST",
        )
        expected_until = ext.expires_at.isoformat()

    resp = _checkin(client, build_checkin_body(private_key, installation_id=installation_id))
    assert resp.status_code == 200
    policy_payload = resp.get_json()["signed_assertion"]["payload"]["offline_policy"]
    assert policy_payload["emergency_extension_allowed"] is True
    assert policy_payload["emergency_extension_until"] == expected_until

    with app.app_context():
        lic = db_session.get(License, license_id)
        stored_policy_after = get_policy_for_license(lic)
        # Unchanged -- the override was never persisted to the stored row.
        assert stored_policy_after.emergency_extension_until is None
        assert stored_policy_after.emergency_extension_allowed is False


def test_checkin_no_active_extension_leaves_policy_unchanged(app, client, seeded, signing_key):
    actor_id = make_staff(app, "ci-noext@example.com")
    _, installation_id, private_key = _do_activation(app, client, actor_id)

    resp = _checkin(client, build_checkin_body(private_key, installation_id=installation_id))
    assert resp.status_code == 200
    policy_payload = resp.get_json()["signed_assertion"]["payload"]["offline_policy"]
    assert policy_payload["emergency_extension_allowed"] is False
    assert policy_payload["emergency_extension_until"] is None


def test_checkin_reflects_real_subscription_expiry_while_license_stays_active(app, client, seeded, signing_key):
    # Phase 8V-P6 root cause: License.status can legitimately remain ACTIVE
    # (history preserved) while Subscription.status moves to EXPIRED --
    # checkin.py must still issue a fresh assertion (not a bare 400 with no
    # assertion at all) whose subscription_status field reflects reality, so
    # the client-side policy evaluator's new commercial-restriction logic has
    # something real to act on.
    actor_id = make_staff(app, "ci-subexp@example.com")
    license_id, installation_id, private_key = _do_activation(app, client, actor_id)

    with app.app_context():
        from app.extensions import db_session
        from app.models.licensing import License
        from app.subscriptions.services import transition_subscription

        lic = db_session.get(License, license_id)
        license_status_before = lic.status
        assert license_status_before in ("ACTIVE", "ISSUED")
        transition_subscription(lic.subscription, "EXPIRED", actor_id, reason="Phase 8V-P6 regression test")

    resp = _checkin(client, build_checkin_body(private_key, installation_id=installation_id))
    assert resp.status_code == 200
    payload = resp.get_json()["signed_assertion"]["payload"]
    assert payload["subscription_status"] == "EXPIRED"
    with app.app_context():
        lic = db_session.get(License, license_id)
        assert lic.status == license_status_before  # license history genuinely preserved, not silently overwritten


def test_checkin_device_key_mismatch_rejected(app, client, seeded, signing_key):
    actor_id = make_staff(app, "ci2@example.com")
    _, installation_id, _ = _do_activation(app, client, actor_id)
    wrong_key = make_device_keypair()

    resp = _checkin(client, build_checkin_body(wrong_key, installation_id=installation_id))
    assert resp.status_code == 400
    assert resp.get_json()["reason_code"] == "INVALID_SIGNATURE"


def test_checkin_stolen_installation_id_without_private_key_fails(app, client, seeded, signing_key):
    """Exactly Principle 2: possession of the installation ID string alone,
    without the matching private key, must never be enough."""
    actor_id = make_staff(app, "ci3@example.com")
    _, installation_id, _ = _do_activation(app, client, actor_id)
    attacker_key = make_device_keypair()

    resp = _checkin(client, build_checkin_body(attacker_key, installation_id=installation_id))
    assert resp.status_code != 200


def test_checkin_unknown_installation_rejected(client, seeded, signing_key):
    from tests.conftest import make_device_keypair

    key = make_device_keypair()
    resp = _checkin(client, build_checkin_body(key, installation_id="00000000-0000-0000-0000-000000000000"))
    assert resp.status_code == 400
    assert resp.get_json()["reason_code"] == "INSTALLATION_NOT_FOUND"


def test_checkin_replay_rejected(app, client, seeded, signing_key):
    actor_id = make_staff(app, "ci4@example.com")
    _, installation_id, private_key = _do_activation(app, client, actor_id)
    body = build_checkin_body(private_key, installation_id=installation_id)

    resp1 = _checkin(client, body)
    assert resp1.status_code == 200
    resp2 = _checkin(client, body)
    assert resp2.status_code == 400
    assert resp2.get_json()["reason_code"] == "NONCE_REUSED"


def test_checkin_suspended_license_rejected(app, client, seeded, signing_key):
    actor_id = make_staff(app, "ci5@example.com")
    license_id, installation_id, private_key = _do_activation(app, client, actor_id)
    with app.app_context():
        from app.extensions import db_session
        from app.licensing.services import transition_license
        from app.models.licensing import License

        lic = db_session.get(License, license_id)
        transition_license(lic, "ACTIVE", actor_id)
        transition_license(lic, "SUSPENDED", actor_id)

    resp = _checkin(client, build_checkin_body(private_key, installation_id=installation_id))
    assert resp.status_code == 400
    assert resp.get_json()["reason_code"] == "ACTIVATION_REJECTED"  # LICENSE_SUSPENDED normalized publicly


def test_checkin_revoked_device_key_rejected(app, client, seeded, signing_key):
    actor_id = make_staff(app, "ci6@example.com")
    _, installation_id, private_key = _do_activation(app, client, actor_id)
    with app.app_context():
        from app.extensions import db_session
        from app.licensing_service import device_identity
        from app.models.installations import Installation
        import uuid as uuid_mod

        installation = db_session.get(Installation, uuid_mod.UUID(installation_id))
        key_row = device_identity.get_active_device_key(installation.id)
        device_identity.revoke_device_key(key_row, actor_id)

    resp = _checkin(client, build_checkin_body(private_key, installation_id=installation_id))
    assert resp.status_code == 400
    assert resp.get_json()["reason_code"] == "DEVICE_KEY_REVOKED"


def test_checkin_updates_last_check_in_timestamp(app, client, seeded, signing_key):
    actor_id = make_staff(app, "ci7@example.com")
    _, installation_id, private_key = _do_activation(app, client, actor_id)

    resp = _checkin(client, build_checkin_body(private_key, installation_id=installation_id))
    assert resp.status_code == 200

    with app.app_context():
        from app.extensions import db_session
        from app.models.installations import Installation
        import uuid as uuid_mod

        installation = db_session.get(Installation, uuid_mod.UUID(installation_id))
        assert installation.last_check_in_at is not None


def test_deactivation_then_reactivation_requires_new_activation(app, client, seeded, signing_key):
    actor_id = make_staff(app, "ci8@example.com")
    _, installation_id, private_key = _do_activation(app, client, actor_id)

    from tests.conftest import sign_body
    import uuid as uuid_mod
    from datetime import datetime, timezone

    deactivate_body = {
        "contract_version": "v1", "request_id": str(uuid_mod.uuid4()), "correlation_id": str(uuid_mod.uuid4()),
        "timestamp": datetime.now(timezone.utc).isoformat(), "nonce": uuid_mod.uuid4().hex,
        "installation_id": installation_id, "idempotency_key": str(uuid_mod.uuid4()),
    }
    deactivate_body = sign_body(private_key, deactivate_body)
    resp = client.post("/api/licensing/v1/deactivations", data=json.dumps(deactivate_body), content_type="application/json")
    assert resp.status_code == 200
    assert resp.get_json()["reason_code"] == "DEACTIVATION_ACCEPTED"

    # Device key was revoked as part of deactivation -- check-in now fails.
    checkin_resp = _checkin(client, build_checkin_body(private_key, installation_id=installation_id))
    assert checkin_resp.status_code == 400


def test_deactivation_idempotent(app, client, seeded, signing_key):
    actor_id = make_staff(app, "ci9@example.com")
    _, installation_id, private_key = _do_activation(app, client, actor_id)

    from tests.conftest import sign_body
    import uuid as uuid_mod
    from datetime import datetime, timezone

    idem_key = str(uuid_mod.uuid4())

    def make_deactivate_body():
        b = {
            "contract_version": "v1", "request_id": str(uuid_mod.uuid4()), "correlation_id": str(uuid_mod.uuid4()),
            "timestamp": datetime.now(timezone.utc).isoformat(), "nonce": uuid_mod.uuid4().hex,
            "installation_id": installation_id, "idempotency_key": idem_key,
        }
        return sign_body(private_key, b)

    resp1 = client.post("/api/licensing/v1/deactivations", data=json.dumps(make_deactivate_body()), content_type="application/json")
    assert resp1.status_code == 200
    resp2 = client.post("/api/licensing/v1/deactivations", data=json.dumps(make_deactivate_body()), content_type="application/json")
    assert resp2.status_code == 200
    assert resp1.get_json()["response_id"] == resp2.get_json()["response_id"]
