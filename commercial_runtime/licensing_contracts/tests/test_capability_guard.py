import pytest

from commercial_runtime.licensing_contracts.capability_guard import (
    DENIAL_REASON_LICENSE_INACTIVE,
    DENIAL_REASON_NOT_ENTITLED,
    evaluate_capability,
)
from commercial_runtime.licensing_contracts.state_machine import LicenseState

READ_ONLY_ALLOWLIST = frozenset({"clinic.records.read", "clinic.backup.create", "clinic.backup.restore", "clinic.data.export"})


def test_active_online_allows_mutation_capability_not_in_allowlist():
    decision = evaluate_capability(
        capability_code="clinic.patient.create",
        current_state=LicenseState.ACTIVE_ONLINE,
        restricted_mode_allowlist=READ_ONLY_ALLOWLIST,
    )
    assert decision.allowed is True


def test_restricted_denies_mutation_capability():
    decision = evaluate_capability(
        capability_code="clinic.patient.create",
        current_state=LicenseState.RESTRICTED,
        restricted_mode_allowlist=READ_ONLY_ALLOWLIST,
    )
    assert decision.allowed is False
    assert decision.denial_reason == DENIAL_REASON_LICENSE_INACTIVE


def test_restricted_allows_allowlisted_read_capability():
    decision = evaluate_capability(
        capability_code="clinic.records.read",
        current_state=LicenseState.RESTRICTED,
        restricted_mode_allowlist=READ_ONLY_ALLOWLIST,
    )
    assert decision.allowed is True


def test_suspended_allows_backup():
    decision = evaluate_capability(
        capability_code="clinic.backup.create",
        current_state=LicenseState.SUSPENDED,
        restricted_mode_allowlist=READ_ONLY_ALLOWLIST,
    )
    assert decision.allowed is True


def test_suspended_denies_new_invoice():
    decision = evaluate_capability(
        capability_code="clinic.invoice.create",
        current_state=LicenseState.SUSPENDED,
        restricted_mode_allowlist=READ_ONLY_ALLOWLIST,
    )
    assert decision.allowed is False


def test_revoked_still_allows_export():
    decision = evaluate_capability(
        capability_code="clinic.data.export",
        current_state=LicenseState.REVOKED,
        restricted_mode_allowlist=READ_ONLY_ALLOWLIST,
    )
    assert decision.allowed is True


def test_local_state_corrupt_still_allows_backup_restore():
    decision = evaluate_capability(
        capability_code="clinic.backup.restore",
        current_state=LicenseState.LOCAL_STATE_CORRUPT,
        restricted_mode_allowlist=READ_ONLY_ALLOWLIST,
    )
    assert decision.allowed is True


def test_pre_activation_states_allow_read_and_backup():
    # Part Y: rc.1-upgraded, not-yet-activated install must still allow
    # viewing/backing up/exporting existing data.
    for state in (LicenseState.NOT_CONFIGURED, LicenseState.ACTIVATION_REQUIRED, LicenseState.ACTIVATING):
        decision = evaluate_capability(
            capability_code="clinic.records.read",
            current_state=state,
            restricted_mode_allowlist=READ_ONLY_ALLOWLIST,
        )
        assert decision.allowed is True, f"expected read allowed in {state}"


def test_pre_activation_states_deny_new_mutation():
    for state in (LicenseState.NOT_CONFIGURED, LicenseState.ACTIVATION_REQUIRED, LicenseState.ACTIVATING):
        decision = evaluate_capability(
            capability_code="clinic.patient.create",
            current_state=state,
            restricted_mode_allowlist=READ_ONLY_ALLOWLIST,
        )
        assert decision.allowed is False


def test_entitlement_gate_denies_when_missing():
    decision = evaluate_capability(
        capability_code="retail.settings.update",
        current_state=LicenseState.ACTIVE_ONLINE,
        restricted_mode_allowlist=READ_ONLY_ALLOWLIST,
        required_entitlement="digital_receipts_enabled",
        entitlements={},
    )
    assert decision.allowed is False
    assert decision.denial_reason == DENIAL_REASON_NOT_ENTITLED


def test_entitlement_gate_denies_when_false():
    decision = evaluate_capability(
        capability_code="retail.settings.update",
        current_state=LicenseState.ACTIVE_ONLINE,
        restricted_mode_allowlist=READ_ONLY_ALLOWLIST,
        required_entitlement="digital_receipts_enabled",
        entitlements={"digital_receipts_enabled": False},
    )
    assert decision.allowed is False


def test_entitlement_gate_allows_when_true():
    decision = evaluate_capability(
        capability_code="retail.settings.update",
        current_state=LicenseState.ACTIVE_ONLINE,
        restricted_mode_allowlist=READ_ONLY_ALLOWLIST,
        required_entitlement="digital_receipts_enabled",
        entitlements={"digital_receipts_enabled": True},
    )
    assert decision.allowed is True


def test_entitlement_gate_not_checked_when_restricted_already_denies():
    # State gate short-circuits before the entitlement gate is even
    # consulted -- LICENSE_INACTIVE wins, not CAPABILITY_NOT_ENTITLED, when
    # both would technically apply.
    decision = evaluate_capability(
        capability_code="retail.settings.update",
        current_state=LicenseState.RESTRICTED,
        restricted_mode_allowlist=READ_ONLY_ALLOWLIST,
        required_entitlement="digital_receipts_enabled",
        entitlements={"digital_receipts_enabled": True},
    )
    assert decision.allowed is False
    assert decision.denial_reason == DENIAL_REASON_LICENSE_INACTIVE
