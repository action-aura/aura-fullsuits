"""Part Y CRYPTO/DEVICE IDENTITY/SIGNED ASSERTIONS: canonical serialization,
signing-key lifecycle, device proof-of-possession, assertion sign/verify."""
from __future__ import annotations

import pytest

from tests.conftest import make_device_keypair, public_key_b64, sign_body


def test_canonical_is_deterministic_regardless_of_key_order():
    from app.licensing_service.canonical import canonicalize

    a = canonicalize({"b": 1, "a": 2})
    b = canonicalize({"a": 2, "b": 1})
    assert a == b


def test_canonical_rejects_non_finite_numbers():
    from app.licensing_service.canonical import CanonicalizationError, canonicalize

    with pytest.raises(CanonicalizationError):
        canonicalize({"x": float("nan")})


def test_signing_key_generate_activate_rotate(app):
    with app.app_context():
        from app.licensing_service.signing import activate_signing_key, generate_signing_key, get_active_signing_key, rotate_signing_key

        first = generate_signing_key(app.config["SIGNING_KEY_DIRECTORY"])
        assert first.status == "DRAFT"
        activate_signing_key(app.config["SIGNING_KEY_DIRECTORY"], first.key_id)
        assert get_active_signing_key().key_id == first.key_id

        second = rotate_signing_key(app.config["SIGNING_KEY_DIRECTORY"], "test_rotation")
        assert get_active_signing_key().key_id == second.key_id
        assert second.key_id != first.key_id

        from app.extensions import db_session
        from app.models.licensing_service import SigningKey
        from sqlalchemy import select

        retired = db_session.execute(select(SigningKey).where(SigningKey.key_id == first.key_id)).scalars().first()
        assert retired.status == "RETIRED"  # not REVOKED -- still verifiable


def test_signing_key_health_check(app, signing_key):
    with app.app_context():
        from app.licensing_service.signing import verify_signing_key_health

        result = verify_signing_key_health(app.config["SIGNING_KEY_DIRECTORY"])
        assert result["status"] == "OK"


def test_signing_key_path_traversal_rejected(app):
    with app.app_context():
        from app.licensing_service.signing import SigningKeyError, _safe_key_path

        with pytest.raises(SigningKeyError):
            _safe_key_path(app.config["SIGNING_KEY_DIRECTORY"], "../../etc/passwd")


def test_export_public_keys_never_includes_private_material(app, signing_key):
    with app.app_context():
        from app.licensing_service.signing import export_public_keys

        keys = export_public_keys()
        assert len(keys) == 1
        assert set(keys[0].keys()) == {"key_id", "algorithm", "public_key", "use", "status", "valid_from", "retired_at"}


def test_device_signature_verification_roundtrip():
    from app.licensing_service.canonical import canonicalize_bytes
    from app.licensing_service.device_identity import validate_public_key, verify_signature

    private_key = make_device_keypair()
    public_key = validate_public_key("ed25519", public_key_b64(private_key))
    body = {"a": 1, "b": "x"}
    signed = sign_body(private_key, body)
    assert verify_signature(public_key, canonicalize_bytes(body), signed["signature"]) is True


def test_device_signature_altered_payload_rejected():
    from app.licensing_service.canonical import canonicalize_bytes
    from app.licensing_service.device_identity import validate_public_key, verify_signature

    private_key = make_device_keypair()
    public_key = validate_public_key("ed25519", public_key_b64(private_key))
    signed = sign_body(private_key, {"a": 1})
    tampered_bytes = canonicalize_bytes({"a": 2})  # different from what was actually signed
    assert verify_signature(public_key, tampered_bytes, signed["signature"]) is False


def test_device_wrong_public_key_rejected():
    from app.licensing_service.canonical import canonicalize_bytes
    from app.licensing_service.device_identity import validate_public_key, verify_signature

    signer = make_device_keypair()
    other = make_device_keypair()
    body = {"a": 1}
    signed = sign_body(signer, body)
    wrong_public_key = validate_public_key("ed25519", public_key_b64(other))
    assert verify_signature(wrong_public_key, canonicalize_bytes(body), signed["signature"]) is False


def test_device_malformed_public_key_rejected():
    from app.licensing_service.device_identity import DeviceIdentityError, validate_public_key

    with pytest.raises(DeviceIdentityError):
        validate_public_key("ed25519", "not-valid-base64!!!")


def test_device_unsupported_algorithm_rejected():
    from app.licensing_service.device_identity import DeviceIdentityError, validate_public_key

    private_key = make_device_keypair()
    with pytest.raises(DeviceIdentityError):
        validate_public_key("rsa-4096", public_key_b64(private_key))


def test_assertion_sign_and_verify(app, signing_key):
    with app.app_context():
        from app.licensing_service.assertions import sign_assertion, verify_assertion

        payload = {
            "assertion_id": "test-1", "issuer": "aura-owner", "issued_at": "2026-01-01T00:00:00+00:00",
            "not_before": "2026-01-01T00:00:00+00:00", "expires_at": "2099-01-01T00:00:00+00:00",
        }
        envelope = sign_assertion(payload, app.config["SIGNING_KEY_DIRECTORY"])
        ok, reason = verify_assertion(envelope)
        assert ok is True
        assert reason is None


def test_assertion_tampering_detected(app, signing_key):
    with app.app_context():
        from app.licensing_service.assertions import sign_assertion, verify_assertion

        payload = {
            "assertion_id": "test-2", "issuer": "aura-owner", "issued_at": "2026-01-01T00:00:00+00:00",
            "not_before": "2026-01-01T00:00:00+00:00", "expires_at": "2099-01-01T00:00:00+00:00",
        }
        envelope = sign_assertion(payload, app.config["SIGNING_KEY_DIRECTORY"])
        envelope["payload"]["issuer"] = "not-aura-owner"
        ok, reason = verify_assertion(envelope)
        assert ok is False
        assert reason == "INVALID_SIGNATURE"


def test_assertion_unknown_key_id_rejected(app, signing_key):
    with app.app_context():
        from app.licensing_service.assertions import sign_assertion, verify_assertion

        payload = {
            "assertion_id": "test-3", "issuer": "aura-owner", "issued_at": "2026-01-01T00:00:00+00:00",
            "not_before": "2026-01-01T00:00:00+00:00", "expires_at": "2099-01-01T00:00:00+00:00",
        }
        envelope = sign_assertion(payload, app.config["SIGNING_KEY_DIRECTORY"])
        envelope["signing_key_id"] = "totally-unknown-key-id"
        ok, reason = verify_assertion(envelope)
        assert ok is False
        assert reason == "SIGNING_KEY_UNAVAILABLE"


def test_assertion_expired_rejected(app, signing_key):
    with app.app_context():
        from app.licensing_service.assertions import sign_assertion, verify_assertion

        payload = {
            "assertion_id": "test-4", "issuer": "aura-owner", "issued_at": "2000-01-01T00:00:00+00:00",
            "not_before": "2000-01-01T00:00:00+00:00", "expires_at": "2000-01-02T00:00:00+00:00",
        }
        envelope = sign_assertion(payload, app.config["SIGNING_KEY_DIRECTORY"])
        ok, reason = verify_assertion(envelope)
        assert ok is False
        assert reason == "INVALID_TIMESTAMP"


def test_assertion_not_yet_valid_rejected(app, signing_key):
    with app.app_context():
        from app.licensing_service.assertions import sign_assertion, verify_assertion

        payload = {
            "assertion_id": "test-5", "issuer": "aura-owner", "issued_at": "2099-01-01T00:00:00+00:00",
            "not_before": "2099-01-01T00:00:00+00:00", "expires_at": "2099-06-01T00:00:00+00:00",
        }
        envelope = sign_assertion(payload, app.config["SIGNING_KEY_DIRECTORY"])
        ok, reason = verify_assertion(envelope)
        assert ok is False
        assert reason == "INVALID_TIMESTAMP"


def test_assertion_forbidden_field_rejected():
    from app.licensing_service.assertions import AssertionError_, build_assertion_payload

    with pytest.raises(AssertionError_):
        from app.licensing_service.assertions import _guard_payload

        _guard_payload({"license_key": "should-never-be-here"})


def test_revoked_signing_key_never_trusted(app, signing_key):
    with app.app_context():
        from app.licensing_service.assertions import sign_assertion, verify_assertion
        from app.licensing_service.signing import revoke_signing_key

        payload = {
            "assertion_id": "test-6", "issuer": "aura-owner", "issued_at": "2026-01-01T00:00:00+00:00",
            "not_before": "2026-01-01T00:00:00+00:00", "expires_at": "2099-01-01T00:00:00+00:00",
        }
        envelope = sign_assertion(payload, app.config["SIGNING_KEY_DIRECTORY"])
        revoke_signing_key(signing_key, "test_compromise")
        ok, reason = verify_assertion(envelope)
        assert ok is False
        assert reason == "INVALID_SIGNATURE"
