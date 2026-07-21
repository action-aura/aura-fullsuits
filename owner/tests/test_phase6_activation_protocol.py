"""Part Y ACTIVATION: the real HTTP surface, real Ed25519 signatures, real
Postgres-backed replay/idempotency/rate-limit -- no mocks."""
from __future__ import annotations

import json
import uuid

from tests.conftest import build_activation_body, make_device_keypair, make_license, make_staff


def _activate(client, body):
    return client.post("/api/licensing/v1/activations", data=json.dumps(body), content_type="application/json")


def test_valid_activation_approved(app, client, seeded, signing_key):
    actor_id = make_staff(app, "actor1@example.com")
    license_id, full_key = make_license(app, actor_id, device_limit=2)
    private_key = make_device_keypair()
    body = build_activation_body(private_key, full_key=full_key, installation_id="dev-001")

    resp = _activate(client, body)
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["result"] == "SUCCESS"
    assert data["reason_code"] == "ACTIVATION_APPROVED"
    assert "signed_assertion" in data
    assert full_key not in json.dumps(data)  # never echoed back


def test_invalid_license_key_rejected_generically(app, client, seeded, signing_key):
    actor_id = make_staff(app, "actor2@example.com")
    make_license(app, actor_id)
    private_key = make_device_keypair()
    body = build_activation_body(private_key, full_key="AURA-CLN-1-ZZZZ-ZZZZ-ZZZZ-ZZZZ-ZZZZ", installation_id="dev-002")

    resp = _activate(client, body)
    assert resp.status_code == 400
    data = resp.get_json()
    # Anti-enumeration: never LICENSE_NOT_FOUND publicly.
    assert data["reason_code"] == "ACTIVATION_REJECTED"


def test_suspended_license_rejected(app, client, seeded, signing_key):
    actor_id = make_staff(app, "actor3@example.com")
    license_id, full_key = make_license(app, actor_id)
    with app.app_context():
        from app.extensions import db_session
        from app.licensing.services import transition_license
        from app.models.licensing import License

        lic = db_session.get(License, license_id)
        transition_license(lic, "ACTIVE", actor_id)
        transition_license(lic, "SUSPENDED", actor_id)
    private_key = make_device_keypair()
    body = build_activation_body(private_key, full_key=full_key, installation_id="dev-003")
    resp = _activate(client, body)
    assert resp.status_code == 400
    assert resp.get_json()["reason_code"] == "ACTIVATION_REJECTED"


def test_revoked_license_rejected(app, client, seeded, signing_key):
    actor_id = make_staff(app, "actor4@example.com")
    license_id, full_key = make_license(app, actor_id)
    with app.app_context():
        from app.extensions import db_session
        from app.licensing.services import transition_license
        from app.models.licensing import License

        lic = db_session.get(License, license_id)
        transition_license(lic, "REVOKED", actor_id, reason="test")
    private_key = make_device_keypair()
    body = build_activation_body(private_key, full_key=full_key, installation_id="dev-004")
    resp = _activate(client, body)
    assert resp.status_code == 400


def test_wrong_product_rejected(app, client, seeded, signing_key):
    actor_id = make_staff(app, "actor5@example.com")
    license_id, full_key = make_license(app, actor_id, product_code="AURA_CLINIC")
    private_key = make_device_keypair()
    body = build_activation_body(private_key, full_key=full_key, installation_id="dev-005", product_code="AURA_RETAIL")
    resp = _activate(client, body)
    assert resp.status_code == 400
    assert resp.get_json()["reason_code"] == "PRODUCT_MISMATCH"


def test_disallowed_platform_rejected(app, client, seeded, signing_key):
    actor_id = make_staff(app, "actor6@example.com")
    license_id, full_key = make_license(app, actor_id)
    with app.app_context():
        from app.extensions import db_session
        from app.models.licensing import License

        lic = db_session.get(License, license_id)
        lic.allowed_platforms = "WINDOWS"  # ANDROID not allowed
        db_session.commit()
    private_key = make_device_keypair()
    body = build_activation_body(private_key, full_key=full_key, installation_id="dev-006", platform="ANDROID")
    resp = _activate(client, body)
    assert resp.status_code == 400
    assert resp.get_json()["reason_code"] == "PLATFORM_NOT_ALLOWED"


def test_unsupported_contract_version_rejected(app, client, seeded, signing_key):
    actor_id = make_staff(app, "actor7@example.com")
    license_id, full_key = make_license(app, actor_id)
    private_key = make_device_keypair()
    body = build_activation_body(private_key, full_key=full_key, installation_id="dev-007", contract_version="v99")
    resp = _activate(client, body)
    assert resp.status_code == 400
    assert resp.get_json()["reason_code"] == "UNSUPPORTED_CONTRACT_VERSION"


def test_device_limit_reached(app, client, seeded, signing_key):
    actor_id = make_staff(app, "actor8@example.com")
    license_id, full_key = make_license(app, actor_id, device_limit=1)

    private_key_1 = make_device_keypair()
    resp1 = _activate(client, build_activation_body(private_key_1, full_key=full_key, installation_id="dev-008a"))
    assert resp1.status_code == 200

    private_key_2 = make_device_keypair()
    resp2 = _activate(client, build_activation_body(private_key_2, full_key=full_key, installation_id="dev-008b"))
    assert resp2.status_code == 400
    assert resp2.get_json()["reason_code"] == "DEVICE_LIMIT_REACHED"


def test_idempotent_retry_returns_same_result_no_extra_slot(app, client, seeded, signing_key):
    actor_id = make_staff(app, "actor9@example.com")
    license_id, full_key = make_license(app, actor_id, device_limit=1)
    private_key = make_device_keypair()
    idem_key = str(uuid.uuid4())

    body1 = build_activation_body(private_key, full_key=full_key, installation_id="dev-009", idempotency_key=idem_key)
    resp1 = _activate(client, body1)
    assert resp1.status_code == 200
    data1 = resp1.get_json()

    body2 = build_activation_body(private_key, full_key=full_key, installation_id="dev-009", idempotency_key=idem_key)
    resp2 = _activate(client, body2)
    assert resp2.status_code == 200
    data2 = resp2.get_json()

    assert data1["response_id"] == data2["response_id"]
    assert data1["installation_id"] == data2["installation_id"]

    with app.app_context():
        from app.extensions import db_session
        from app.models.installations import Installation
        from sqlalchemy import select

        count = len(db_session.execute(select(Installation).where(Installation.license_id == license_id)).scalars().all())
        assert count == 1  # the retry did not consume a second slot


def test_replay_of_identical_signed_request_rejected(app, client, seeded, signing_key):
    actor_id = make_staff(app, "actor10@example.com")
    license_id, full_key = make_license(app, actor_id, device_limit=2)
    private_key = make_device_keypair()
    body = build_activation_body(private_key, full_key=full_key, installation_id="dev-010")

    resp1 = _activate(client, body)
    assert resp1.status_code == 200
    resp2 = _activate(client, body)  # byte-identical replay, including the same nonce
    assert resp2.status_code == 400
    assert resp2.get_json()["reason_code"] == "NONCE_REUSED"


def test_invalid_signature_rejected(app, client, seeded, signing_key):
    actor_id = make_staff(app, "actor11@example.com")
    license_id, full_key = make_license(app, actor_id)
    private_key = make_device_keypair()
    body = build_activation_body(private_key, full_key=full_key, installation_id="dev-011")
    body["signature"] = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=="
    resp = _activate(client, body)
    assert resp.status_code == 400
    assert resp.get_json()["reason_code"] == "INVALID_SIGNATURE"


def test_full_key_never_appears_anywhere_in_db_logs_or_error_response(app, client, seeded, signing_key):
    actor_id = make_staff(app, "actor12@example.com")
    license_id, full_key = make_license(app, actor_id)
    private_key = make_device_keypair()

    # A deliberately WRONG key first (error path), then the real one.
    bad_body = build_activation_body(private_key, full_key="AURA-CLN-1-WRNG-WRNG-WRNG-WRNG-WRNG", installation_id="dev-012")
    bad_resp = _activate(client, bad_body)
    assert full_key not in json.dumps(bad_resp.get_json())

    good_body = build_activation_body(private_key, full_key=full_key, installation_id="dev-012")
    good_resp = _activate(client, good_body)
    assert full_key not in json.dumps(good_resp.get_json())

    with app.app_context():
        from app.extensions import db_session
        from app.models.audit import AuditLog
        from app.models.licensing import License
        from app.models.licensing_service import ActivationRequest
        from sqlalchemy import select

        lic = db_session.get(License, license_id)
        assert full_key not in (lic.key_secret_hmac or "")
        for row in db_session.execute(select(ActivationRequest)).scalars().all():
            assert full_key not in json.dumps({"request_id": row.request_id, "reason_code": row.reason_code})
        for row in db_session.execute(select(AuditLog)).scalars().all():
            assert full_key not in json.dumps(row.after_state_redacted or {})
            assert full_key not in json.dumps(row.before_state_redacted or {})


def test_no_payload_rejected(client, seeded, signing_key):
    resp = client.post("/api/licensing/v1/activations", data="not json", content_type="application/json")
    assert resp.status_code == 400


def test_wrong_content_type_rejected(client, seeded, signing_key):
    resp = client.post("/api/licensing/v1/activations", data="{}", content_type="text/plain")
    assert resp.status_code == 415


def test_service_unavailable_without_active_signing_key(client, seeded):
    """Fail-closed: no active signing key means every request is rejected
    before any decision logic runs, not silently allowed through unsigned."""
    resp = client.post("/api/licensing/v1/activations", data="{}", content_type="application/json")
    assert resp.status_code == 503
    assert resp.get_json()["reason_code"] == "SIGNING_KEY_UNAVAILABLE"
