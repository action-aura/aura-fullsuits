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
