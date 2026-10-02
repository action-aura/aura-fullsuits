"""AUDIT-033 fix round (verifier V finding #1): the client half of Owner's
registry-device-id pin.

The client must (a) send its registry device id to Owner INSIDE the device
signature, only when it has one; (b) notice when Owner's lease does not attest
it, because that silently stops this computer signing shop-authority changes;
(c) let Android's Kotlin layer fetch the id (it owns the Owner call, Python
owns the id); (d) report attestation to the shell.
"""
import base64
import json

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from flask import Flask

from commercial_runtime.licensing_contracts.activation import ingest_activation_response, perform_activation
from commercial_runtime.licensing_contracts.canonical import canonicalize_bytes
from commercial_runtime.licensing_contracts.client import LicensingClient, LicensingClientConfig
from commercial_runtime.licensing_contracts.events import LicensingEventRecorder
from commercial_runtime.licensing_contracts.routes import make_licensing_blueprint
from commercial_runtime.licensing_contracts.state_repository import (
    LICENSING_SCHEMA_VERSION, LicenseStateRecord, LicenseStateRepository,
)
from commercial_runtime.licensing_contracts.status_presenter import present_status
from commercial_runtime.licensing_contracts.tests.test_activation import (
    DEVICE_FINGERPRINT as ACT_FP, _envelope as act_envelope, _payload as act_payload,
)
from commercial_runtime.licensing_contracts.tests.test_checkin_scheduler import (
    FakeSigner, _b64_pub, _envelope, _payload, _scheduler, _seed_activated_record,
)
from commercial_runtime.licensing_contracts.trust_store import OwnerTrustStore

DEV = "11111111-1111-4111-8111-111111111111"
OTHER = "22222222-2222-4222-8222-222222222222"
SECRET = "registry-id-test-secret"


@pytest.fixture(autouse=True)
def _clear_anchor_cache():
    from commercial_runtime.licensing_contracts import trusted_time

    trusted_time._ANCHOR_CACHE.clear()
    yield
    trusted_time._ANCHOR_CACHE.clear()


@pytest.fixture
def owner_key():
    return Ed25519PrivateKey.generate()


@pytest.fixture
def trust_store(tmp_path, owner_key):
    s = OwnerTrustStore(tmp_path / "trust.json")
    s.bootstrap_from_anchor({"keys": [{"key_id": "owner-1", "public_key": _b64_pub(owner_key), "algorithm": "ed25519"}]})
    return s


@pytest.fixture
def state_repo(tmp_path):
    return LicenseStateRepository(tmp_path / "database" / "subsystems" / "licensing.db")


@pytest.fixture
def events(tmp_path):
    return LicensingEventRecorder(tmp_path / "database" / "subsystems" / "licensing.db")


# ── (a) request bodies ────────────────────────────────────────────────────

class _Resp:
    status_code = 200
    headers: dict = {}

    def json(self):
        return {"result": "SUCCESS"}


class _CapturingSession:
    def __init__(self):
        self.bodies = []

    def request(self, method, url, json=None, **_kw):  # noqa: A002 -- mirrors requests' keyword
        self.bodies.append(json)
        return _Resp()


class _RealSigner:
    """A real Ed25519 signer, so a test can verify the signature really covers the field."""

    def __init__(self):
        self._key = Ed25519PrivateKey.generate()
        self.signed = []

    def sign(self, canonical_bytes: bytes) -> str:
        self.signed.append(canonical_bytes)
        return base64.b64encode(self._key.sign(canonical_bytes)).decode("ascii")

    def get_public_key_b64(self) -> str:
        return _b64_pub(self._key)

    def verify(self, body: dict) -> bool:
        from cryptography.exceptions import InvalidSignature

        signable = {k: v for k, v in body.items() if k != "signature"}
        try:
            self._key.public_key().verify(base64.b64decode(body["signature"]), canonicalize_bytes(signable))
            return True
        except InvalidSignature:
            return False


def _client():
    session = _CapturingSession()
    return LicensingClient(LicensingClientConfig(base_url="https://owner.example/api/licensing/v1"), session=session), session


def _activate(client, signer, **kw):
    return client.activate(
        product_code="AURA_RETAIL", platform="WINDOWS", app_version="1", release_channel="rc",
        installation_id="i", device_public_key_b64=signer.get_public_key_b64(), license_key="K",
        idempotency_key="idem", signer=signer, **kw,
    )


@pytest.mark.parametrize("call", ["activate", "check_in"])
def test_the_id_is_in_the_body_and_covered_by_the_device_signature(call):
    client, session = _client()
    signer = _RealSigner()
    if call == "activate":
        _activate(client, signer, registry_device_id=DEV)
    else:
        client.check_in(installation_id="inst", signer=signer, registry_device_id=DEV)
    body = session.bodies[0]
    assert body["registry_device_id"] == DEV
    assert signer.verify(body), "the signature must verify over a body that includes the id"
    # The bytes handed to sign() contained it (so it was added BEFORE signing, not after).
    assert json.loads(signer.signed[0])["registry_device_id"] == DEV
    # ...and swapping it afterwards breaks the signature: it really is covered.
    assert signer.verify({**body, "registry_device_id": OTHER}) is False


@pytest.mark.parametrize("call", ["activate", "check_in"])
@pytest.mark.parametrize("absent", [None, ""])
def test_the_key_is_absent_when_there_is_no_id(call, absent):
    client, session = _client()
    signer = _RealSigner()
    kwargs = {} if absent is None and call == "check_in" else {"registry_device_id": absent}
    if call == "activate":
        _activate(client, signer, **kwargs)
    else:
        client.check_in(installation_id="inst", signer=signer, **kwargs)
    assert "registry_device_id" not in session.bodies[0], "an old Owner must see exactly the body it always saw"
    assert "registry_device_id" not in json.loads(signer.signed[0])


# ── (a) the scheduler and activation pass the provider's value through ────

class _RecordingClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.kwargs = []

    def check_in(self, *, installation_id, signer, registry_device_id=None):
        self.kwargs.append({"installation_id": installation_id, "registry_device_id": registry_device_id})
        return self.responses.pop(0)

    def fetch_signing_keys(self):
        return {"keys": []}


def test_the_scheduler_sends_the_providers_value(owner_key, trust_store, state_repo, events):
    _seed_activated_record(state_repo, assertion_envelope_json=None)
    env = _envelope(owner_key, "owner-1", _payload(registry_device_id=DEV))
    client = _RecordingClient([{"result": "SUCCESS", "signed_assertion": env}])
    _scheduler(client, trust_store, state_repo, events, registry_device_id_provider=lambda: DEV).run_once()
    assert client.kwargs == [{"installation_id": "inst-1", "registry_device_id": DEV}]


def test_the_scheduler_sends_none_without_a_provider_or_when_it_breaks(owner_key, trust_store, state_repo, events):
    def boom():
        raise RuntimeError("corrupt local_device.json")

    for provider in (None, boom, lambda: None):
        _seed_activated_record(state_repo, assertion_envelope_json=None)
        env = _envelope(owner_key, "owner-1", _payload())
        client = _RecordingClient([{"result": "SUCCESS", "signed_assertion": env}])
        _scheduler(client, trust_store, state_repo, events, registry_device_id_provider=provider).run_once()
        assert client.kwargs[0]["registry_device_id"] is None, "a broken provider must never stop the check-in"
        assert "CHECK_IN_SUCCEEDED" in [e.event_type for e in events.recent()]


def test_activation_sends_the_providers_value(owner_key, trust_store, state_repo, events):
    seen = {}

    class _Client:
        def activate(self, **kw):
            seen.update(kw)
            return {
                "result": "SUCCESS", "installation_id": "inst-x",
                "signed_assertion": act_envelope(owner_key, "owner-1", act_payload("inst-x", registry_device_id=DEV)),
            }

        def fetch_signing_keys(self):
            return {"keys": []}

    perform_activation(
        client=_Client(), signer=FakeSigner(), trust_store=trust_store, state_repository=state_repo,
        event_recorder=events, product_code="AURA_RETAIL", platform="WINDOWS", app_version="1",
        release_channel="rc", license_key="K", device_public_key_fingerprint=ACT_FP,
        registry_device_id_provider=lambda: DEV,
    )
    assert seen["registry_device_id"] == DEV
    assert "REGISTRY_DEVICE_ID_NOT_ATTESTED" not in [e.event_type for e in events.recent()]


# ── (b) the not-attested event ────────────────────────────────────────────

def _ingest_checkin(owner_key, trust_store, state_repo, events, payload, provider):
    _seed_activated_record(state_repo, assertion_envelope_json=None)
    env = _envelope(owner_key, "owner-1", payload)
    sched = _scheduler(_RecordingClient([]), trust_store, state_repo, events, registry_device_id_provider=provider)
    sched.ingest_checkin_response({"result": "SUCCESS", "signed_assertion": env})


def _not_attested(events):
    return [e for e in events.recent() if e.event_type == "REGISTRY_DEVICE_ID_NOT_ATTESTED"]


@pytest.mark.parametrize("attested", [None, OTHER], ids=["missing", "other-id"])
def test_a_lease_that_does_not_attest_this_computer_is_recorded(owner_key, trust_store, state_repo, events, attested, caplog):
    payload = _payload() if attested is None else _payload(registry_device_id=attested)
    with caplog.at_level("WARNING"):
        _ingest_checkin(owner_key, trust_store, state_repo, events, payload, lambda: DEV)
    rows = _not_attested(events)
    assert len(rows) == 1 and dict(rows[0].details) == {"local": DEV, "attested": attested}
    assert "cannot sign shop-authority changes" in caplog.text


def test_an_attesting_lease_records_nothing(owner_key, trust_store, state_repo, events):
    _ingest_checkin(owner_key, trust_store, state_repo, events, _payload(registry_device_id=DEV), lambda: DEV)
    assert _not_attested(events) == []


def test_no_provider_or_no_local_id_records_nothing(owner_key, trust_store, state_repo, events):
    _ingest_checkin(owner_key, trust_store, state_repo, events, _payload(), None)
    _ingest_checkin(owner_key, trust_store, state_repo, events, _payload(), lambda: None)
    assert _not_attested(events) == []


def test_the_same_gap_is_not_logged_on_every_check_in(owner_key, trust_store, state_repo, events):
    for _ in range(3):
        _ingest_checkin(owner_key, trust_store, state_repo, events, _payload(), lambda: DEV)
    assert len(_not_attested(events)) == 1
    # ...but a CHANGE in the gap is news.
    _ingest_checkin(owner_key, trust_store, state_repo, events, _payload(registry_device_id=OTHER), lambda: DEV)
    assert len(_not_attested(events)) == 2


def test_the_activation_path_records_it_too(owner_key, trust_store, state_repo, events):
    env = act_envelope(owner_key, "owner-1", act_payload("inst-y"))  # Owner did not attest (old Owner / clash)
    ingest_activation_response(
        {"result": "SUCCESS", "installation_id": "inst-y", "signed_assertion": env},
        trust_store=trust_store, state_repository=state_repo, event_recorder=events,
        product_code="AURA_RETAIL", platform="WINDOWS", device_public_key_fingerprint=ACT_FP,
        registry_device_id_provider=lambda: DEV,
    )
    assert [dict(e.details) for e in _not_attested(events)] == [{"local": DEV, "attested": None}]


def test_a_stale_assertion_does_not_report_attestation(owner_key, trust_store, state_repo, events):
    """An out-of-order older lease is thrown away; it must not generate a verdict."""
    from datetime import timedelta

    from commercial_runtime.licensing_contracts.tests.test_checkin_scheduler import NOW

    _seed_activated_record(state_repo, assertion_envelope_json=None, assertion_issued_at=NOW.isoformat())
    old = _envelope(owner_key, "owner-1", _payload(issued_at=(NOW - timedelta(hours=1)).isoformat()))
    sched = _scheduler(_RecordingClient([]), trust_store, state_repo, events, registry_device_id_provider=lambda: DEV)
    sched.ingest_checkin_response({"result": "SUCCESS", "signed_assertion": old})
    assert _not_attested(events) == []


# ── (c) the internal route for Android ───────────────────────────────────

def _app(tmp_path, provider):
    app = Flask(__name__)
    app.register_blueprint(make_licensing_blueprint(
        product_code="AURA_RETAIL", platform="ANDROID", app_version="1", app_data_dir=str(tmp_path / "appdata"),
        owner_base_url="https://owner.example/api/licensing/v1", verify_tls=True, timeout_seconds=5.0,
        trust_anchor_path=tmp_path / "anchor.json", device_identity_factory=lambda d: None,
        internal_shared_secret=SECRET, registry_device_id_provider=provider,
    ))
    return app.test_client()


def test_internal_route_refuses_without_the_secret(tmp_path):
    c = _app(tmp_path, lambda: DEV)
    assert c.get("/api/licensing/_internal/registry-device-id").status_code == 403
    assert c.get("/api/licensing/_internal/registry-device-id", headers={"X-Aura-Internal-Secret": "nope"}).status_code == 403


def test_internal_route_returns_the_id_or_null(tmp_path):
    hdr = {"X-Aura-Internal-Secret": SECRET}
    r = _app(tmp_path, lambda: DEV).get("/api/licensing/_internal/registry-device-id", headers=hdr)
    assert r.status_code == 200 and r.get_json() == {"registry_device_id": DEV}
    for provider in (None, lambda: None):
        r = _app(tmp_path, provider).get("/api/licensing/_internal/registry-device-id", headers=hdr)
        assert r.status_code == 200 and r.get_json() == {"registry_device_id": None}


def test_internal_route_survives_a_broken_provider(tmp_path):
    def boom():
        raise RuntimeError("corrupt")

    r = _app(tmp_path, boom).get("/api/licensing/_internal/registry-device-id", headers={"X-Aura-Internal-Secret": SECRET})
    assert r.status_code == 200 and r.get_json() == {"registry_device_id": None}


# ── (d) the status flag ──────────────────────────────────────────────────

def _record(payload):
    return LicenseStateRecord(
        licensing_schema_version=LICENSING_SCHEMA_VERSION, product_code="AURA_RETAIL", platform="WINDOWS",
        current_state="ACTIVE_ONLINE", owner_installation_id="inst-1",
        assertion_envelope_json=json.dumps({"payload": payload}),
    )


def test_status_flag_is_true_only_for_a_lease_that_names_an_id():
    assert present_status(_record({"registry_device_id": DEV}))["registry_device_attested"] is True
    for payload in ({}, {"registry_device_id": ""}, {"registry_device_id": None}, {"registry_device_id": 7}):
        assert present_status(_record(payload))["registry_device_attested"] is False
    assert "registry_device_attested" not in present_status(None)
    # a yes/no only: the id itself is never exposed here
    assert DEV not in json.dumps(present_status(_record({"registry_device_id": DEV})))
