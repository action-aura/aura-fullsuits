"""Tests for the desktop half of automatic LAN site-relay discovery-and-join
(`site_relay/autojoin.py`), retail schema v30
(`docs/launch-readiness/lan-restaurant-design.md`).

Mirrors `test_site_relay_join.py`'s / `test_site_relay_roster.py`'s own
stated conventions exactly (this task's own instruction was to reuse those
files' idiom for minting signed envelopes and building a trust store, rather
than inventing a second way):

  * Real Ed25519 keypairs throughout (`cryptography`), real canonical-body
    signing (`canonicalize_bytes` -- the identical routine the licence
    assertion is signed with), and a real `OwnerTrustStore` built the same
    way `licensing_contracts/tests/test_assertion_verifier.py` and
    `test_site_relay_join.py` both build one.
  * No sockets and no real licensing state: `beacon_listener` and
    `session_factory` are hand-rolled fakes (`FakeSession`,
    `FakeStateRepository`) that record every call they receive, exactly what
    `discover_and_join`'s "every collaborator injectable" design is for.
  * `join.py`'s own `verify_membership` is the SERVER-side mirror of the
    check this module's tests exercise on the CLIENT side -- the "different
    licence" and "assertion presented with a different device's key" attacks
    are the identical shape, just from the joining device's point of view.
"""
import base64
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from commercial_runtime.licensing_contracts.canonical import canonicalize_bytes
from commercial_runtime.licensing_contracts.device_identity import fingerprint_of
from commercial_runtime.licensing_contracts.state_repository import LicenseStateRecord
from commercial_runtime.licensing_contracts.trust_store import OwnerTrustStore
from commercial_runtime.sync.site_relay import autojoin
from commercial_runtime.sync.site_relay.autojoin import AutoJoinResult, discover_and_join

# This shop's shared licence -- held constant across every test that wants
# "same shop". A device (or a hub) whose assertion names anything else is a
# different shop, full stop.
SHOP_LICENSE_PUBLIC_ID = "lic-shop-1"
OTHER_SHOP_LICENSE_PUBLIC_ID = "lic-some-other-shop"

HUB_URL = "https://192.168.1.50:8443"
HUB_SPKI_PIN = "hub-spki-pin-value"


# ── helpers (mirrors test_site_relay_join.py's own helper shapes) ─────────

def _b64_pub(private_key) -> str:
    raw = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw
    )
    return base64.b64encode(raw).decode("ascii")


def _standard_policy() -> dict:
    """Copied from `test_site_relay_join.py`'s own `_standard_policy` (itself
    copied from `licensing_contracts/tests/test_assertion_verifier.py`) --
    the minimal SAFE `offline_policy` shape `verify_assertion` requires every
    payload to carry."""
    return {
        "check_in_interval_seconds": 86400,
        "retry_interval_seconds": 3600,
        "offline_grace_seconds": 1209600,
        "warning_start_seconds": 864000,
        "hard_expiry_behavior": "RESTRICT_COMMERCIAL_FEATURES_PRESERVE_DATA",
        "clock_rollback_tolerance_seconds": 300,
        "assertion_refresh_threshold_seconds": 86400,
    }


def _assertion_payload(*, installation_id: str, device_fingerprint: str, **overrides) -> dict:
    """Mirrors `test_site_relay_join.py`'s own `_assertion_payload` helper --
    every field `assertion_verifier.ALLOWED_PAYLOAD_FIELDS` allows, populated
    with values that verify cleanly by default."""
    now = datetime.now(timezone.utc)
    base = {
        "assertion_id": "a-" + secrets.token_hex(4),
        "issuer": "aura-owner",
        "product_code": "AURA_RETAIL",
        "license_public_id": SHOP_LICENSE_PUBLIC_ID,
        "installation_public_id": installation_id,
        "platform": "WINDOWS",
        "app_version_policy": "1.0.0",
        "release_channel": "stable",
        "issued_at": now.isoformat(),
        "not_before": (now - timedelta(minutes=5)).isoformat(),
        "expires_at": (now + timedelta(days=1)).isoformat(),
        "license_status": "ACTIVE",
        "installation_status": "ACTIVE",
        "subscription_status": "ACTIVE",
        "allowed_device_count": 5,
        "device_key_fingerprint": device_fingerprint,
        "entitlements": {},
        "offline_policy": _standard_policy(),
        "contract_version": "v1",
    }
    base.update(overrides)
    return base


def _sign_assertion(signing_key, key_id: str, payload: dict) -> dict:
    signature = signing_key.sign(canonicalize_bytes(payload))
    return {
        "payload": payload,
        "signing_key_id": key_id,
        "algorithm": "ed25519",
        "signature": base64.b64encode(signature).decode("ascii"),
    }


class Device:
    """A fake device holding a REAL Ed25519 keypair -- duplicated from
    `test_site_relay_join.py`'s own `Device` rather than imported, matching
    this test package's stated convention (no `conftest.py`; every file here
    is self-contained)."""

    def __init__(self, installation_id=None):
        self.installation_id = installation_id or str(uuid.uuid4())
        self.private_key = Ed25519PrivateKey.generate()

    @property
    def _raw_public_key(self) -> bytes:
        return self.private_key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw
        )

    @property
    def public_key_b64(self) -> str:
        return base64.b64encode(self._raw_public_key).decode("ascii")

    @property
    def fingerprint(self) -> str:
        return fingerprint_of(self._raw_public_key)

    def build_assertion(self, signing_key, *, key_id: str = "owner-1", **overrides) -> dict:
        payload = _assertion_payload(
            installation_id=self.installation_id, device_fingerprint=self.fingerprint, **overrides
        )
        return _sign_assertion(signing_key, key_id, payload)


class FakeSigner:
    """The minimal `signer` protocol `discover_and_join` actually uses --
    just `get_public_key_b64()` (see autojoin.py's module docstring for why
    that is the only method this module needs)."""

    def __init__(self, device: Device):
        self._device = device

    def get_public_key_b64(self) -> str:
        return self._device.public_key_b64


class FakeStateRepository:
    """Stands in for `LicenseStateRepository` -- no sqlite, no file I/O.
    Records every `save()` call so a test can assert exactly when (or
    whether) persistence happened -- the load-bearing property for the
    mutation-proof around test 8."""

    def __init__(self, record):
        self._record = record
        self.save_calls: list = []

    def load(self):
        return self._record

    def save(self, record) -> None:
        self.save_calls.append(record)
        self._record = record


class FakeResponse:
    def __init__(self, status_code: int, json_body=None):
        self.status_code = status_code
        self._json_body = json_body

    def json(self):
        return self._json_body


class FakeSession:
    """Stands in for the `requests.Session`-shaped object `session_factory`
    returns. Records every `get`/`post` call (URL + kwargs) so tests can
    assert not just the RETURN VALUE of `discover_and_join` but WHETHER a
    given HTTP call was even attempted -- the property
    test_different_license_hub_is_never_joined and
    test_join_rejected_by_hub_is_not_persisted both depend on."""

    def __init__(self, *, get_response=None, post_response=None):
        self.get_calls: list = []
        self.post_calls: list = []
        self._get_response = get_response
        self._post_response = post_response

    def get(self, url, **kwargs):
        self.get_calls.append((url, kwargs))
        return self._get_response

    def post(self, url, **kwargs):
        self.post_calls.append((url, kwargs))
        return self._post_response


def _session_factory(session: FakeSession, calls: list):
    def factory(pin: str):
        calls.append(pin)
        return session
    return factory


def _refusing_session_factory(calls: list):
    """A `session_factory` that FAILS the test loudly if it is ever called --
    used by every test that asserts no HTTP call was attempted at all."""
    def factory(pin: str):
        calls.append(pin)
        raise AssertionError("session_factory must not be called")
    return factory


def _refusing_beacon_listener(calls: list):
    def listener(timeout_seconds: float):
        calls.append(timeout_seconds)
        raise AssertionError("beacon_listener must not be called")
    return listener


def _identity_body(hub_device: Device, assertion_envelope: dict) -> dict:
    return {
        "installation_id": hub_device.installation_id,
        "device_public_key": hub_device.public_key_b64,
        "assertion": assertion_envelope,
    }


def _own_state(own_device: Device, own_assertion_envelope: dict, *, sync_relay_base_url=None) -> LicenseStateRecord:
    return LicenseStateRecord(
        licensing_schema_version=2,
        product_code="AURA_RETAIL",
        platform="WINDOWS",
        current_state="ACTIVE_ONLINE",
        owner_installation_id=own_device.installation_id,
        assertion_envelope_json=__import__("json").dumps(own_assertion_envelope),
        sync_relay_base_url=sync_relay_base_url,
    )


def _trust_store(tmp_path, owner_key, *, key_id: str = "owner-1") -> OwnerTrustStore:
    store = OwnerTrustStore(tmp_path / "trust.json")
    store.bootstrap_from_anchor(
        {"keys": [{"key_id": key_id, "public_key": _b64_pub(owner_key), "algorithm": "ed25519"}]}
    )
    return store


def _pointer(hub_device: Device, *, url: str = HUB_URL, spki_pin: str = HUB_SPKI_PIN) -> dict:
    return {"installation_id": hub_device.installation_id, "url": url, "spki_pin": spki_pin}


# ── 1. no beacon heard ──────────────────────────────────────────────────

def test_no_beacon_heard_returns_no_hub_found_and_makes_no_http_call(tmp_path):
    owner_key = Ed25519PrivateKey.generate()
    own_device = Device()
    own_assertion = own_device.build_assertion(owner_key)
    repo = FakeStateRepository(_own_state(own_device, own_assertion))

    session_factory_calls: list = []
    beacon_calls: list = []

    def beacon_listener(timeout_seconds):
        beacon_calls.append(timeout_seconds)
        return None

    result = discover_and_join(
        state_repository=repo,
        signer=FakeSigner(own_device),
        trust_store=_trust_store(tmp_path, owner_key),
        trusted_now=datetime.now(timezone.utc),
        session_factory=_refusing_session_factory(session_factory_calls),
        beacon_listener=beacon_listener,
    )

    assert result == AutoJoinResult.NO_HUB_FOUND
    assert beacon_calls == [10.0]
    assert session_factory_calls == []  # no HTTP call was attempted at all
    assert repo.save_calls == []


# ── 2. happy path ───────────────────────────────────────────────────────

def test_happy_path_joins_and_posts_this_devices_own_identity(tmp_path):
    owner_key = Ed25519PrivateKey.generate()
    own_device = Device()
    own_assertion = own_device.build_assertion(owner_key)
    repo = FakeStateRepository(_own_state(own_device, own_assertion))

    hub_device = Device()
    hub_assertion = hub_device.build_assertion(owner_key)  # same licence, trusted key

    session = FakeSession(
        get_response=FakeResponse(200, _identity_body(hub_device, hub_assertion)),
        post_response=FakeResponse(200, {"joined": True, "installation_id": own_device.installation_id}),
    )
    session_factory_calls: list = []

    result = discover_and_join(
        state_repository=repo,
        signer=FakeSigner(own_device),
        trust_store=_trust_store(tmp_path, owner_key),
        trusted_now=datetime.now(timezone.utc),
        session_factory=_session_factory(session, session_factory_calls),
        beacon_listener=lambda timeout_seconds: _pointer(hub_device),
    )

    assert result == AutoJoinResult.JOINED
    assert session_factory_calls == [HUB_SPKI_PIN]  # dialed using the beacon-advertised pin

    assert len(session.get_calls) == 1
    assert session.get_calls[0][0] == HUB_URL + "/api/sync/v1/identity"

    # THE LOAD-BEARING ASSERTION: the POST carried THIS device's OWN
    # installation id, key, and assertion -- never the hub's.
    assert len(session.post_calls) == 1
    post_url, post_kwargs = session.post_calls[0]
    assert post_url == HUB_URL + "/api/sync/v1/join"
    assert post_kwargs["json"] == {
        "installation_id": own_device.installation_id,
        "device_public_key": own_device.public_key_b64,
        "assertion": own_assertion,
    }

    assert len(repo.save_calls) == 1
    assert repo.save_calls[0].sync_relay_base_url == HUB_URL


# ── 3. different licence -- THE most important test in this file ──────────

def test_hub_with_a_different_license_is_refused_and_join_is_never_called(tmp_path):
    """A hub presenting a perfectly genuine, correctly signed, in-date
    assertion -- just for a DIFFERENT licence -- must never be joined. This
    device must not offer itself to another shop's hub.

    MUTATION-PROOF (verbatim, see task report): commenting out the
    `verified.payload.get("license_public_id") != own_license_public_id`
    check in `autojoin.py` turns this test RED -- `session.post_calls`
    becomes non-empty and the result becomes `JOINED`.
    """
    owner_key = Ed25519PrivateKey.generate()
    own_device = Device()
    own_assertion = own_device.build_assertion(owner_key)
    repo = FakeStateRepository(_own_state(own_device, own_assertion))

    hub_device = Device()
    hub_assertion = hub_device.build_assertion(
        owner_key, license_public_id=OTHER_SHOP_LICENSE_PUBLIC_ID
    )

    session = FakeSession(get_response=FakeResponse(200, _identity_body(hub_device, hub_assertion)))
    session_factory_calls: list = []

    result = discover_and_join(
        state_repository=repo,
        signer=FakeSigner(own_device),
        trust_store=_trust_store(tmp_path, owner_key),
        trusted_now=datetime.now(timezone.utc),
        session_factory=_session_factory(session, session_factory_calls),
        beacon_listener=lambda timeout_seconds: _pointer(hub_device),
    )

    assert result == AutoJoinResult.DIFFERENT_SHOP
    assert session.post_calls == []  # /join is NEVER called
    assert repo.save_calls == []


# ── 4. assertion signed by an untrusted key ────────────────────────────────

def test_hub_assertion_signed_by_untrusted_key_is_refused(tmp_path):
    """MUTATION-PROOF (verbatim, see task report): reading
    `verified.payload["license_public_id"]`-equivalent data straight off
    `hub_assertion["payload"]` WITHOUT calling `verify_assertion` first turns
    this test RED. The hub's assertion here names THIS SHOP'S OWN licence
    (`SHOP_LICENSE_PUBLIC_ID`, the default) -- if the signature were never
    checked, the (unverified) shop-boundary comparison would pass and
    `session.post_calls` would become non-empty with a `JOINED` result,
    instead of the required `FAILED` with no join at all.
    """
    owner_key = Ed25519PrivateKey.generate()
    stranger_key = Ed25519PrivateKey.generate()  # never admitted to trust_store
    own_device = Device()
    own_assertion = own_device.build_assertion(owner_key)
    repo = FakeStateRepository(_own_state(own_device, own_assertion))

    hub_device = Device()
    hub_assertion = hub_device.build_assertion(stranger_key, key_id="stranger-1")  # same licence, untrusted signer

    session = FakeSession(get_response=FakeResponse(200, _identity_body(hub_device, hub_assertion)))
    session_factory_calls: list = []

    result = discover_and_join(
        state_repository=repo,
        signer=FakeSigner(own_device),
        trust_store=_trust_store(tmp_path, owner_key),
        trusted_now=datetime.now(timezone.utc),
        session_factory=_session_factory(session, session_factory_calls),
        beacon_listener=lambda timeout_seconds: _pointer(hub_device),
    )

    assert result == AutoJoinResult.FAILED
    assert session.post_calls == []
    assert repo.save_calls == []


# ── 5. expired assertion ────────────────────────────────────────────────

def test_expired_hub_assertion_is_refused(tmp_path):
    owner_key = Ed25519PrivateKey.generate()
    own_device = Device()
    own_assertion = own_device.build_assertion(owner_key)
    repo = FakeStateRepository(_own_state(own_device, own_assertion))

    hub_device = Device()
    now = datetime.now(timezone.utc)
    hub_assertion = hub_device.build_assertion(
        owner_key,
        not_before=(now - timedelta(days=2)).isoformat(),
        expires_at=(now - timedelta(days=1)).isoformat(),
    )

    session = FakeSession(get_response=FakeResponse(200, _identity_body(hub_device, hub_assertion)))
    session_factory_calls: list = []

    result = discover_and_join(
        state_repository=repo,
        signer=FakeSigner(own_device),
        trust_store=_trust_store(tmp_path, owner_key),
        trusted_now=now,
        session_factory=_session_factory(session, session_factory_calls),
        beacon_listener=lambda timeout_seconds: _pointer(hub_device),
    )

    assert result == AutoJoinResult.FAILED
    assert session.post_calls == []
    assert repo.save_calls == []


# ── 6. not activated ────────────────────────────────────────────────────

def test_not_activated_device_does_nothing(tmp_path):
    owner_key = Ed25519PrivateKey.generate()
    repo = FakeStateRepository(None)  # never activated -- no row at all

    session_factory_calls: list = []
    beacon_calls: list = []

    result = discover_and_join(
        state_repository=repo,
        signer=FakeSigner(Device()),
        trust_store=_trust_store(tmp_path, owner_key),
        trusted_now=datetime.now(timezone.utc),
        session_factory=_refusing_session_factory(session_factory_calls),
        beacon_listener=_refusing_beacon_listener(beacon_calls),
    )

    assert result == AutoJoinResult.NOT_ACTIVATED
    assert beacon_calls == []
    assert session_factory_calls == []
    assert repo.save_calls == []


# ── 7. already joined to that same hub ─────────────────────────────────

def test_already_joined_to_the_same_hub_is_a_no_op(tmp_path):
    owner_key = Ed25519PrivateKey.generate()
    own_device = Device()
    own_assertion = own_device.build_assertion(owner_key)
    hub_device = Device()
    # This device already uses HUB_URL as its relay -- persisted from a
    # prior successful autojoin.
    repo = FakeStateRepository(_own_state(own_device, own_assertion, sync_relay_base_url=HUB_URL))

    session_factory_calls: list = []

    result = discover_and_join(
        state_repository=repo,
        signer=FakeSigner(own_device),
        trust_store=_trust_store(tmp_path, owner_key),
        trusted_now=datetime.now(timezone.utc),
        session_factory=_refusing_session_factory(session_factory_calls),
        beacon_listener=lambda timeout_seconds: _pointer(hub_device),
    )

    assert result == AutoJoinResult.ALREADY_JOINED
    assert session_factory_calls == []  # no network write -- no network call at all
    assert repo.save_calls == []


# ── 8. /join returns non-200 ────────────────────────────────────────────

def test_join_rejected_by_hub_is_not_persisted(tmp_path):
    """MUTATION-PROOF (verbatim, see task report): moving
    `own_record.sync_relay_base_url = hub_url; state_repository.save(...)`
    to BEFORE the `join_response.status_code != 200` check turns this test
    RED -- `repo.save_calls` becomes non-empty and `repo.load().sync_relay_
    base_url` becomes `HUB_URL` even though the hub refused the join.
    """
    owner_key = Ed25519PrivateKey.generate()
    own_device = Device()
    own_assertion = own_device.build_assertion(owner_key)
    repo = FakeStateRepository(_own_state(own_device, own_assertion, sync_relay_base_url=None))

    hub_device = Device()
    hub_assertion = hub_device.build_assertion(owner_key)  # same licence, trusted -- verification succeeds

    session = FakeSession(
        get_response=FakeResponse(200, _identity_body(hub_device, hub_assertion)),
        post_response=FakeResponse(400, {"reason_code": "INSTALLATION_REVOKED"}),
    )
    session_factory_calls: list = []

    result = discover_and_join(
        state_repository=repo,
        signer=FakeSigner(own_device),
        trust_store=_trust_store(tmp_path, owner_key),
        trusted_now=datetime.now(timezone.utc),
        session_factory=_session_factory(session, session_factory_calls),
        beacon_listener=lambda timeout_seconds: _pointer(hub_device),
    )

    assert result == AutoJoinResult.FAILED
    assert len(session.post_calls) == 1  # /join WAS attempted...
    assert repo.save_calls == []  # ...but never persisted
    assert repo.load().sync_relay_base_url is None


# ── beacon pointer extraction: unauthenticated by design ────────────────

def test_extract_beacon_pointer_ignores_signature_and_reads_only_the_pointer_fields():
    """Documents, with a real assertion, the module's own central claim: the
    beacon is a POINTER, not a credential. A well-formed-but-garbage
    signature does not stop the pointer fields from being read -- nothing
    downstream trusts them without the separate, independent assertion
    verification this file's other tests exercise."""
    import json as _json

    datagram = _json.dumps(
        {
            "installation_id": "hub-inst-1",
            "url": HUB_URL,
            "spki_pin": HUB_SPKI_PIN,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "signature": "not-a-real-signature",
        }
    ).encode("utf-8")

    pointer = autojoin._extract_beacon_pointer(datagram)

    assert pointer == {"installation_id": "hub-inst-1", "url": HUB_URL, "spki_pin": HUB_SPKI_PIN}


def test_extract_beacon_pointer_rejects_oversized_datagram_before_parsing():
    oversized = b"{" + b"x" * autojoin.beacon.BEACON_MAX_DATAGRAM_BYTES
    assert autojoin._extract_beacon_pointer(oversized) is None


def test_extract_beacon_pointer_rejects_missing_fields():
    import json as _json

    assert autojoin._extract_beacon_pointer(_json.dumps({"url": HUB_URL}).encode("utf-8")) is None
    assert autojoin._extract_beacon_pointer(b"not json at all") is None
    assert autojoin._extract_beacon_pointer(_json.dumps(["not", "a", "dict"]).encode("utf-8")) is None
