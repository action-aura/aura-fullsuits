import base64
from datetime import datetime, timedelta, timezone

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization

from commercial_runtime.licensing_contracts.activation import ActivationFailed, ActivationPending, perform_activation
from commercial_runtime.licensing_contracts.canonical import canonicalize_bytes
from commercial_runtime.licensing_contracts.client import LicensingClientError
from commercial_runtime.licensing_contracts.events import LicensingEventRecorder
from commercial_runtime.licensing_contracts.state_machine import LicenseState
from commercial_runtime.licensing_contracts.state_repository import LicenseStateRepository
from commercial_runtime.licensing_contracts.trust_store import OwnerTrustStore

NOW = datetime.now(timezone.utc)
DEVICE_FINGERPRINT = "fp-device-1"


def _b64_pub(private_key):
    raw = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw
    )
    return base64.b64encode(raw).decode("ascii")


def _payload(installation_id, **overrides):
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
        "device_key_fingerprint": DEVICE_FINGERPRINT,
        "entitlements": {"max_devices": 2},
        "offline_policy": {
            "check_in_interval_seconds": 86400,
            "retry_interval_seconds": 3600,
            "offline_grace_seconds": 1209600,
            "warning_start_seconds": 864000,
            "hard_expiry_behavior": "RESTRICT_COMMERCIAL_FEATURES_PRESERVE_DATA",
            "clock_rollback_tolerance_seconds": 300,
            "assertion_refresh_threshold_seconds": 86400,
        },
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


class FakeSigner:
    def sign(self, canonical_bytes: bytes) -> str:
        return "fake-device-signature"

    def get_public_key_b64(self) -> str:
        return "fake-device-pub"


class FakeClient:
    def __init__(self, response=None, error=None):
        self._response = response
        self._error = error
        self.activate_calls = []

    def activate(self, **kwargs):
        self.activate_calls.append(kwargs)
        if self._error:
            raise self._error
        return self._response


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


def _activate(client, trust_store, state_repo, events, **overrides):
    defaults = dict(
        client=client,
        signer=FakeSigner(),
        trust_store=trust_store,
        state_repository=state_repo,
        event_recorder=events,
        product_code="AURA_RETAIL",
        platform="WINDOWS",
        app_version="1.0.0-rc.2",
        release_channel="rc",
        license_key="AURA-RETAIL-XXXX-YYYY-ZZZZ",
        device_public_key_fingerprint=DEVICE_FINGERPRINT,
    )
    defaults.update(overrides)
    return perform_activation(**defaults)


def test_successful_activation_persists_state_and_returns_owner_installation_id(owner_key, trust_store, state_repo, events):
    envelope = _envelope(owner_key, "owner-1", _payload("owner-assigned-inst-1"))
    client = FakeClient(response={
        "result": "SUCCESS",
        "reason_code": "ACTIVATION_APPROVED",
        "installation_id": "owner-assigned-inst-1",
        "signed_assertion": envelope,
    })

    result = _activate(client, trust_store, state_repo, events)

    assert result.state == LicenseState.ACTIVE_ONLINE
    assert result.owner_installation_id == "owner-assigned-inst-1"
    loaded = state_repo.load()
    assert loaded.owner_installation_id == "owner-assigned-inst-1"
    assert loaded.current_state == "ACTIVE_ONLINE"


def test_owner_installation_id_is_not_the_client_generated_one(owner_key, trust_store, state_repo, events):
    envelope = _envelope(owner_key, "owner-1", _payload("owner-assigned-inst-1"))
    client = FakeClient(response={
        "result": "SUCCESS",
        "installation_id": "owner-assigned-inst-1",
        "signed_assertion": envelope,
    })
    _activate(client, trust_store, state_repo, events)
    sent_installation_id = client.activate_calls[0]["installation_id"]
    # The server-assigned id must differ from whatever the client proposed --
    # if this test's fixture ever accidentally used the same string it would
    # not catch the real bug this guards against, so assert they differ.
    assert sent_installation_id != "owner-assigned-inst-1"


def test_rejected_activation_raises_and_records_event(trust_store, state_repo, events):
    client = FakeClient(response={"result": "REJECTED", "reason_code": "ACTIVATION_REJECTED"})
    with pytest.raises(ActivationFailed) as exc:
        _activate(client, trust_store, state_repo, events)
    assert exc.value.reason_code == "ACTIVATION_REJECTED"
    assert state_repo.load() is None
    assert "ACTIVATION_FAILED" in [e.event_type for e in events.recent()]


def test_network_failure_raises_activation_failed(trust_store, state_repo, events):
    client = FakeClient(error=LicensingClientError("NETWORK_UNAVAILABLE", "down"))
    with pytest.raises(ActivationFailed) as exc:
        _activate(client, trust_store, state_repo, events)
    assert exc.value.reason_code == "NETWORK_UNAVAILABLE"


def test_unverifiable_assertion_on_claimed_success_is_not_trusted(owner_key, trust_store, state_repo, events):
    # Owner claims SUCCESS but the assertion is signed by an untrusted key --
    # must not activate.
    attacker_key = Ed25519PrivateKey.generate()
    envelope = _envelope(attacker_key, "attacker-key", _payload("owner-assigned-inst-1"))
    client = FakeClient(response={
        "result": "SUCCESS",
        "installation_id": "owner-assigned-inst-1",
        "signed_assertion": envelope,
    })
    with pytest.raises(ActivationFailed):
        _activate(client, trust_store, state_repo, events)
    assert state_repo.load() is None


def test_full_license_key_sent_exactly_once(owner_key, trust_store, state_repo, events):
    envelope = _envelope(owner_key, "owner-1", _payload("owner-assigned-inst-1"))
    client = FakeClient(response={
        "result": "SUCCESS",
        "installation_id": "owner-assigned-inst-1",
        "signed_assertion": envelope,
    })
    _activate(client, trust_store, state_repo, events, license_key="AURA-RETAIL-SECRET-KEY")
    assert len(client.activate_calls) == 1
    assert client.activate_calls[0]["license_key"] == "AURA-RETAIL-SECRET-KEY"


# ── Android path: ingest_activation_response() (Part U) ───────────────────
# Kotlin already made the signed HTTP call; Python only verifies+persists.

from commercial_runtime.licensing_contracts.activation import ingest_activation_response


def test_ingest_activation_response_persists_state(owner_key, trust_store, state_repo, events):
    envelope = _envelope(owner_key, "owner-1", _payload("owner-assigned-inst-1"))
    response = {"result": "SUCCESS", "installation_id": "owner-assigned-inst-1", "signed_assertion": envelope}

    result = ingest_activation_response(
        response,
        trust_store=trust_store,
        state_repository=state_repo,
        event_recorder=events,
        product_code="AURA_RETAIL",
        platform="WINDOWS",
        device_public_key_fingerprint=DEVICE_FINGERPRINT,
    )

    assert result.state == LicenseState.ACTIVE_ONLINE
    assert state_repo.load().owner_installation_id == "owner-assigned-inst-1"


def test_ingest_activation_response_never_trusts_kotlins_claimed_success(owner_key, trust_store, state_repo, events):
    # Simulates a compromised/buggy Kotlin layer claiming SUCCESS with a
    # forged assertion -- Python must independently reject it regardless.
    attacker_key = Ed25519PrivateKey.generate()
    envelope = _envelope(attacker_key, "attacker-key", _payload("owner-assigned-inst-1"))
    response = {"result": "SUCCESS", "installation_id": "owner-assigned-inst-1", "signed_assertion": envelope}

    with pytest.raises(ActivationFailed):
        ingest_activation_response(
            response,
            trust_store=trust_store,
            state_repository=state_repo,
            event_recorder=events,
            product_code="AURA_RETAIL",
            platform="WINDOWS",
            device_public_key_fingerprint=DEVICE_FINGERPRINT,
        )
    assert state_repo.load() is None


def test_ingest_activation_response_rejects_malformed_response(trust_store, state_repo, events):
    with pytest.raises(ActivationFailed) as exc:
        ingest_activation_response(
            {"result": "SUCCESS"},  # missing installation_id/signed_assertion
            trust_store=trust_store,
            state_repository=state_repo,
            event_recorder=events,
            product_code="AURA_RETAIL",
            platform="WINDOWS",
            device_public_key_fingerprint=DEVICE_FINGERPRINT,
        )
    assert exc.value.reason_code == "MALFORMED_RESPONSE"


def test_pending_activation_raises_activation_pending_not_failed(trust_store, state_repo, events):
    """Phase 8 Part O: PENDING is not a failure -- must raise a distinct
    exception type, never ActivationFailed, so a caller that only catches
    ActivationFailed does not mistakenly treat an awaiting-approval
    activation as rejected."""
    client = FakeClient(response={"result": "PENDING", "reason_code": "ACTIVATION_PENDING_REVIEW", "installation_id": "owner-assigned-inst-2"})
    with pytest.raises(ActivationPending) as exc:
        _activate(client, trust_store, state_repo, events)
    assert exc.value.reason_code == "ACTIVATION_PENDING_REVIEW"
    assert exc.value.installation_id == "owner-assigned-inst-2"
    assert state_repo.load() is None
    assert "ACTIVATION_PENDING" in [e.event_type for e in events.recent()]
    assert "ACTIVATION_FAILED" not in [e.event_type for e in events.recent()]


# ── Activation-time trust refresh (launch-readiness CRITICAL) ─────────────
# A brand-new install whose BUNDLED anchor predates the current Owner signing
# key would otherwise fail activation with UNKNOWN_SIGNING_KEY forever: unlike
# the check-in path (LicenseCheckInScheduler.run_once refreshes the manifest
# before every cycle), activation verified once and gave up.


def _rotation_manifest_for(anchor_private, anchor_key_id, new_private, new_key_id):
    body = {
        "manifest_version": 1,
        "issued_at": NOW.isoformat(),
        "keys": [
            {"key_id": anchor_key_id, "public_key": _b64_pub(anchor_private), "algorithm": "ed25519", "status": "RETIRED"},
            {"key_id": new_key_id, "public_key": _b64_pub(new_private), "algorithm": "ed25519", "status": "ACTIVE"},
        ],
        "signed_by_key_id": new_key_id,
    }
    signable = {"manifest_version": body["manifest_version"], "issued_at": body["issued_at"], "keys": body["keys"]}
    canonical = canonicalize_bytes(signable)

    def _sig(private):
        return base64.b64encode(private.sign(canonical)).decode("ascii")

    return {
        **body,
        "signature": _sig(new_private),
        "signatures": [
            {"key_id": new_key_id, "algorithm": "ed25519", "signature": _sig(new_private)},
            {"key_id": anchor_key_id, "algorithm": "ed25519", "signature": _sig(anchor_private)},
        ],
    }


class RotatingFakeClient(FakeClient):
    """Owner has rotated: the assertion is signed by the new key, and
    /signing-keys serves a manifest countersigned by the outgoing key."""

    def __init__(self, response, manifest):
        super().__init__(response=response)
        self._manifest = manifest
        self.signing_key_fetches = 0

    def fetch_signing_keys(self):
        self.signing_key_fetches += 1
        return self._manifest


def test_activation_recovers_from_a_stale_bundled_anchor_after_rotation(owner_key, trust_store, state_repo, events):
    rotated_key = Ed25519PrivateKey.generate()
    envelope = _envelope(rotated_key, "owner-2", _payload("owner-assigned-inst-9"))
    client = RotatingFakeClient(
        response={"result": "SUCCESS", "installation_id": "owner-assigned-inst-9", "signed_assertion": envelope},
        manifest=_rotation_manifest_for(owner_key, "owner-1", rotated_key, "owner-2"),
    )

    result = _activate(client, trust_store, state_repo, events)

    assert result.state == LicenseState.ACTIVE_ONLINE
    assert client.signing_key_fetches == 1
    assert trust_store.is_trusted("owner-2") is True


def test_activation_trust_refresh_never_admits_a_never_trusted_signer(owner_key, trust_store, state_repo, events):
    """The refresh is not an escape hatch: a manifest whose signers this
    install has never trusted leaves the trust store untouched and activation
    still fails."""
    attacker_key = Ed25519PrivateKey.generate()
    envelope = _envelope(attacker_key, "attacker-key", _payload("owner-assigned-inst-9"))
    client = RotatingFakeClient(
        response={"result": "SUCCESS", "installation_id": "owner-assigned-inst-9", "signed_assertion": envelope},
        manifest=_rotation_manifest_for(Ed25519PrivateKey.generate(), "unknown-0", attacker_key, "attacker-key"),
    )

    with pytest.raises(ActivationFailed) as exc:
        _activate(client, trust_store, state_repo, events)

    assert exc.value.reason_code == "UNKNOWN_SIGNING_KEY"
    assert trust_store.is_trusted("attacker-key") is False
    assert state_repo.load() is None


def test_ingest_activation_response_pending_raises_and_persists_nothing(trust_store, state_repo, events):
    with pytest.raises(ActivationPending) as exc:
        ingest_activation_response(
            {"result": "PENDING", "reason_code": "ACTIVATION_PENDING_REVIEW", "installation_id": "owner-assigned-inst-3"},
            trust_store=trust_store,
            state_repository=state_repo,
            event_recorder=events,
            product_code="AURA_RETAIL",
            platform="WINDOWS",
            device_public_key_fingerprint=DEVICE_FINGERPRINT,
        )
    assert exc.value.installation_id == "owner-assigned-inst-3"
    assert state_repo.load() is None


def test_unknown_signing_key_failure_records_both_key_ids(owner_key, trust_store, state_repo, events):
    """The stranded-trust-store diagnostic (2026-09-04, Mi Note 10).

    A device whose trust_store.json predates Owner's current signing key fails
    every activation with UNKNOWN_SIGNING_KEY and nothing else -- no hint that
    the cause is a stale trust store rather than a bad assertion. The obvious
    check (trust_anchor.json, which was correct) actively misleads, because the
    anchor is only ever read when trust_store.json does NOT exist.

    Record which key signed and which keys this install actually trusts, so the
    next occurrence is a glance instead of a multi-session dig.
    """
    rotated_owner_key = Ed25519PrivateKey.generate()
    envelope = _envelope(rotated_owner_key, "owner-2-rotated", _payload("owner-assigned-inst-1"))
    client = FakeClient(response={
        "result": "SUCCESS",
        "installation_id": "owner-assigned-inst-1",
        "signed_assertion": envelope,
    })

    with pytest.raises(ActivationFailed) as exc:
        _activate(client, trust_store, state_repo, events)
    assert exc.value.reason_code == "UNKNOWN_SIGNING_KEY"

    failed = [e for e in events.recent() if e.event_type == "ACTIVATION_FAILED"]
    assert len(failed) == 1
    details = failed[0].details
    assert details["reason_code"] == "UNKNOWN_SIGNING_KEY"
    # Both sides of the disagreement, which is the entire point.
    assert details["assertion_signing_key_id"] == "owner-2-rotated"
    assert details["trusted_key_ids"] == ["owner-1"]


def test_other_failures_keep_the_bare_reason_code(owner_key, trust_store, state_repo, events):
    """The allow-half: the diagnostic is scoped to UNKNOWN_SIGNING_KEY alone.

    Without this, a _failure_details() that attached key ids to EVERY failure
    would satisfy the test above while quietly widening what every unrelated
    failure writes into the local event log.
    """
    envelope = _envelope(owner_key, "owner-1", _payload("owner-assigned-inst-1", product_code="AURA_CLINIC"))
    client = FakeClient(response={
        "result": "SUCCESS",
        "installation_id": "owner-assigned-inst-1",
        "signed_assertion": envelope,
    })

    with pytest.raises(ActivationFailed) as exc:
        _activate(client, trust_store, state_repo, events)
    assert exc.value.reason_code == "ASSERTION_PRODUCT_MISMATCH"

    failed = [e for e in events.recent() if e.event_type == "ACTIVATION_FAILED"]
    assert failed[0].details == {"reason_code": "ASSERTION_PRODUCT_MISMATCH"}


# ── Guarded re-anchor (2026-09-04) ────────────────────────────────────────────
# A store stranded by a DISCONTINUOUS Owner rotation -- a fresh deploy holding
# only its new key, so nothing this install trusts can countersign a manifest --
# had no recovery at all. Reinstalling did not help (app data survives), which
# is how a real Mi Note 10 stayed unlicensable. These cover both halves: it must
# rescue that device, and it must stay useless to anything the bundled anchor
# does not itself name.


def _anchor_file(tmp_path, name, *entries):
    import json as json_mod

    path = tmp_path / name
    path.write_text(
        json_mod.dumps({"keys": [
            {"key_id": key_id, "public_key": _b64_pub(key), "algorithm": "ed25519"}
            for key_id, key in entries
        ]}),
        encoding="utf-8",
    )
    return path


def test_a_discontinuous_rotation_recovers_from_the_bundled_anchor(owner_key, trust_store, state_repo, events, tmp_path):
    """The allow-half, and the whole reason this exists."""
    from commercial_runtime.licensing_contracts.activation import make_bundled_anchor_recovery

    fresh_owner = Ed25519PrivateKey.generate()
    envelope = _envelope(fresh_owner, "owner-2-fresh-deploy", _payload("owner-assigned-inst-1"))
    client = FakeClient(response={
        "result": "SUCCESS",
        "installation_id": "owner-assigned-inst-1",
        "signed_assertion": envelope,
    })
    anchor = _anchor_file(tmp_path, "trust_anchor.json", ("owner-2-fresh-deploy", fresh_owner))

    result = _activate(
        client, trust_store, state_repo, events,
        anchor_recovery=make_bundled_anchor_recovery(anchor, trust_store),
    )

    assert result.state == LicenseState.ACTIVE_ONLINE
    assert trust_store.is_trusted("owner-2-fresh-deploy") is True
    # MERGE, not replace: an install that had legitimately rotated forward must
    # never be dragged back to its build's anchor.
    assert trust_store.is_trusted("owner-1") is True
    assert any(e.event_type == "TRUST_ANCHOR_READMITTED" for e in events.recent()), (
        "a re-anchor is security-relevant and must never happen silently"
    )


def test_recovery_never_admits_a_signer_the_bundled_anchor_does_not_name(owner_key, trust_store, state_repo, events, tmp_path):
    """The deny-half. Recovery is not "trust whoever answered": the anchor is
    the only thing that can introduce a key, so a forged assertion from a key
    named nowhere is still refused, and nothing is admitted."""
    from commercial_runtime.licensing_contracts.activation import make_bundled_anchor_recovery

    attacker = Ed25519PrivateKey.generate()
    envelope = _envelope(attacker, "attacker-key", _payload("owner-assigned-inst-1"))
    client = FakeClient(response={
        "result": "SUCCESS",
        "installation_id": "owner-assigned-inst-1",
        "signed_assertion": envelope,
    })
    # A legitimate anchor: it names the key this store already has, so recovery
    # can admit nothing new.
    anchor = _anchor_file(tmp_path, "trust_anchor.json", ("owner-1", owner_key))

    with pytest.raises(ActivationFailed) as exc:
        _activate(
            client, trust_store, state_repo, events,
            anchor_recovery=make_bundled_anchor_recovery(anchor, trust_store),
        )

    assert exc.value.reason_code == "UNKNOWN_SIGNING_KEY"
    assert trust_store.is_trusted("attacker-key") is False
    assert state_repo.load() is None
    assert not any(e.event_type == "TRUST_ANCHOR_READMITTED" for e in events.recent())


def test_the_anchor_cannot_swap_the_key_material_behind_a_trusted_key_id(owner_key, trust_store, state_repo, events, tmp_path):
    """Add-only, and the sharpest edge of the whole change.

    A tampered anchor that REUSES a key_id this store already trusts must not
    replace the public key behind it -- otherwise editing one file would
    silently re-point an established identity at an attacker's key, which is
    exactly the trust-on-first-use hole the seed-once rule existed to prevent.
    Recovery has to actually RUN for this to prove anything. An assertion
    signed with a key id the store already knows fails as
    ASSERTION_VERIFICATION_FAILED, which never reaches the re-anchor branch at
    all -- a version of this test written that way passed while exercising
    nothing. So the assertion is signed by a genuinely unknown key (forcing
    UNKNOWN_SIGNING_KEY and therefore recovery), and the anchor smuggles a
    SECOND entry that tries to re-point the established id.
    """
    from commercial_runtime.licensing_contracts.activation import make_bundled_anchor_recovery

    fresh_owner = Ed25519PrivateKey.generate()
    attacker = Ed25519PrivateKey.generate()
    envelope = _envelope(fresh_owner, "owner-2-fresh-deploy", _payload("owner-assigned-inst-1"))
    client = FakeClient(response={
        "result": "SUCCESS",
        "installation_id": "owner-assigned-inst-1",
        "signed_assertion": envelope,
    })
    anchor = _anchor_file(
        tmp_path, "trust_anchor.json",
        ("owner-2-fresh-deploy", fresh_owner),   # the legitimate new key
        ("owner-1", attacker),                   # the smuggled re-point
    )

    result = _activate(
        client, trust_store, state_repo, events,
        anchor_recovery=make_bundled_anchor_recovery(anchor, trust_store),
    )

    # Recovery ran and did its job for the genuinely new key...
    assert result.state == LicenseState.ACTIVE_ONLINE
    assert trust_store.is_trusted("owner-2-fresh-deploy") is True
    # ...while the established identity keeps its ORIGINAL key material.
    assert trust_store.get_public_key_b64("owner-1") == _b64_pub(owner_key)
    assert trust_store.get_public_key_b64("owner-1") != _b64_pub(attacker)


def test_a_missing_bundled_anchor_leaves_the_original_failure_intact(owner_key, trust_store, state_repo, events, tmp_path):
    """A build with no anchor file must report why verification failed, never
    a file-io error the customer cannot act on."""
    from commercial_runtime.licensing_contracts.activation import make_bundled_anchor_recovery

    fresh_owner = Ed25519PrivateKey.generate()
    envelope = _envelope(fresh_owner, "owner-2-fresh-deploy", _payload("owner-assigned-inst-1"))
    client = FakeClient(response={
        "result": "SUCCESS",
        "installation_id": "owner-assigned-inst-1",
        "signed_assertion": envelope,
    })

    with pytest.raises(ActivationFailed) as exc:
        _activate(
            client, trust_store, state_repo, events,
            anchor_recovery=make_bundled_anchor_recovery(tmp_path / "does-not-exist.json", trust_store),
        )

    assert exc.value.reason_code == "UNKNOWN_SIGNING_KEY"
