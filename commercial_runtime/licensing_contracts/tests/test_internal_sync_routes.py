import base64
import json
from datetime import datetime, timedelta, timezone

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization
from flask import Flask

from commercial_runtime.licensing_contracts.canonical import canonicalize_bytes
from commercial_runtime.licensing_contracts.device_identity import WindowsDpapiDeviceIdentityProvider
from commercial_runtime.licensing_contracts.routes import make_licensing_blueprint

pytestmark = pytest.mark.skipif(
    __import__("sys").platform != "win32", reason="Uses the real Windows DPAPI device identity provider as a stand-in signer."
)

NOW = datetime.now(timezone.utc)
SHARED_SECRET = "test-shared-secret-abc123"


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


def _payload(installation_id, device_fingerprint, **overrides):
    base = {
        "assertion_id": "a-1",
        "issuer": "aura-owner",
        "product_code": "AURA_CLINIC",
        "license_public_id": "lic-1",
        "installation_public_id": installation_id,
        "platform": "ANDROID",
        "app_version_policy": "1.0.0-rc.2",
        "release_channel": "rc",
        "issued_at": NOW.isoformat(),
        "not_before": (NOW - timedelta(minutes=5)).isoformat(),
        "expires_at": (NOW + timedelta(days=1)).isoformat(),
        "license_status": "ACTIVE",
        "installation_status": "ACTIVE",
        "subscription_status": "ACTIVE",
        "allowed_device_count": 2,
        "device_key_fingerprint": device_fingerprint,
        "entitlements": {"max_devices": 2},
        "offline_policy": _standard_policy(),
        "contract_version": "v1",
    }
    base.update(overrides)
    return base


def _envelope(owner_key, key_id, payload):
    sig = owner_key.sign(canonicalize_bytes(payload))
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
def app_with_secret(tmp_path, owner_key):
    trust_anchor_path = tmp_path / "trust_anchor.json"
    trust_anchor_path.write_text(
        json.dumps({"keys": [{"key_id": "owner-1", "public_key": _b64_pub(owner_key), "algorithm": "ed25519"}]}),
        encoding="utf-8",
    )
    flask_app = Flask(__name__)
    bp = make_licensing_blueprint(
        product_code="AURA_CLINIC",
        platform="ANDROID",
        app_version="1.0.0-rc.2",
        app_data_dir=str(tmp_path / "appdata"),
        owner_base_url="https://owner.example/api/licensing/v1",
        verify_tls=True,
        timeout_seconds=5.0,
        trust_anchor_path=trust_anchor_path,
        device_identity_factory=lambda licensing_dir: WindowsDpapiDeviceIdentityProvider(licensing_dir),
        internal_shared_secret=SHARED_SECRET,
    )
    flask_app.register_blueprint(bp)
    return flask_app


@pytest.fixture
def app_without_secret(tmp_path, owner_key):
    trust_anchor_path = tmp_path / "trust_anchor.json"
    trust_anchor_path.write_text(
        json.dumps({"keys": [{"key_id": "owner-1", "public_key": _b64_pub(owner_key), "algorithm": "ed25519"}]}),
        encoding="utf-8",
    )
    flask_app = Flask(__name__)
    bp = make_licensing_blueprint(
        product_code="AURA_RETAIL",
        platform="WINDOWS",
        app_version="1.0.0-rc.2",
        app_data_dir=str(tmp_path / "appdata"),
        owner_base_url="https://owner.example/api/licensing/v1",
        verify_tls=True,
        timeout_seconds=5.0,
        trust_anchor_path=trust_anchor_path,
        device_identity_factory=lambda licensing_dir: WindowsDpapiDeviceIdentityProvider(licensing_dir),
        # internal_shared_secret intentionally omitted -- Windows path.
    )
    flask_app.register_blueprint(bp)
    return flask_app


def _generate_device_key(flask_app, tmp_path):
    """Simulates Kotlin having already generated the device key -- required
    before any /_internal/* route makes sense, exactly like on Android."""
    provider = WindowsDpapiDeviceIdentityProvider(tmp_path / "appdata" / "licensing")
    meta = provider.generate_new_key()
    return provider, meta


def test_internal_routes_not_registered_without_shared_secret(app_without_secret):
    client = app_without_secret.test_client()
    resp = client.post("/api/licensing/_internal/sync-activation", json={})
    assert resp.status_code == 404


def test_internal_sync_activation_rejects_missing_secret(app_with_secret):
    client = app_with_secret.test_client()
    resp = client.post("/api/licensing/_internal/sync-activation", json={})
    assert resp.status_code == 403


def test_internal_sync_activation_rejects_wrong_secret(app_with_secret):
    client = app_with_secret.test_client()
    resp = client.post(
        "/api/licensing/_internal/sync-activation",
        json={},
        headers={"X-Aura-Internal-Secret": "wrong-secret"},
    )
    assert resp.status_code == 403


def test_internal_sync_activation_succeeds_with_correct_secret(app_with_secret, owner_key, tmp_path):
    provider, meta = _generate_device_key(app_with_secret, tmp_path)
    envelope = _envelope(owner_key, "owner-1", _payload("owner-assigned-inst-1", meta.public_key_fingerprint))
    body = {"result": "SUCCESS", "installation_id": "owner-assigned-inst-1", "signed_assertion": envelope}

    client = app_with_secret.test_client()
    resp = client.post(
        "/api/licensing/_internal/sync-activation",
        json=body,
        headers={"X-Aura-Internal-Secret": SHARED_SECRET},
    )
    assert resp.status_code == 200
    assert resp.get_json()["result"] == "SUCCESS"

    status_resp = client.get("/api/licensing/status")
    assert status_resp.get_json()["installation_id"] == "owner-assigned-inst-1"


def test_internal_sync_activation_rejects_forged_assertion_even_with_correct_secret(app_with_secret, tmp_path):
    # The shared secret proves "this came from our own Kotlin process," not
    # "this is a real Owner response" -- a forged/tampered envelope must
    # still be rejected by independent verification.
    provider, meta = _generate_device_key(app_with_secret, tmp_path)
    attacker_key = Ed25519PrivateKey.generate()
    envelope = _envelope(attacker_key, "owner-1", _payload("owner-assigned-inst-1", meta.public_key_fingerprint))
    body = {"result": "SUCCESS", "installation_id": "owner-assigned-inst-1", "signed_assertion": envelope}

    client = app_with_secret.test_client()
    resp = client.post(
        "/api/licensing/_internal/sync-activation",
        json=body,
        headers={"X-Aura-Internal-Secret": SHARED_SECRET},
    )
    assert resp.status_code == 400

    status_resp = client.get("/api/licensing/status")
    assert status_resp.get_json()["current_state"] == "NOT_CONFIGURED"


def test_internal_sync_deactivation_succeeds(app_with_secret, owner_key, tmp_path):
    provider, meta = _generate_device_key(app_with_secret, tmp_path)
    envelope = _envelope(owner_key, "owner-1", _payload("owner-assigned-inst-1", meta.public_key_fingerprint))
    client = app_with_secret.test_client()
    client.post(
        "/api/licensing/_internal/sync-activation",
        json={"result": "SUCCESS", "installation_id": "owner-assigned-inst-1", "signed_assertion": envelope},
        headers={"X-Aura-Internal-Secret": SHARED_SECRET},
    )

    resp = client.post(
        "/api/licensing/_internal/sync-deactivation",
        json={"result": "SUCCESS", "reason_code": "DEACTIVATION_ACCEPTED"},
        headers={"X-Aura-Internal-Secret": SHARED_SECRET},
    )
    assert resp.status_code == 200
    assert resp.get_json()["state"] == "DEVICE_DEACTIVATED"
