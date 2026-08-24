import pytest

from commercial_runtime.licensing_contracts.client import LicensingClientError
from commercial_runtime.licensing_contracts.deactivation import DeactivationFailed, perform_deactivation
from commercial_runtime.licensing_contracts.events import LicensingEventRecorder
from commercial_runtime.licensing_contracts.state_machine import LicenseState
from commercial_runtime.licensing_contracts.state_repository import (
    LICENSING_SCHEMA_VERSION,
    LicenseStateRecord,
    LicenseStateRepository,
)


class FakeSigner:
    def sign(self, canonical_bytes: bytes) -> str:
        return "sig"

    def get_public_key_b64(self) -> str:
        return "pub"


class FakeClient:
    def __init__(self, response=None, error=None):
        self._response = response
        self._error = error
        self.deactivate_calls = []

    def deactivate(self, **kwargs):
        self.deactivate_calls.append(kwargs)
        if self._error:
            raise self._error
        return self._response


@pytest.fixture
def state_repo(tmp_path):
    return LicenseStateRepository(tmp_path / "database" / "subsystems" / "licensing.db")


@pytest.fixture
def events(tmp_path):
    return LicensingEventRecorder(tmp_path / "database" / "subsystems" / "licensing.db")


def _seed(state_repo, **overrides):
    defaults = dict(
        licensing_schema_version=LICENSING_SCHEMA_VERSION,
        product_code="AURA_RETAIL",
        platform="WINDOWS",
        current_state="ACTIVE_ONLINE",
        owner_installation_id="inst-1",
    )
    defaults.update(overrides)
    record = LicenseStateRecord(**defaults)
    state_repo.save(record)
    return record


def test_deactivation_before_activation_is_a_noop(state_repo, events):
    client = FakeClient()
    result = perform_deactivation(client=client, signer=FakeSigner(), state_repository=state_repo, event_recorder=events)
    assert result == LicenseState.NOT_CONFIGURED
    assert len(client.deactivate_calls) == 0


def test_successful_deactivation_transitions_state_and_records_event(state_repo, events):
    _seed(state_repo)
    client = FakeClient(response={"result": "SUCCESS", "reason_code": "DEACTIVATION_ACCEPTED"})

    result = perform_deactivation(client=client, signer=FakeSigner(), state_repository=state_repo, event_recorder=events)

    assert result == LicenseState.DEVICE_DEACTIVATED
    assert state_repo.load().current_state == "DEVICE_DEACTIVATED"
    assert "DEVICE_DEACTIVATED" in [e.event_type for e in events.recent()]


def test_deactivation_is_idempotent_when_called_twice(state_repo, events):
    _seed(state_repo)
    client = FakeClient(response={"result": "SUCCESS", "reason_code": "DEACTIVATION_ACCEPTED"})

    perform_deactivation(client=client, signer=FakeSigner(), state_repository=state_repo, event_recorder=events)
    result = perform_deactivation(client=client, signer=FakeSigner(), state_repository=state_repo, event_recorder=events)

    assert result == LicenseState.DEVICE_DEACTIVATED
    assert len(client.deactivate_calls) == 2  # both calls succeed safely


def test_network_failure_raises_and_leaves_state_unchanged(state_repo, events):
    _seed(state_repo)
    client = FakeClient(error=LicensingClientError("NETWORK_UNAVAILABLE", "down"))

    with pytest.raises(DeactivationFailed):
        perform_deactivation(client=client, signer=FakeSigner(), state_repository=state_repo, event_recorder=events)

    assert state_repo.load().current_state == "ACTIVE_ONLINE"  # unchanged, not silently marked deactivated


def test_owner_rejection_raises_with_reason_code(state_repo, events):
    _seed(state_repo)
    client = FakeClient(response={"result": "REJECTED", "reason_code": "ACTIVATION_REJECTED"})

    with pytest.raises(DeactivationFailed) as exc:
        perform_deactivation(client=client, signer=FakeSigner(), state_repository=state_repo, event_recorder=events)
    assert exc.value.reason_code == "ACTIVATION_REJECTED"


# ── Android path: ingest_deactivation_response() (Part U) ─────────────────

from commercial_runtime.licensing_contracts.deactivation import ingest_deactivation_response


def test_ingest_deactivation_response_transitions_state(state_repo, events):
    _seed(state_repo)
    result = ingest_deactivation_response(
        {"result": "SUCCESS", "reason_code": "DEACTIVATION_ACCEPTED"},
        state_repository=state_repo,
        event_recorder=events,
    )
    assert result == LicenseState.DEVICE_DEACTIVATED
    assert state_repo.load().current_state == "DEVICE_DEACTIVATED"


def test_ingest_deactivation_response_rejection_raises(state_repo, events):
    _seed(state_repo)
    with pytest.raises(DeactivationFailed):
        ingest_deactivation_response(
            {"result": "REJECTED", "reason_code": "ACTIVATION_REJECTED"},
            state_repository=state_repo,
            event_recorder=events,
        )
    assert state_repo.load().current_state == "ACTIVE_ONLINE"


# ── AUDIT: unguarded LicenseState(record.current_state) (Part L twin of ──
# ── flask_guard.py's fix / checkin_scheduler.py's identical twin) ────────


def test_deactivation_noop_with_corrupt_state_and_no_installation_id(state_repo, events):
    """Proof 3: a record can exist (so it's not a fresh install) with BOTH
    a corrupt current_state AND no owner_installation_id yet (e.g. a
    partial write during activation that never completed). The pre-fix
    code unconditionally called LicenseState(record.current_state) on the
    way out of this genuine no-op branch and crashed an explicit,
    user-initiated deactivation request instead of just no-op'ing."""
    state_repo.save(
        LicenseStateRecord(
            licensing_schema_version=LICENSING_SCHEMA_VERSION,
            product_code="AURA_RETAIL",
            platform="WINDOWS",
            current_state="GARBAGE_NOT_A_REAL_STATE",
            owner_installation_id=None,
        )
    )
    client = FakeClient()

    result = perform_deactivation(client=client, signer=FakeSigner(), state_repository=state_repo, event_recorder=events)

    assert result == LicenseState.LOCAL_STATE_CORRUPT
    assert len(client.deactivate_calls) == 0
    assert "LOCAL_STATE_CORRUPT" in [e.event_type for e in events.recent()]


def test_deactivation_with_corrupt_state_but_real_installation_id_still_proceeds(state_repo, events):
    """Corruption of the current_state text field alone must not block an
    explicit, user-initiated deactivation of an install that DOES still
    have a real owner_installation_id -- deactivation is a controlled
    recovery-adjacent action (Part X), not a read-only one, and refusing it
    here would leave an owner with a corrupt row and no way to release the
    device seat. The fix only has to stop the unguarded LicenseState(...)
    construction from crashing on the way out of the no-op branch -- it
    does not (and should not) block a deactivation that has a real
    installation id to act on."""
    state_repo.save(
        LicenseStateRecord(
            licensing_schema_version=LICENSING_SCHEMA_VERSION,
            product_code="AURA_RETAIL",
            platform="WINDOWS",
            current_state="GARBAGE_NOT_A_REAL_STATE",
            owner_installation_id="inst-1",
        )
    )
    client = FakeClient(response={"result": "SUCCESS", "reason_code": "DEACTIVATION_ACCEPTED"})

    result = perform_deactivation(client=client, signer=FakeSigner(), state_repository=state_repo, event_recorder=events)

    assert result == LicenseState.DEVICE_DEACTIVATED
    assert len(client.deactivate_calls) == 1


def test_ingest_deactivation_response_noop_with_corrupt_state_and_no_installation_id(state_repo, events):
    """Same guard, Android entry point (Part U)."""
    state_repo.save(
        LicenseStateRecord(
            licensing_schema_version=LICENSING_SCHEMA_VERSION,
            product_code="AURA_RETAIL",
            platform="WINDOWS",
            current_state="",
            owner_installation_id=None,
        )
    )

    result = ingest_deactivation_response(
        {"result": "SUCCESS", "reason_code": "DEACTIVATION_ACCEPTED"},
        state_repository=state_repo,
        event_recorder=events,
    )

    assert result == LicenseState.LOCAL_STATE_CORRUPT


def test_deactivation_missing_record_resolves_not_configured(state_repo, events):
    """Proof 5: a MISSING record must still resolve NOT_CONFIGURED, never
    LOCAL_STATE_CORRUPT -- a fresh install is not a damaged one. (Already
    covered functionally by test_deactivation_before_activation_is_a_noop
    above; this asserts the same guarantee explicitly against the new
    _resolve_stored_state() helper's own contract.)"""
    client = FakeClient()

    result = perform_deactivation(client=client, signer=FakeSigner(), state_repository=state_repo, event_recorder=events)

    assert result == LicenseState.NOT_CONFIGURED
    event_types = [e.event_type for e in events.recent()]
    assert "LOCAL_STATE_CORRUPT" not in event_types


def test_deactivation_corrupt_state_containing_forbidden_marker_does_not_raise_from_recorder(state_repo, events):
    """Proof 4: events.record() RAISES LicensingEventError when details
    contain a forbidden-marker substring (license_key, patient, sale_total,
    ...). A corrupt current_state that happens to itself contain one of
    those substrings must not re-raise from inside the handler written to
    prevent exactly that."""
    state_repo.save(
        LicenseStateRecord(
            licensing_schema_version=LICENSING_SCHEMA_VERSION,
            product_code="AURA_RETAIL",
            platform="WINDOWS",
            current_state="patient_card_number_leaked_into_this_column",
            owner_installation_id=None,
        )
    )
    client = FakeClient()

    # must not raise LicensingEventError
    result = perform_deactivation(client=client, signer=FakeSigner(), state_repository=state_repo, event_recorder=events)

    assert result == LicenseState.LOCAL_STATE_CORRUPT
    assert "LOCAL_STATE_CORRUPT" in [e.event_type for e in events.recent()]
