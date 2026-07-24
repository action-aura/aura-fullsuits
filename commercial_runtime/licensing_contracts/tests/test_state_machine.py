import pytest

from commercial_runtime.licensing_contracts.state_machine import (
    ACTIVE_FAMILY,
    DATA_PRESERVED_FAMILY,
    InvalidTransitionError,
    LicenseState,
    transition,
)


def test_full_happy_path_activation():
    s = LicenseState.NOT_CONFIGURED
    s = transition(s, "device_key_generated")
    assert s == LicenseState.ACTIVATION_REQUIRED
    s = transition(s, "activation_submitted")
    assert s == LicenseState.ACTIVATING
    s = transition(s, "owner_approved")
    assert s == LicenseState.ACTIVE_ONLINE


def test_activation_rejection_returns_to_activation_required():
    s = transition(LicenseState.ACTIVATING, "owner_rejected")
    assert s == LicenseState.ACTIVATION_REQUIRED


def test_offline_grace_progression():
    s = LicenseState.ACTIVE_ONLINE
    s = transition(s, "checkin_failed_network")
    assert s == LicenseState.ACTIVE_OFFLINE
    s = transition(s, "warning_threshold_crossed")
    assert s == LicenseState.WARNING
    s = transition(s, "grace_threshold_crossed")
    assert s == LicenseState.GRACE_PERIOD
    s = transition(s, "grace_exhausted")
    assert s == LicenseState.RESTRICTED
    s = transition(s, "checkin_succeeded")
    assert s == LicenseState.ACTIVE_ONLINE


@pytest.mark.parametrize(
    "start",
    [
        LicenseState.NOT_CONFIGURED,
        LicenseState.ACTIVATION_REQUIRED,
        LicenseState.ACTIVE_ONLINE,
        LicenseState.ACTIVE_OFFLINE,
        LicenseState.WARNING,
        LicenseState.GRACE_PERIOD,
        LicenseState.RESTRICTED,
        LicenseState.SUSPENDED,
        LicenseState.CLOCK_REVIEW_REQUIRED,
    ],
)
def test_global_events_reachable_from_any_state(start):
    assert transition(start, "owner_suspended") == LicenseState.SUSPENDED
    assert transition(start, "owner_revoked") == LicenseState.REVOKED
    assert transition(start, "owner_expired") == LicenseState.EXPIRED
    assert transition(start, "clock_rollback_suspected") == LicenseState.CLOCK_REVIEW_REQUIRED
    assert transition(start, "local_state_corruption_detected") == LicenseState.LOCAL_STATE_CORRUPT


def test_invalid_transition_raises_not_silently_ignored():
    with pytest.raises(InvalidTransitionError):
        transition(LicenseState.NOT_CONFIGURED, "checkin_succeeded")


def test_revoked_has_no_outgoing_local_transition_only_reactivation_via_new_flow():
    # REVOKED is terminal-until-reactivation: no event drives it anywhere
    # except starting a brand new activation, which begins from
    # NOT_CONFIGURED/ACTIVATION_REQUIRED, not from REVOKED itself.
    with pytest.raises(InvalidTransitionError):
        transition(LicenseState.REVOKED, "activation_submitted")


def test_device_replacement_flow():
    s = transition(LicenseState.DEVICE_DEACTIVATED, "replacement_completed")
    assert s == LicenseState.DEVICE_REPLACED
    s = transition(s, "device_key_generated")
    assert s == LicenseState.ACTIVATION_REQUIRED


def test_same_device_reactivation_after_deactivation():
    s = transition(LicenseState.DEVICE_DEACTIVATED, "reactivation_requested")
    assert s == LicenseState.ACTIVATION_REQUIRED


def test_local_state_corrupt_reset_flow():
    s = transition(LicenseState.LOCAL_STATE_CORRUPT, "reset_completed")
    assert s == LicenseState.ACTIVATION_REQUIRED


def test_active_family_membership():
    for s in (LicenseState.ACTIVE_ONLINE, LicenseState.ACTIVE_OFFLINE, LicenseState.WARNING, LicenseState.GRACE_PERIOD):
        assert s in ACTIVE_FAMILY
    assert LicenseState.RESTRICTED not in ACTIVE_FAMILY


def test_data_preserved_family_includes_restricted_and_terminal_states():
    for s in (
        LicenseState.RESTRICTED,
        LicenseState.SUSPENDED,
        LicenseState.REVOKED,
        LicenseState.EXPIRED,
        LicenseState.LOCAL_STATE_CORRUPT,
    ):
        assert s in DATA_PRESERVED_FAMILY


def test_data_preserved_family_includes_pre_activation_states():
    # Part Y: an rc.1-upgraded install with no licensing state yet must
    # still allow viewing/backing up/exporting existing data before
    # activation -- these states are not a "no data access" zone.
    for s in (LicenseState.NOT_CONFIGURED, LicenseState.ACTIVATION_REQUIRED, LicenseState.ACTIVATING):
        assert s in DATA_PRESERVED_FAMILY
