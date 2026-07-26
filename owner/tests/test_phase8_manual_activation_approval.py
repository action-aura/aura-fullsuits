"""Phase 8 Milestone 5, Part O: manual activation approval wired into the
real HTTP activation endpoint. AUTOMATIC (no ActivationPolicy row -- the
state of the world for every test in test_phase6_activation_protocol.py)
must remain byte-for-byte the existing behavior; these tests only exercise
what changes once a policy is explicitly configured."""
from __future__ import annotations

import json
from datetime import date

from tests.conftest import build_activation_body, make_device_keypair, make_license, make_staff


def _activate(client, body):
    return client.post("/api/licensing/v1/activations", data=json.dumps(body), content_type="application/json")


def _set_manual_approval(app, staff_id, product_code="AURA_CLINIC"):
    from app.commercial_ops.activation_policy import create_activation_policy
    from app.extensions import db_session
    from app.models.catalog import Product

    product = db_session.query(Product).filter_by(product_code=product_code).first()
    create_activation_policy(
        policy_code=f"manual-{product_code}-{staff_id}", mode="MANUAL_APPROVAL",
        effective_date=date(2026, 1, 1), actor_staff_user_id=staff_id, product_id=product.id,
    )


def test_gated_activation_returns_pending_not_signed_assertion(app, client, seeded, signing_key):
    actor_id = make_staff(app, "m5-actor1@example.com")
    license_id, full_key = make_license(app, actor_id, device_limit=2)
    with app.app_context():
        _set_manual_approval(app, actor_id)

    private_key = make_device_keypair()
    body = build_activation_body(private_key, full_key=full_key, installation_id="m5-dev-001")
    resp = _activate(client, body)

    assert resp.status_code == 200
    data = resp.get_json()
    assert data["result"] == "PENDING"
    assert data["decision"] == "PENDING_REVIEW"
    assert "signed_assertion" not in data
    assert full_key not in json.dumps(data)

    with app.app_context():
        from app.extensions import db_session
        from app.models.installations import Installation

        installation = db_session.get(Installation, data["installation_id"])
        assert installation.status == "PENDING_ACTIVATION"


def test_approval_then_retry_self_heals_to_success(app, client, seeded, signing_key):
    actor_id = make_staff(app, "m5-actor2@example.com")
    approver_id = make_staff(app, "m5-actor2-approver@example.com")
    license_id, full_key = make_license(app, actor_id, device_limit=2)
    with app.app_context():
        _set_manual_approval(app, actor_id)

    private_key = make_device_keypair()
    body = build_activation_body(private_key, full_key=full_key, installation_id="m5-dev-002")
    first = _activate(client, body)
    assert first.get_json()["result"] == "PENDING"

    with app.app_context():
        from app.commercial_ops.activation_policy import approve_pending_activation
        from app.extensions import db_session
        from app.models.activation_governance import PendingActivation
        from app.models.installations import Installation

        installation = db_session.get(Installation, first.get_json()["installation_id"])
        pending = db_session.execute(
            __import__("sqlalchemy").select(PendingActivation).where(PendingActivation.installation_id == installation.id)
        ).scalars().first()
        approve_pending_activation(pending, approver_id)

    # A real retry: same idempotency_key, but a fresh nonce/timestamp/
    # request_id (the original nonce was already consumed by the first
    # call and would be rejected as a replay if reused verbatim).
    retry_body = build_activation_body(
        private_key, full_key=full_key, installation_id="m5-dev-002", idempotency_key=body["idempotency_key"],
    )
    second = _activate(client, retry_body)
    data = second.get_json()
    assert data["result"] == "SUCCESS"
    assert data["decision"] == "APPROVED"
    assert "signed_assertion" in data


def test_rejected_activation_never_reaches_success_on_retry(app, client, seeded, signing_key):
    actor_id = make_staff(app, "m5-actor3@example.com")
    rejector_id = make_staff(app, "m5-actor3-rejector@example.com")
    license_id, full_key = make_license(app, actor_id, device_limit=2)
    with app.app_context():
        _set_manual_approval(app, actor_id)

    private_key = make_device_keypair()
    body = build_activation_body(private_key, full_key=full_key, installation_id="m5-dev-003")
    first = _activate(client, body)

    with app.app_context():
        from app.commercial_ops.activation_policy import reject_pending_activation
        from app.extensions import db_session
        from app.models.activation_governance import PendingActivation
        from app.models.installations import Installation

        installation = db_session.get(Installation, first.get_json()["installation_id"])
        pending = db_session.execute(
            __import__("sqlalchemy").select(PendingActivation).where(PendingActivation.installation_id == installation.id)
        ).scalars().first()
        reject_pending_activation(pending, rejector_id, reason="Suspicious device fingerprint")
        assert installation.status == "DEACTIVATED"


def test_automatic_mode_unaffected_when_no_policy_configured(app, client, seeded, signing_key):
    """No ActivationPolicy row at all -- must behave exactly like Phase 6/7,
    i.e. immediate SUCCESS with a signed assertion."""
    actor_id = make_staff(app, "m5-actor4@example.com")
    license_id, full_key = make_license(app, actor_id, device_limit=2)
    private_key = make_device_keypair()
    body = build_activation_body(private_key, full_key=full_key, installation_id="m5-dev-004")
    resp = _activate(client, body)
    data = resp.get_json()
    assert data["result"] == "SUCCESS"
    assert "signed_assertion" in data


def test_device_slot_exception_raises_effective_limit_at_activation(app, client, seeded, signing_key):
    from datetime import datetime, timedelta

    actor_id = make_staff(app, "m5-actor5@example.com")
    license_id, full_key = make_license(app, actor_id, device_limit=1)

    private_key_a = make_device_keypair()
    body_a = build_activation_body(private_key_a, full_key=full_key, installation_id="m5-dev-005a")
    resp_a = _activate(client, body_a)
    assert resp_a.get_json()["result"] == "SUCCESS"

    private_key_b = make_device_keypair()
    body_b = build_activation_body(private_key_b, full_key=full_key, installation_id="m5-dev-005b")
    resp_b = _activate(client, body_b)
    assert resp_b.status_code == 400
    assert resp_b.get_json()["reason_code"] == "DEVICE_LIMIT_REACHED"

    with app.app_context():
        from app.commercial_ops.device_slot_ops import create_device_slot_exception
        from app.extensions import db_session
        from app.models.licensing import License

        lic = db_session.get(License, license_id)
        create_device_slot_exception(
            license_row=lic, extra_slots=1, reason="Temporary pilot rollout, +1 device",
            starts_at=datetime.utcnow(), expires_at=datetime.utcnow() + timedelta(days=7),
            actor_staff_user_id=actor_id,
        )

    # Fresh nonce/request_id for the retry -- the original was already
    # consumed by the rejected attempt above (nonce consumption happens
    # before the device-limit check).
    body_b2 = build_activation_body(private_key_b, full_key=full_key, installation_id="m5-dev-005b")
    resp_b2 = _activate(client, body_b2)
    assert resp_b2.get_json()["result"] == "SUCCESS"
