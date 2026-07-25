import base64
from datetime import datetime, timedelta, timezone

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization

from commercial_runtime.licensing_contracts.assertion_verifier import (
    AssertionVerificationError,
    verify_assertion,
)
from commercial_runtime.licensing_contracts.canonical import canonicalize_bytes
from commercial_runtime.licensing_contracts.trust_store import OwnerTrustStore

NOW = datetime.now(timezone.utc)


def _b64_pub(private_key):
    raw = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw
    )
    return base64.b64encode(raw).decode("ascii")


def _standard_policy():
    return {
        "check_in_interval_seconds": 86400,
        "retry_interval_seconds": 3600,
        "offline_grace_seconds": 1209600,
        "warning_start_seconds": 864000,
        "hard_expiry_behavior": "RESTRICT_COMMERCIAL_FEATURES_PRESERVE_DATA",
        "clock_rollback_tolerance_seconds": 300,
        "assertion_refresh_threshold_seconds": 86400,
    }


def _payload(**overrides):
    base = {
        "assertion_id": "a-1",
        "issuer": "aura-owner",
        "product_code": "AURA_RETAIL",
        "license_public_id": "lic-1",
        "installation_public_id": "inst-1",
        "platform": "WINDOWS",
        "app_version_policy": "1.0.0-rc.2",
        "release_channel": "rc",
        "issued_at": NOW.isoformat(),
        "not_before": (NOW - timedelta(minutes=5)).isoformat(),
        "expires_at": (NOW + timedelta(days=1)).isoformat(),
        "license_status": "ACTIVE",
        "installation_status": "ACTIVE",
        "subscription_status": "ACTIVE",
        "allowed_device_count": 2,
        "device_key_fingerprint": "fp-device-1",
        "entitlements": {"max_devices": 2},
        "offline_policy": _standard_policy(),
        "contract_version": "v1",
    }
    base.update(overrides)
    return base


def _envelope(signing_key, key_id, payload):
    sig = signing_key.sign(canonicalize_bytes(payload))
    return {
        "payload": payload,
        "signing_key_id": key_id,
        "algorithm": "ed25519",
        "assertion_version": 1,
        "signature": base64.b64encode(sig).decode("ascii"),
    }


@pytest.fixture
def owner_key():
    return Ed25519PrivateKey.generate()


@pytest.fixture
def trust_store(tmp_path, owner_key):
    s = OwnerTrustStore(tmp_path / "trust.json")
    s.bootstrap_from_anchor({"keys": [{"key_id": "owner-1", "public_key": _b64_pub(owner_key), "algorithm": "ed25519"}]})
    return s


def _verify(envelope, trust_store, **kwargs):
    defaults = dict(
        trust_store=trust_store,
        expected_product_code="AURA_RETAIL",
        expected_platform="WINDOWS",
        expected_installation_id="inst-1",
        expected_device_key_fingerprint="fp-device-1",
        trusted_now=NOW,
    )
    defaults.update(kwargs)
    return verify_assertion(envelope, **defaults)


def test_valid_assertion_verifies(owner_key, trust_store):
    env = _envelope(owner_key, "owner-1", _payload())
    result = _verify(env, trust_store)
    assert result.evidence.license_status == "ACTIVE"
    assert result.signing_key_id == "owner-1"


def test_unknown_signing_key_rejected(owner_key, trust_store):
    env = _envelope(owner_key, "some-other-key", _payload())
    with pytest.raises(AssertionVerificationError) as exc:
        _verify(env, trust_store)
    assert exc.value.reason_code == "UNKNOWN_SIGNING_KEY"


def test_tampered_payload_rejected(owner_key, trust_store):
    env = _envelope(owner_key, "owner-1", _payload())
    env["payload"]["allowed_device_count"] = 999
    with pytest.raises(AssertionVerificationError) as exc:
        _verify(env, trust_store)
    assert exc.value.reason_code == "ASSERTION_VERIFICATION_FAILED"


def test_expired_assertion_rejected(owner_key, trust_store):
    env = _envelope(owner_key, "owner-1", _payload(expires_at=(NOW - timedelta(days=1)).isoformat()))
    with pytest.raises(AssertionVerificationError) as exc:
        _verify(env, trust_store)
    assert exc.value.reason_code == "ASSERTION_EXPIRED"


def test_not_yet_valid_assertion_rejected(owner_key, trust_store):
    env = _envelope(owner_key, "owner-1", _payload(not_before=(NOW + timedelta(days=1)).isoformat()))
    with pytest.raises(AssertionVerificationError) as exc:
        _verify(env, trust_store)
    assert exc.value.reason_code == "ASSERTION_NOT_YET_VALID"


def test_small_clock_skew_within_tolerance_accepted(owner_key, trust_store):
    """Phase 7V-A: a real physical Android device's clock was confirmed
    ~1 second behind the signing host's, which -- before this tolerance
    existed -- rejected every genuinely valid, freshly-issued assertion as
    ASSERTION_NOT_YET_VALID. A modest clock-skew allowance (mirroring
    Owner's own ACTIVATION_TIMESTAMP_SKEW_SECONDS concept for request
    timestamps) must accept a small amount of client/server clock drift on
    both the not_before and expires_at boundaries."""
    env = _envelope(
        owner_key, "owner-1",
        _payload(
            not_before=(NOW + timedelta(seconds=30)).isoformat(),
            expires_at=(NOW - timedelta(seconds=30)).isoformat(),
        ),
    )
    result = _verify(env, trust_store)
    assert result.signing_key_id == "owner-1"


def test_clock_skew_beyond_tolerance_still_rejected(owner_key, trust_store):
    env = _envelope(owner_key, "owner-1", _payload(not_before=(NOW + timedelta(minutes=5)).isoformat()))
    with pytest.raises(AssertionVerificationError) as exc:
        _verify(env, trust_store)
    assert exc.value.reason_code == "ASSERTION_NOT_YET_VALID"


def test_wrong_product_rejected(owner_key, trust_store):
    env = _envelope(owner_key, "owner-1", _payload(product_code="AURA_CLINIC"))
    with pytest.raises(AssertionVerificationError) as exc:
        _verify(env, trust_store)
    assert exc.value.reason_code == "ASSERTION_PRODUCT_MISMATCH"


def test_wrong_platform_rejected(owner_key, trust_store):
    env = _envelope(owner_key, "owner-1", _payload(platform="ANDROID"))
    with pytest.raises(AssertionVerificationError) as exc:
        _verify(env, trust_store)
    assert exc.value.reason_code == "ASSERTION_PLATFORM_MISMATCH"


def test_wrong_installation_rejected(owner_key, trust_store):
    env = _envelope(owner_key, "owner-1", _payload(installation_public_id="some-other-installation"))
    with pytest.raises(AssertionVerificationError) as exc:
        _verify(env, trust_store)
    assert exc.value.reason_code == "ASSERTION_INSTALLATION_MISMATCH"


def test_wrong_device_fingerprint_rejected(owner_key, trust_store):
    # Simulates a stolen assertion replayed from a different device.
    env = _envelope(owner_key, "owner-1", _payload(device_key_fingerprint="fp-attacker-device"))
    with pytest.raises(AssertionVerificationError) as exc:
        _verify(env, trust_store)
    assert exc.value.reason_code == "ASSERTION_DEVICE_MISMATCH"


def test_unsupported_algorithm_rejected(owner_key, trust_store):
    env = _envelope(owner_key, "owner-1", _payload())
    env["algorithm"] = "rsa"
    with pytest.raises(AssertionVerificationError):
        _verify(env, trust_store)


def test_forbidden_field_rejected(owner_key, trust_store):
    payload = _payload()
    payload["patient_name"] = "should never appear"
    env = _envelope(owner_key, "owner-1", payload)
    with pytest.raises(AssertionVerificationError) as exc:
        _verify(env, trust_store)
    assert exc.value.reason_code == "ASSERTION_FORBIDDEN_FIELD"


def test_forbidden_marker_inside_allowed_field_rejected(owner_key, trust_store):
    payload = _payload(release_channel="rc-with-patient-data-leak")
    env = _envelope(owner_key, "owner-1", payload)
    with pytest.raises(AssertionVerificationError) as exc:
        _verify(env, trust_store)
    assert exc.value.reason_code == "ASSERTION_FORBIDDEN_FIELD"


def test_malformed_envelope_missing_field_rejected(owner_key, trust_store):
    env = _envelope(owner_key, "owner-1", _payload())
    del env["signing_key_id"]
    with pytest.raises(AssertionVerificationError):
        _verify(env, trust_store)


def test_unsafe_hard_expiry_behavior_rejected(owner_key, trust_store):
    policy = _standard_policy()
    policy["hard_expiry_behavior"] = "DELETE_EVERYTHING"
    env = _envelope(owner_key, "owner-1", _payload(offline_policy=policy))
    with pytest.raises(AssertionVerificationError):
        _verify(env, trust_store)


def test_naive_dates_rejected(owner_key, trust_store):
    payload = _payload()
    payload["not_before"] = "2026-07-22T10:00:00"  # no timezone
    env = _envelope(owner_key, "owner-1", payload)
    with pytest.raises(AssertionVerificationError):
        _verify(env, trust_store)
