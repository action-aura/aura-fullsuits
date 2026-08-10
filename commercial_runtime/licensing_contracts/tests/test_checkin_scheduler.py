import base64
from datetime import datetime, timedelta, timezone

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization

from commercial_runtime.licensing_contracts.canonical import canonicalize_bytes
from commercial_runtime.licensing_contracts.checkin_scheduler import LicenseCheckInScheduler
from commercial_runtime.licensing_contracts.client import LicensingClientError
from commercial_runtime.licensing_contracts.events import LicensingEventRecorder
from commercial_runtime.licensing_contracts.state_machine import LicenseState
from commercial_runtime.licensing_contracts.state_repository import (
    LICENSING_SCHEMA_VERSION,
    LicenseStateRecord,
    LicenseStateRepository,
)
from commercial_runtime.licensing_contracts.trust_store import OwnerTrustStore

NOW = datetime.now(timezone.utc)
INSTALLATION_ID = "inst-1"
DEVICE_FINGERPRINT = "fp-device-1"


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
        "installation_public_id": INSTALLATION_ID,
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


class FakeSigner:
    def sign(self, canonical_bytes: bytes) -> str:
        return "fake-device-signature"

    def get_public_key_b64(self) -> str:
        return "fake-device-pub"


class FakeClient:
    def __init__(self, checkin_responses=None, signing_keys_response=None):
        self._checkin_responses = list(checkin_responses or [])
        self._signing_keys_response = signing_keys_response or {"keys": []}
        self.checkin_calls = 0

    def check_in(self, *, installation_id, signer):
        self.checkin_calls += 1
        item = self._checkin_responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def fetch_signing_keys(self):
        return self._signing_keys_response


@pytest.fixture(autouse=True)
def _clear_anchor_cache():
    """The process-lifetime anchor cache (Phase 7V-F, trusted_time.py) is
    module-level global state keyed by installation id -- every test in
    this file reuses the same INSTALLATION_ID constant, so without clearing
    it a cached anchor from one test would leak into the next."""
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


def _seed_activated_record(state_repo, **overrides):
    defaults = dict(
        licensing_schema_version=LICENSING_SCHEMA_VERSION,
        product_code="AURA_RETAIL",
        platform="WINDOWS",
        current_state="ACTIVE_ONLINE",
        owner_installation_id=INSTALLATION_ID,
        device_public_key_fingerprint=DEVICE_FINGERPRINT,
        last_successful_checkin_at=NOW.isoformat(),
    )
    defaults.update(overrides)
    record = LicenseStateRecord(**defaults)
    state_repo.save(record)
    return record


def _scheduler(client, trust_store, state_repo, events, **overrides):
    defaults = dict(
        client=client,
        signer=FakeSigner(),
        trust_store=trust_store,
        state_repository=state_repo,
        event_recorder=events,
        product_code="AURA_RETAIL",
        platform="WINDOWS",
        device_public_key_fingerprint=DEVICE_FINGERPRINT,
    )
    defaults.update(overrides)
    return LicenseCheckInScheduler(**defaults)


def test_run_once_noop_before_activation(state_repo, trust_store, events):
    state_repo.save(
        LicenseStateRecord(
            licensing_schema_version=LICENSING_SCHEMA_VERSION,
            product_code="AURA_RETAIL",
            platform="WINDOWS",
            current_state="ACTIVATION_REQUIRED",
        )
    )
    scheduler = _scheduler(FakeClient(), trust_store, state_repo, events)
    result = scheduler.run_once()
    assert result == LicenseState.ACTIVATION_REQUIRED


def test_successful_checkin_persists_new_assertion_and_reaches_active_online(owner_key, trust_store, state_repo, events):
    _seed_activated_record(state_repo, current_state="ACTIVE_ONLINE", assertion_envelope_json=None)
    envelope = _envelope(owner_key, "owner-1", _payload())
    client = FakeClient(checkin_responses=[{"result": "SUCCESS", "signed_assertion": envelope}])
    scheduler = _scheduler(client, trust_store, state_repo, events)

    result = scheduler.run_once()

    assert result == LicenseState.ACTIVE_ONLINE
    loaded = state_repo.load()
    assert loaded.assertion_id == "a-1"
    assert loaded.license_status == "ACTIVE"
    event_types = [e.event_type for e in events.recent()]
    assert "CHECK_IN_SUCCEEDED" in event_types
    assert "ASSERTION_ACCEPTED" in event_types


def test_network_failure_falls_back_to_offline_evaluation_not_immediate_restriction(owner_key, trust_store, state_repo, events):
    envelope = _envelope(owner_key, "owner-1", _payload())
    import json

    _seed_activated_record(
        state_repo,
        assertion_envelope_json=json.dumps(envelope),
        trusted_time_anchor_server_time=NOW.isoformat(),
        last_successful_checkin_at=NOW.isoformat(),
    )
    client = FakeClient(checkin_responses=[LicensingClientError("NETWORK_UNAVAILABLE", "down")])
    scheduler = _scheduler(client, trust_store, state_repo, events)

    result = scheduler.run_once()

    # Recently checked in, assertion still valid -- a single network blip
    # must not immediately restrict.
    assert result in (LicenseState.ACTIVE_ONLINE, LicenseState.ACTIVE_OFFLINE)
    event_types = [e.event_type for e in events.recent()]
    assert "CHECK_IN_FAILED" in event_types


def test_tampered_response_assertion_is_rejected_and_old_state_retained(owner_key, trust_store, state_repo, events):
    import json

    original_envelope = _envelope(owner_key, "owner-1", _payload())
    _seed_activated_record(
        state_repo,
        assertion_envelope_json=json.dumps(original_envelope),
        trusted_time_anchor_server_time=NOW.isoformat(),
        last_successful_checkin_at=NOW.isoformat(),
    )

    tampered_envelope = _envelope(owner_key, "owner-1", _payload())
    tampered_envelope["payload"]["allowed_device_count"] = 999  # invalidates the signature
    client = FakeClient(checkin_responses=[{"result": "SUCCESS", "signed_assertion": tampered_envelope}])
    scheduler = _scheduler(client, trust_store, state_repo, events)

    scheduler.run_once()

    loaded = state_repo.load()
    # The ORIGINAL assertion must still be what's persisted -- never
    # overwritten by an unverifiable response (Part L).
    assert loaded.assertion_envelope_json == json.dumps(original_envelope)
    event_types = [e.event_type for e in events.recent()]
    assert "ASSERTION_REJECTED" in event_types


def test_long_offline_gap_transitions_to_restricted_and_fires_event(owner_key, trust_store, state_repo, events):
    import json

    envelope = _envelope(owner_key, "owner-1", _payload(expires_at=(NOW + timedelta(days=30)).isoformat()))
    long_ago = NOW - timedelta(days=20)
    _seed_activated_record(
        state_repo,
        assertion_envelope_json=json.dumps(envelope),
        trusted_time_anchor_server_time=NOW.isoformat(),
        last_successful_checkin_at=long_ago.isoformat(),
    )
    client = FakeClient(checkin_responses=[LicensingClientError("NETWORK_UNAVAILABLE", "down")])
    scheduler = _scheduler(client, trust_store, state_repo, events)

    result = scheduler.run_once()

    assert result == LicenseState.RESTRICTED
    loaded = state_repo.load()
    assert loaded.current_state == "RESTRICTED"
    event_types = [e.event_type for e in events.recent()]
    assert "RESTRICTED_MODE_ENTERED" in event_types


def test_start_and_stop_lifecycle_does_not_raise(trust_store, state_repo, events):
    state_repo.save(
        LicenseStateRecord(
            licensing_schema_version=LICENSING_SCHEMA_VERSION,
            product_code="AURA_RETAIL",
            platform="WINDOWS",
            current_state="ACTIVATION_REQUIRED",
        )
    )
    scheduler = _scheduler(FakeClient(), trust_store, state_repo, events)
    scheduler.start(interval_seconds=3600)
    scheduler.stop()  # must cancel cleanly, no dangling timer firing later


# ── Android path: ingest_checkin_response() / reevaluate_only() (Part U) ──
# Kotlin already made the signed HTTP call (run_once() is Windows-only);
# Python only independently verifies and re-evaluates.


def test_ingest_checkin_response_persists_and_verifies(owner_key, trust_store, state_repo, events):
    _seed_activated_record(state_repo, current_state="ACTIVE_ONLINE", assertion_envelope_json=None)
    envelope = _envelope(owner_key, "owner-1", _payload())
    scheduler = _scheduler(FakeClient(), trust_store, state_repo, events)

    result = scheduler.ingest_checkin_response({"result": "SUCCESS", "signed_assertion": envelope})

    assert result == LicenseState.ACTIVE_ONLINE
    assert state_repo.load().assertion_id == "a-1"


def test_ingest_checkin_response_never_trusts_forged_assertion(trust_store, state_repo, events):
    import json

    attacker_key = Ed25519PrivateKey.generate()
    original_envelope = _envelope(attacker_key, "owner-1", _payload())  # wrong key, would fail verify anyway
    _seed_activated_record(
        state_repo,
        assertion_envelope_json=json.dumps(_envelope(Ed25519PrivateKey.generate(), "owner-1", _payload())),
    )
    scheduler = _scheduler(FakeClient(), trust_store, state_repo, events)

    forged_envelope = _envelope(attacker_key, "owner-1", _payload())
    result = scheduler.ingest_checkin_response({"result": "SUCCESS", "signed_assertion": forged_envelope})

    # Untrusted key -> rejected -> falls back to re-evaluating the OLD
    # (also-unverifiable-in-this-test, deliberately) stored assertion, never
    # silently promoted to ACTIVE_ONLINE off the forged one.
    assert result != LicenseState.ACTIVE_ONLINE or state_repo.load().assertion_id != forged_envelope["payload"]["assertion_id"]


def test_reevaluate_only_does_not_call_client(owner_key, trust_store, state_repo, events):
    import json

    envelope = _envelope(owner_key, "owner-1", _payload())
    _seed_activated_record(
        state_repo,
        assertion_envelope_json=json.dumps(envelope),
        trusted_time_anchor_server_time=NOW.isoformat(),
        last_successful_checkin_at=NOW.isoformat(),
    )
    client = FakeClient()
    scheduler = _scheduler(client, trust_store, state_repo, events)

    scheduler.reevaluate_only(checkin_ok=False)

    assert client.checkin_calls == 0


def test_elapsed_offline_time_accumulates_across_separate_scheduler_instances(
    owner_key, trust_store, state_repo, events, monkeypatch
):
    """Phase 7V-F regression: routes.py builds a brand-new
    LicenseCheckInScheduler on every single HTTP request (by design, so a
    corrected config takes effect without a restart) -- so a real
    installation's "did Owner ever come back" evaluation happens across many
    separate scheduler instances over real wall-clock time, never one
    long-lived object. Before the fix, _resolve_anchor() called
    rehydrate_anchor() fresh on every instance, which re-pins the monotonic
    reference to "right now" every time -- so trusted_now() always collapsed
    back to the persisted (unchanged, since Owner is unreachable)
    trusted_time_anchor_server_time, and elapsed offline duration could
    never advance no matter how much real time passed. This test simulates
    two separate scheduler instances (matching the real per-request
    pattern) evaluating the SAME persisted record, with a real gap of
    monotonic time between them, and asserts the second instance sees the
    gap reflected in its evaluation -- proving the anchor is cached across
    instances (keyed by installation id + unchanged server_time), not
    re-pinned fresh each time.
    """
    import json
    import time as time_module

    envelope = _envelope(owner_key, "owner-1", _payload(expires_at=(NOW + timedelta(days=1)).isoformat()))
    _seed_activated_record(
        state_repo,
        assertion_envelope_json=json.dumps(envelope),
        trusted_time_anchor_server_time=NOW.isoformat(),
        last_successful_checkin_at=NOW.isoformat(),
    )
    client = FakeClient(checkin_responses=[LicensingClientError("NETWORK_UNAVAILABLE", "down")])

    # First instance (e.g. the first failed check-in request) pins the anchor.
    scheduler_a = _scheduler(client, trust_store, state_repo, events)
    fake_monotonic = [1000.0]
    monkeypatch.setattr(time_module, "monotonic", lambda: fake_monotonic[0])
    scheduler_a.run_once()

    # A second, independently-constructed instance (the NEXT HTTP request,
    # some real time later) must see that elapsed time -- not reset to zero.
    fake_monotonic[0] = 1000.0 + 40.0  # 40 real seconds later, Owner still down
    client2 = FakeClient(checkin_responses=[LicensingClientError("NETWORK_UNAVAILABLE", "down")])
    scheduler_b = _scheduler(client2, trust_store, state_repo, events)
    anchor = scheduler_b._resolve_anchor(state_repo.load())
    from commercial_runtime.licensing_contracts.trusted_time import trusted_now

    observed_now = trusted_now(anchor)
    # Must reflect the real 40s gap, not collapse back to the original
    # anchor.server_time (which the pre-fix behavior always did).
    assert observed_now >= NOW + timedelta(seconds=39)


# -- Phase 8V-P9: real stale-assertion (monotonicity) guard -----------------

def test_older_valid_assertion_rejected_as_stale_after_newer_one_accepted(owner_key, trust_store, state_repo, events):
    # Phase 8V-P9 real finding: verify_assertion() alone (signature + validity
    # window + identity match) never checked whether an assertion was newer
    # than the one already stored -- a genuinely valid, still-unexpired OLDER
    # assertion could silently overwrite a newer one and revert local state.
    # This is the direct regression for that fix.
    _seed_activated_record(state_repo, current_state="ACTIVE_ONLINE", assertion_envelope_json=None)
    scheduler = _scheduler(FakeClient(), trust_store, state_repo, events)

    assertion_a = _envelope(owner_key, "owner-1", _payload(
        assertion_id="a-old", issued_at=NOW.isoformat(), allowed_device_count=2,
    ))
    result_a = scheduler.ingest_checkin_response({"result": "SUCCESS", "signed_assertion": assertion_a})
    assert result_a == LicenseState.ACTIVE_ONLINE
    assert state_repo.load().assertion_id == "a-old"

    assertion_b = _envelope(owner_key, "owner-1", _payload(
        assertion_id="b-new", issued_at=(NOW + timedelta(minutes=5)).isoformat(), allowed_device_count=1,
    ))
    result_b = scheduler.ingest_checkin_response({"result": "SUCCESS", "signed_assertion": assertion_b})
    assert result_b == LicenseState.ACTIVE_ONLINE
    assert state_repo.load().assertion_id == "b-new"

    # Deliver the OLDER assertion A again, after B was already accepted --
    # A is still a genuinely, independently valid signature within its own
    # signed window (not tampered, not forged) -- must be rejected as stale.
    result_replay = scheduler.ingest_checkin_response({"result": "SUCCESS", "signed_assertion": assertion_a})
    loaded = state_repo.load()
    assert loaded.assertion_id == "b-new"  # unchanged -- A never overwrote B
    event_types = [e.event_type for e in events.recent()]
    assert "ASSERTION_STALE_REJECTED" in event_types
    # Exactly 2 real acceptances happened (A, then B) -- the stale replay of
    # A afterward must not produce a THIRD CHECK_IN_SUCCEEDED/ASSERTION_ACCEPTED
    # pair.
    assert event_types.count("CHECK_IN_SUCCEEDED") == 2
    assert event_types.count("ASSERTION_ACCEPTED") == 2


def test_first_ever_assertion_is_never_rejected_as_stale(owner_key, trust_store, state_repo, events):
    _seed_activated_record(state_repo, current_state="ACTIVE_ONLINE", assertion_envelope_json=None)
    scheduler = _scheduler(FakeClient(), trust_store, state_repo, events)

    envelope = _envelope(owner_key, "owner-1", _payload(assertion_id="first-ever"))
    result = scheduler.ingest_checkin_response({"result": "SUCCESS", "signed_assertion": envelope})

    assert result == LicenseState.ACTIVE_ONLINE
    assert state_repo.load().assertion_id == "first-ever"
    event_types = [e.event_type for e in events.recent()]
    assert "ASSERTION_STALE_REJECTED" not in event_types


def test_reevaluate_only_before_activation_is_noop(trust_store, state_repo, events):
    state_repo.save(
        LicenseStateRecord(
            licensing_schema_version=LICENSING_SCHEMA_VERSION,
            product_code="AURA_RETAIL",
            platform="WINDOWS",
            current_state="ACTIVATION_REQUIRED",
        )
    )
    scheduler = _scheduler(FakeClient(), trust_store, state_repo, events)
    assert scheduler.reevaluate_only() == LicenseState.ACTIVATION_REQUIRED


# -- AUDIT P0-3: fail closed on monotonic-clock-rollback corruption ---------
# trusted_time.trusted_now() raises TrustedTimeError when time.monotonic()
# reads backward since the anchor was pinned (never supposed to happen on
# any supported platform, but it fails closed rather than treating it as
# zero elapsed -- see that function's docstring). Before this fix, that
# exception was never caught anywhere in _reevaluate()'s call chain -- it
# propagated straight out of run_once()/reevaluate_only() as an unhandled
# exception (an unhandled 500 on the real /check-in HTTP route, since
# routes.py's check_in() has no try/except of its own around
# scheduler.run_once()).


def test_monotonic_rollback_during_reevaluate_yields_clock_review_required_not_a_crash(
    owner_key, trust_store, state_repo, events, monkeypatch
):
    import json
    import time as time_module

    envelope = _envelope(owner_key, "owner-1", _payload(expires_at=(NOW + timedelta(days=1)).isoformat()))
    _seed_activated_record(
        state_repo,
        assertion_envelope_json=json.dumps(envelope),
        trusted_time_anchor_server_time=NOW.isoformat(),
        last_successful_checkin_at=NOW.isoformat(),
    )
    scheduler = _scheduler(FakeClient(), trust_store, state_repo, events)

    # First evaluation pins the process-lifetime anchor cache (trusted_time.py)
    # at monotonic=1000.0 -- mirrors the real sequence (an anchor gets pinned
    # once, then reused across later re-evaluations of the same persisted
    # server_time).
    fake_monotonic = [1000.0]
    monkeypatch.setattr(time_module, "monotonic", lambda: fake_monotonic[0])
    first_result = scheduler.reevaluate_only(checkin_ok=False)
    assert first_result != LicenseState.CLOCK_REVIEW_REQUIRED  # sanity: no rollback yet

    # The monotonic clock reading backward is exactly the corruption this
    # guards against -- must never happen on any real platform, but must be
    # handled, not crash, if it somehow does.
    fake_monotonic[0] = 900.0

    result = scheduler.reevaluate_only(checkin_ok=False)  # must not raise

    assert result == LicenseState.CLOCK_REVIEW_REQUIRED
    assert state_repo.load().current_state == "CLOCK_REVIEW_REQUIRED"
    event_types = [e.event_type for e in events.recent()]
    assert "CLOCK_REVIEW_REQUIRED" in event_types


def test_monotonic_rollback_during_run_once_yields_clock_review_required_not_a_crash(
    owner_key, trust_store, state_repo, events, monkeypatch
):
    """Same corruption, but through run_once()'s failed-check-in fallback
    path (the real Windows /check-in route's actual call), not just
    reevaluate_only() directly."""
    import json
    import time as time_module

    envelope = _envelope(owner_key, "owner-1", _payload(expires_at=(NOW + timedelta(days=1)).isoformat()))
    _seed_activated_record(
        state_repo,
        assertion_envelope_json=json.dumps(envelope),
        trusted_time_anchor_server_time=NOW.isoformat(),
        last_successful_checkin_at=NOW.isoformat(),
    )
    client = FakeClient(checkin_responses=[LicensingClientError("NETWORK_UNAVAILABLE", "down")] * 2)
    scheduler = _scheduler(client, trust_store, state_repo, events)

    fake_monotonic = [2000.0]
    monkeypatch.setattr(time_module, "monotonic", lambda: fake_monotonic[0])
    scheduler.run_once()  # pins the anchor

    fake_monotonic[0] = 1500.0  # backward
    result = scheduler.run_once()  # must not raise

    assert result == LicenseState.CLOCK_REVIEW_REQUIRED
