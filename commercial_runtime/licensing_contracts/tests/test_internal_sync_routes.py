import base64
import json
from datetime import datetime, timedelta, timezone

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization
from flask import Flask

from commercial_runtime.licensing_contracts.canonical import canonicalize_bytes
from commercial_runtime.licensing_contracts.client import LicensingClient
from commercial_runtime.licensing_contracts.device_identity import WindowsDpapiDeviceIdentityProvider
from commercial_runtime.licensing_contracts.routes import make_licensing_blueprint
from commercial_runtime.licensing_contracts.state_repository import LicenseStateRepository
from commercial_runtime.licensing_contracts.trust_store import OwnerTrustStore

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


def test_internal_reevaluate_rejects_missing_secret(app_with_secret):
    client = app_with_secret.test_client()
    resp = client.post("/api/licensing/_internal/reevaluate", json={})
    assert resp.status_code == 403


def test_internal_reevaluate_advances_stuck_state_without_a_fresh_checkin(app_with_secret, owner_key, tmp_path):
    # Phase 7V-A gate I: Android's OwnerClient makes its own HTTP call and,
    # on failure, never goes through the scheduler at all (unlike Windows'
    # run_once()) -- so without this route, current_state would stay stuck
    # at whatever was last persisted no matter how much trusted time has
    # actually elapsed. Uses a short policy and back-dates the persisted
    # last_successful_checkin_at (exactly what real elapsed wall-clock time
    # would otherwise do) so elapsed_offline already exceeds grace+retry --
    # no sleeping/time-mocking needed to observe RESTRICTED.
    provider, meta = _generate_device_key(app_with_secret, tmp_path)
    short_policy = _standard_policy()
    short_policy.update(
        check_in_interval_seconds=10, retry_interval_seconds=5, offline_grace_seconds=30, warning_start_seconds=20
    )
    payload = _payload(
        "owner-assigned-inst-1",
        meta.public_key_fingerprint,
        offline_policy=short_policy,
    )
    envelope = _envelope(owner_key, "owner-1", payload)
    client = app_with_secret.test_client()
    client.post(
        "/api/licensing/_internal/sync-activation",
        json={"result": "SUCCESS", "installation_id": "owner-assigned-inst-1", "signed_assertion": envelope},
        headers={"X-Aura-Internal-Secret": SHARED_SECRET},
    )

    db_path = tmp_path / "appdata" / "database" / "subsystems" / "licensing.db"
    repo = LicenseStateRepository(db_path)
    record = repo.load()
    record.last_successful_checkin_at = (NOW - timedelta(seconds=300)).isoformat()
    repo.save(record)

    resp = client.post(
        "/api/licensing/_internal/reevaluate",
        json={},
        headers={"X-Aura-Internal-Secret": SHARED_SECRET},
    )
    assert resp.status_code == 200
    assert resp.get_json()["current_state"] == "RESTRICTED"


# ── Signing-key rotation: the two twin ingestion routes must not drift ────
# /_internal/sync-activation and /_internal/sync-checkin are the ONLY two
# routes that take a raw Owner-signed assertion from the Kotlin layer and
# verify it here. They therefore share one failure mode -- Owner rotated its
# signing key and this install's trust store predates the rotation -- and
# must share one recovery. The refresher was originally applied to
# sync-activation only, which left Android able to recover from a rotation
# that happened BEFORE activation but permanently stuck on one that happened
# AFTER it. (Windows has never had that hole: run_once() refreshes the
# manifest before every cycle, but Android's Kotlin layer makes its own
# Owner call and never goes through run_once().) Parametrised over both
# routes on purpose: the point is that neither can be fixed alone again.


def _rotation_manifest(old_private, old_key_id, new_private, new_key_id):
    """What Owner serves from /signing-keys after a rotation: the new key,
    countersigned by the OUTGOING key this install still trusts (see
    owner/app/licensing_service/signing.py::export_signed_keyset_manifest).
    Nothing here is trusted on its own say-so -- admit_manifest() still
    requires a signature from a key already in the store."""
    body = {
        "manifest_version": 1,
        "issued_at": NOW.isoformat(),
        "keys": [
            {"key_id": old_key_id, "public_key": _b64_pub(old_private), "algorithm": "ed25519", "status": "RETIRED"},
            {"key_id": new_key_id, "public_key": _b64_pub(new_private), "algorithm": "ed25519", "status": "ACTIVE"},
        ],
        "signed_by_key_id": new_key_id,
    }
    canonical = canonicalize_bytes(
        {"manifest_version": body["manifest_version"], "issued_at": body["issued_at"], "keys": body["keys"]}
    )

    def _sig(private):
        return base64.b64encode(private.sign(canonical)).decode("ascii")

    return {
        **body,
        "signature": _sig(new_private),
        "signatures": [
            {"key_id": new_key_id, "algorithm": "ed25519", "signature": _sig(new_private)},
            {"key_id": old_key_id, "algorithm": "ed25519", "signature": _sig(old_private)},
        ],
    }


@pytest.mark.parametrize("route", ["sync-activation", "sync-checkin"])
def test_both_internal_sync_routes_recover_from_a_signing_key_rotation(
    route, app_with_secret, owner_key, tmp_path, monkeypatch
):
    provider, meta = _generate_device_key(app_with_secret, tmp_path)
    rotated_key = Ed25519PrivateKey.generate()
    monkeypatch.setattr(
        LicensingClient,
        "fetch_signing_keys",
        lambda self: _rotation_manifest(owner_key, "owner-1", rotated_key, "owner-2"),
    )
    client = app_with_secret.test_client()
    headers = {"X-Aura-Internal-Secret": SHARED_SECRET}

    if route == "sync-activation":
        # A rotation BEFORE this device ever activated -- the bundled anchor
        # was cut before the current signing key existed.
        envelope = _envelope(
            rotated_key, "owner-2", _payload("inst-rot", meta.public_key_fingerprint, assertion_id="a-rotated")
        )
        resp = client.post(
            "/api/licensing/_internal/sync-activation",
            json={"result": "SUCCESS", "installation_id": "inst-rot", "signed_assertion": envelope},
            headers=headers,
        )
        assert resp.status_code == 200, resp.get_json()
        assert resp.get_json()["result"] == "SUCCESS"
    else:
        # A rotation AFTER activation. Activate first on the key the bundled
        # anchor really trusts, so the failure under test is purely "the
        # NEXT check-in is signed by a key this store has never seen."
        first = _envelope(owner_key, "owner-1", _payload("inst-rot", meta.public_key_fingerprint))
        activated = client.post(
            "/api/licensing/_internal/sync-activation",
            json={"result": "SUCCESS", "installation_id": "inst-rot", "signed_assertion": first},
            headers=headers,
        )
        assert activated.status_code == 200, activated.get_json()

        rotated_envelope = _envelope(
            rotated_key,
            "owner-2",
            _payload(
                "inst-rot",
                meta.public_key_fingerprint,
                assertion_id="a-rotated",
                # Strictly newer than the activation assertion, or the
                # monotonicity guard (_is_stale_assertion) would reject it
                # for a reason that has nothing to do with the rotation.
                issued_at=(NOW + timedelta(seconds=2)).isoformat(),
            ),
        )
        resp = client.post(
            "/api/licensing/_internal/sync-checkin",
            json={"signed_assertion": rotated_envelope},
            headers=headers,
        )
        assert resp.status_code == 200, resp.get_json()

    # The assertion signed by the ROTATED key is what actually got stored --
    # not merely "the request returned 200", which it does either way.
    record = LicenseStateRepository(tmp_path / "appdata" / "database" / "subsystems" / "licensing.db").load()
    assert record is not None
    assert record.assertion_id == "a-rotated"
    assert OwnerTrustStore(tmp_path / "appdata" / "licensing" / "trust_store.json").is_trusted("owner-2") is True


@pytest.mark.parametrize("route", ["sync-activation", "sync-checkin"])
def test_neither_internal_sync_route_admits_a_never_trusted_signer(
    route, app_with_secret, owner_key, tmp_path, monkeypatch
):
    """The refresh is not an escape hatch on either route: a manifest whose
    signers this install has never trusted leaves the trust store untouched
    and the assertion is still refused."""
    provider, meta = _generate_device_key(app_with_secret, tmp_path)
    attacker_key = Ed25519PrivateKey.generate()
    monkeypatch.setattr(
        LicensingClient,
        "fetch_signing_keys",
        lambda self: _rotation_manifest(Ed25519PrivateKey.generate(), "unknown-0", attacker_key, "attacker-key"),
    )
    client = app_with_secret.test_client()
    headers = {"X-Aura-Internal-Secret": SHARED_SECRET}
    forged = _envelope(
        attacker_key, "attacker-key", _payload("inst-rot", meta.public_key_fingerprint, assertion_id="a-forged")
    )

    if route == "sync-activation":
        resp = client.post(
            "/api/licensing/_internal/sync-activation",
            json={"result": "SUCCESS", "installation_id": "inst-rot", "signed_assertion": forged},
            headers=headers,
        )
        assert resp.status_code == 400
        assert LicenseStateRepository(tmp_path / "appdata" / "database" / "subsystems" / "licensing.db").load() is None
    else:
        first = _envelope(owner_key, "owner-1", _payload("inst-rot", meta.public_key_fingerprint))
        client.post(
            "/api/licensing/_internal/sync-activation",
            json={"result": "SUCCESS", "installation_id": "inst-rot", "signed_assertion": first},
            headers=headers,
        )
        client.post("/api/licensing/_internal/sync-checkin", json={"signed_assertion": forged}, headers=headers)
        record = LicenseStateRepository(tmp_path / "appdata" / "database" / "subsystems" / "licensing.db").load()
        assert record.assertion_id == "a-1"  # the real one, untouched

    assert OwnerTrustStore(tmp_path / "appdata" / "licensing" / "trust_store.json").is_trusted("attacker-key") is False


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
