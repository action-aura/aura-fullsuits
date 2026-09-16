import base64
import json
from datetime import datetime, timedelta, timezone

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization
from flask import Flask

from commercial_runtime.licensing_contracts import routes as routes_module
from commercial_runtime.licensing_contracts.canonical import canonicalize_bytes
from commercial_runtime.licensing_contracts.device_identity import WindowsDpapiDeviceIdentityProvider
from commercial_runtime.licensing_contracts.routes import make_licensing_blueprint

pytestmark = pytest.mark.skipif(
    __import__("sys").platform != "win32", reason="Uses the real Windows DPAPI device identity provider."
)

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


def _payload(installation_id, device_fingerprint, **overrides):
    base = {
        "assertion_id": "a-1",
        "issuer": "aura-owner",
        "product_code": "AURA_RETAIL",
        "license_public_id": "lic-1",
        "installation_public_id": installation_id,
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


class FakeClient:
    """Stands in for routes.LicensingClient -- monkeypatched at the module
    level so make_licensing_blueprint's real construction path is exercised
    unchanged, only the transport is faked."""

    def __init__(self, *_, **__):
        pass

    activate_response = None
    checkin_response = None

    def activate(self, **kwargs):
        return FakeClient.activate_response

    def check_in(self, **kwargs):
        return FakeClient.checkin_response

    def deactivate(self, **kwargs):
        return FakeClient.deactivate_response

    def fetch_signing_keys(self):
        return {"schema_version": 1, "keys": []}


@pytest.fixture
def owner_key():
    return Ed25519PrivateKey.generate()


@pytest.fixture
def app(tmp_path, owner_key, monkeypatch):
    monkeypatch.setattr(routes_module, "LicensingClient", FakeClient)

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
    )
    flask_app.register_blueprint(bp)
    return flask_app


@pytest.fixture
def client(app):
    return app.test_client()


def _blueprint_without_owner_url(tmp_path, owner_key):
    """The same app, but cut WITHOUT an Owner URL -- a dev build, a
    misconfigured deployment, or any install whose Owner was never wired."""
    import json as _json
    from flask import Flask as _Flask
    trust_anchor_path = tmp_path / "trust_anchor_no_owner.json"
    trust_anchor_path.write_text(
        _json.dumps({"keys": [{"key_id": "owner-1", "public_key": _b64_pub(owner_key),
                               "algorithm": "ed25519"}]}), encoding="utf-8")
    flask_app = _Flask(__name__)
    flask_app.register_blueprint(make_licensing_blueprint(
        product_code="AURA_RETAIL", platform="WINDOWS", app_version="1.0.0-rc.2",
        app_data_dir=str(tmp_path / "appdata"),
        owner_base_url="",                      # <-- the whole point
        verify_tls=True, timeout_seconds=5.0, trust_anchor_path=trust_anchor_path,
        device_identity_factory=lambda d: WindowsDpapiDeviceIdentityProvider(d),
    ))
    return flask_app.test_client()


def test_status_reports_the_stored_licence_even_with_no_owner_url(tmp_path, owner_key):
    """A LICENSED DEVICE MUST NOT BE TOLD IT IS UNLICENSED.

    This route used to short-circuit on `if not owner_base_url` and answer
    NOT_CONFIGURED before opening licensing.db. Measured in a real browser on
    an install whose database said ACTIVE_ONLINE, the desktop shell rendered
    "This device is not licensed to ring new sales" on every screen -- while
    sales rang perfectly, because enforcement reads licensing.db and never
    consults the Owner URL at all.

    The mutation that turns this red is the old behaviour itself: restore the
    early `return _not_configured_response()` and this fails, because the
    route stops seeing a record it is sitting on top of.
    """
    from commercial_runtime.licensing_contracts.state_repository import (
        LicenseStateRecord, LicenseStateRepository)

    db = tmp_path / "appdata" / "database" / "subsystems" / "licensing.db"
    db.parent.mkdir(parents=True, exist_ok=True)
    LicenseStateRepository(db).save(LicenseStateRecord(
        licensing_schema_version=2, product_code="AURA_RETAIL", platform="WINDOWS",
        current_state="ACTIVE_ONLINE",
    ))

    body = _blueprint_without_owner_url(tmp_path, owner_key).get("/api/licensing/status").get_json()

    assert body["current_state"] == "ACTIVE_ONLINE", (
        "a device holding an ACTIVE_ONLINE licence reported "
        f"{body['current_state']!r} because no Owner URL was configured -- the "
        "route answered a question it was not asked")
    # The Owner connection is still absent, and that is still worth reporting --
    # as its OWN fact, not as a verdict on the licence.
    assert body["owner_configured"] is False


def test_status_still_says_not_configured_when_there_is_genuinely_no_licence(tmp_path, owner_key):
    """THE ALLOW-HALF, and it is why the fix is not just 'delete the check'.

    A fresh install with no licence record at all must still report
    NOT_CONFIGURED. Without this, a fix that simply removed the early return
    could have started reporting some other state for an unactivated device
    and nothing would have caught it -- the deny-half above passes either way.
    """
    body = _blueprint_without_owner_url(tmp_path, owner_key).get("/api/licensing/status").get_json()
    assert body["current_state"] == "NOT_CONFIGURED"
    assert body["owner_configured"] is False


def test_status_before_activation_is_not_configured_shape(client):
    resp = client.get("/api/licensing/status")
    assert resp.status_code == 200
    assert resp.get_json()["current_state"] == "NOT_CONFIGURED"


def test_activate_missing_license_key_is_400(client):
    resp = client.post("/api/licensing/activate", json={})
    assert resp.status_code == 400
    assert resp.get_json()["reason_code"] == "INVALID_REQUEST"


def _fingerprint_from_request(device_public_key_b64: str) -> str:
    """Recomputes the device-key fingerprint the same way the route itself
    does, from the device_public_key_b64 actually sent in a given request --
    lets a FakeClient build a real, correctly-bound assertion without
    hardcoding a fingerprint the real device key wouldn't actually have."""
    import base64
    import hashlib

    return hashlib.sha256(base64.b64decode(device_public_key_b64)).hexdigest()


def test_full_activation_flow_through_http(client, owner_key, monkeypatch):
    def _activate(self, **kwargs):
        fingerprint = _fingerprint_from_request(kwargs["device_public_key_b64"])
        envelope = _envelope(owner_key, "owner-1", _payload("owner-assigned-inst-1", fingerprint))
        return {"result": "SUCCESS", "installation_id": "owner-assigned-inst-1", "signed_assertion": envelope}

    monkeypatch.setattr(routes_module.LicensingClient, "activate", _activate)

    resp = client.post("/api/licensing/activate", json={"license_key": "AURA-RETAIL-XXXX-YYYY"})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["result"] == "SUCCESS"
    assert body["installation_id"] == "owner-assigned-inst-1"
    assert body["state"] == "ACTIVE_ONLINE"

    status_resp = client.get("/api/licensing/status")
    assert status_resp.get_json()["installation_id"] == "owner-assigned-inst-1"


def test_activate_rejected_by_owner_returns_400_with_reason_code(client, monkeypatch):
    def _activate(self, **kwargs):
        return {"result": "REJECTED", "reason_code": "ACTIVATION_REJECTED"}

    monkeypatch.setattr(routes_module.LicensingClient, "activate", _activate)
    resp = client.post("/api/licensing/activate", json={"license_key": "AURA-RETAIL-BAD-KEY"})
    assert resp.status_code == 400
    assert resp.get_json()["reason_code"] == "ACTIVATION_REJECTED"


def test_checkin_before_activation_reports_activation_required(client):
    resp = client.post("/api/licensing/check-in")
    assert resp.status_code == 200
    assert resp.get_json()["current_state"] == "ACTIVATION_REQUIRED"


def test_licensing_routes_never_expose_license_key_in_response(client, owner_key, monkeypatch):
    def _activate(self, **kwargs):
        fingerprint = _fingerprint_from_request(kwargs["device_public_key_b64"])
        envelope = _envelope(owner_key, "owner-1", _payload("owner-assigned-inst-1", fingerprint))
        return {"result": "SUCCESS", "installation_id": "owner-assigned-inst-1", "signed_assertion": envelope}

    monkeypatch.setattr(routes_module.LicensingClient, "activate", _activate)
    resp = client.post("/api/licensing/activate", json={"license_key": "AURA-RETAIL-SUPER-SECRET"})
    assert "AURA-RETAIL-SUPER-SECRET" not in resp.get_data(as_text=True)


def test_deactivate_before_activation_is_a_noop_200(client):
    resp = client.post("/api/licensing/deactivate")
    assert resp.status_code == 200
    assert resp.get_json()["state"] == "NOT_CONFIGURED"


def test_full_activate_then_deactivate_flow(client, owner_key, monkeypatch):
    def _activate(self, **kwargs):
        fingerprint = _fingerprint_from_request(kwargs["device_public_key_b64"])
        envelope = _envelope(owner_key, "owner-1", _payload("owner-assigned-inst-1", fingerprint))
        return {"result": "SUCCESS", "installation_id": "owner-assigned-inst-1", "signed_assertion": envelope}

    def _deactivate(self, **kwargs):
        return {"result": "SUCCESS", "reason_code": "DEACTIVATION_ACCEPTED"}

    monkeypatch.setattr(routes_module.LicensingClient, "activate", _activate)
    monkeypatch.setattr(routes_module.LicensingClient, "deactivate", _deactivate)

    client.post("/api/licensing/activate", json={"license_key": "AURA-RETAIL-XXXX-YYYY"})
    resp = client.post("/api/licensing/deactivate")
    assert resp.status_code == 200
    assert resp.get_json()["state"] == "DEVICE_DEACTIVATED"

    status_resp = client.get("/api/licensing/status")
    assert status_resp.get_json()["current_state"] == "DEVICE_DEACTIVATED"


def test_checkin_response_flags_when_owner_was_unreachable(client, owner_key, monkeypatch):
    def _activate(self, **kwargs):
        fingerprint = _fingerprint_from_request(kwargs["device_public_key_b64"])
        envelope = _envelope(owner_key, "owner-1", _payload("owner-assigned-inst-1", fingerprint))
        return {"result": "SUCCESS", "installation_id": "owner-assigned-inst-1", "signed_assertion": envelope}

    monkeypatch.setattr(routes_module.LicensingClient, "activate", _activate)
    client.post("/api/licensing/activate", json={"license_key": "AURA-RETAIL-XXXX-YYYY"})

    def _check_in_fails(self, **kwargs):
        from commercial_runtime.licensing_contracts.client import NetworkError
        raise NetworkError("NETWORK_UNAVAILABLE", "simulated outage")

    monkeypatch.setattr(routes_module.LicensingClient, "check_in", _check_in_fails)
    resp = client.post("/api/licensing/check-in")
    assert resp.status_code == 200  # a network blip is not itself an HTTP error (Part AD)
    assert resp.get_json()["last_attempt_reached_owner"] is False


def test_checkin_response_flags_when_owner_reached_successfully(client, owner_key, monkeypatch):
    def _activate(self, **kwargs):
        fingerprint = _fingerprint_from_request(kwargs["device_public_key_b64"])
        envelope = _envelope(owner_key, "owner-1", _payload("owner-assigned-inst-1", fingerprint))
        return {"result": "SUCCESS", "installation_id": "owner-assigned-inst-1", "signed_assertion": envelope}

    monkeypatch.setattr(routes_module.LicensingClient, "activate", _activate)
    client.post("/api/licensing/activate", json={"license_key": "AURA-RETAIL-XXXX-YYYY"})

    # Real check-in requires a verifiable assertion in the response to count
    # as CHECK_IN_SUCCEEDED (see checkin_scheduler.ingest_checkin_response) --
    # build one properly, bound to this test's actual device key.
    def _check_in_with_assertion(self, *, installation_id, signer):
        fingerprint = __import__("hashlib").sha256(
            __import__("base64").b64decode(signer.get_public_key_b64())
        ).hexdigest()
        envelope = _envelope(owner_key, "owner-1", _payload("owner-assigned-inst-1", fingerprint))
        return {"result": "SUCCESS", "signed_assertion": envelope}

    monkeypatch.setattr(routes_module.LicensingClient, "check_in", _check_in_with_assertion)
    resp = client.post("/api/licensing/check-in")
    assert resp.status_code == 200
    assert resp.get_json()["last_attempt_reached_owner"] is True
