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


# -- AUDIT P0-1: content-type defense-in-depth (independent of auth_required,
# always active on this shared factory -- see make_licensing_blueprint's
# _reject_non_json_body() docstring). This `app`/`client` fixture pair
# builds the blueprint with no auth_required, so these tests isolate the
# content-type check on its own, decoupled from Retail's session wiring
# (covered separately in products/retail/tests).


def test_form_encoded_post_to_deactivate_is_rejected_415(client):
    # A plain HTML <form> can never set Content-Type: application/json --
    # this is exactly the shape a cross-site drive-by POST would take.
    resp = client.post("/api/licensing/deactivate", data={"foo": "bar"})
    assert resp.status_code == 415
    assert resp.get_json()["reason_code"] == "INVALID_REQUEST"


def test_form_encoded_post_to_checkin_is_rejected_415(client):
    resp = client.post("/api/licensing/check-in", data={"foo": "bar"})
    assert resp.status_code == 415


def test_form_encoded_post_to_activate_is_rejected_415(client):
    resp = client.post("/api/licensing/activate", data={"license_key": "AURA-RETAIL-XXXX"})
    assert resp.status_code == 415


def test_bare_post_with_no_body_is_not_rejected_by_content_type_check(client):
    # No Content-Type at all (this package's own pre-existing no-body
    # check-in/deactivate calls) must still work exactly as before.
    resp = client.post("/api/licensing/check-in")
    assert resp.status_code == 200


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


def test_checkin_route_handles_monotonic_rollback_without_crashing(client, owner_key, monkeypatch):
    """AUDIT P0-3: this route (check_in() above) has no try/except of its
    own around scheduler.run_once() -- if trusted_time.trusted_now() raises
    TrustedTimeError (the monotonic clock reading backward since the
    anchor was pinned), that must be handled inside the scheduler itself
    (checkin_scheduler._reevaluate()) or this HTTP route 500s. Proves the
    fix holds at the real HTTP boundary, not just at the
    LicenseCheckInScheduler unit level (see test_checkin_scheduler.py's
    test_monotonic_rollback_during_run_once_yields_clock_review_required_
    not_a_crash for that)."""
    from commercial_runtime.licensing_contracts import trusted_time
    import time as time_module

    # A distinct installation_id avoids colliding with the process-lifetime
    # anchor cache other tests in this file populate under
    # "owner-assigned-inst-1"; clearing it too, for good measure.
    trusted_time._ANCHOR_CACHE.clear()
    installation_id = "owner-assigned-inst-clock-review"

    def _activate(self, **kwargs):
        fingerprint = _fingerprint_from_request(kwargs["device_public_key_b64"])
        envelope = _envelope(owner_key, "owner-1", _payload(installation_id, fingerprint))
        return {"result": "SUCCESS", "installation_id": installation_id, "signed_assertion": envelope}

    monkeypatch.setattr(routes_module.LicensingClient, "activate", _activate)
    activate_resp = client.post("/api/licensing/activate", json={"license_key": "AURA-RETAIL-XXXX-YYYY"})
    assert activate_resp.status_code == 200

    def _check_in_fails(self, **kwargs):
        from commercial_runtime.licensing_contracts.client import NetworkError
        raise NetworkError("NETWORK_UNAVAILABLE", "simulated outage")

    monkeypatch.setattr(routes_module.LicensingClient, "check_in", _check_in_fails)

    fake_monotonic = [5000.0]
    monkeypatch.setattr(time_module, "monotonic", lambda: fake_monotonic[0])

    # First failed check-in pins the anchor at monotonic=5000.0.
    first_resp = client.post("/api/licensing/check-in")
    assert first_resp.status_code == 200

    # The monotonic clock reads backward on the next attempt -- must not 500.
    fake_monotonic[0] = 4000.0
    resp = client.post("/api/licensing/check-in")

    assert resp.status_code == 200
    assert resp.get_json()["current_state"] == "CLOCK_REVIEW_REQUIRED"

    trusted_time._ANCHOR_CACHE.clear()
