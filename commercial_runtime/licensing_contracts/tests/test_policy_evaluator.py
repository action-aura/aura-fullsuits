from datetime import datetime, timedelta, timezone

import pytest

from commercial_runtime.licensing_contracts.policy_evaluator import (
    AssertionEvidence,
    OfflinePolicy,
    PolicyEvaluationError,
    evaluate,
)
from commercial_runtime.licensing_contracts.state_machine import LicenseState
from commercial_runtime.licensing_contracts.trusted_time import new_anchor

NOW = datetime.now(timezone.utc)

STANDARD_POLICY = OfflinePolicy(
    check_in_interval_seconds=86400,
    retry_interval_seconds=3600,
    offline_grace_seconds=14 * 86400,
    warning_start_seconds=10 * 86400,
    hard_expiry_behavior="RESTRICT_COMMERCIAL_FEATURES_PRESERVE_DATA",
    clock_rollback_tolerance_seconds=300,
    assertion_refresh_threshold_seconds=86400,
)


def _evidence(policy=STANDARD_POLICY, license_status="ACTIVE", installation_status="ACTIVE"):
    return AssertionEvidence(
        not_before=NOW - timedelta(days=1),
        expires_at=NOW + timedelta(days=30),
        license_status=license_status,
        installation_status=installation_status,
        subscription_status="ACTIVE",
        offline_policy=policy,
    )


def test_rejects_unknown_hard_expiry_behavior():
    with pytest.raises(PolicyEvaluationError):
        OfflinePolicy(
            check_in_interval_seconds=1,
            retry_interval_seconds=1,
            offline_grace_seconds=1,
            warning_start_seconds=1,
            hard_expiry_behavior="DELETE_EVERYTHING",
            clock_rollback_tolerance_seconds=1,
            assertion_refresh_threshold_seconds=1,
        )


def test_rule1_valid_assertion_recent_checkin_is_active_online():
    anchor = new_anchor(NOW)
    result = evaluate(
        evidence=_evidence(),
        anchor=anchor,
        local_wall_clock_now=NOW,
        last_successful_checkin_at=NOW - timedelta(hours=1),
        last_checkin_attempt_ok=True,
    )
    assert result == LicenseState.ACTIVE_ONLINE


def test_rule2_checkin_failed_but_within_interval_is_active_offline():
    anchor = new_anchor(NOW)
    result = evaluate(
        evidence=_evidence(),
        anchor=anchor,
        local_wall_clock_now=NOW,
        last_successful_checkin_at=NOW - timedelta(hours=2),
        last_checkin_attempt_ok=False,
    )
    assert result == LicenseState.ACTIVE_OFFLINE


def test_rule3_within_warning_window_is_warning():
    anchor = new_anchor(NOW)
    # grace=14d, warning_start=10d before grace expiry -> warning boundary at day 4.
    result = evaluate(
        evidence=_evidence(),
        anchor=anchor,
        local_wall_clock_now=NOW,
        last_successful_checkin_at=NOW - timedelta(days=5),
        last_checkin_attempt_ok=False,
    )
    assert result == LicenseState.WARNING


def test_rule5_grace_exhausted_restricts_when_policy_says_restrict():
    anchor = new_anchor(NOW)
    result = evaluate(
        evidence=_evidence(),
        anchor=anchor,
        local_wall_clock_now=NOW,
        last_successful_checkin_at=NOW - timedelta(days=20),
        last_checkin_attempt_ok=False,
    )
    assert result == LicenseState.RESTRICTED


def test_warn_only_never_restricts():
    warn_only_policy = OfflinePolicy(
        check_in_interval_seconds=86400,
        retry_interval_seconds=3600,
        offline_grace_seconds=14 * 86400,
        warning_start_seconds=10 * 86400,
        hard_expiry_behavior="WARN_ONLY",
        clock_rollback_tolerance_seconds=300,
        assertion_refresh_threshold_seconds=86400,
    )
    anchor = new_anchor(NOW)
    result = evaluate(
        evidence=_evidence(policy=warn_only_policy),
        anchor=anchor,
        local_wall_clock_now=NOW,
        last_successful_checkin_at=NOW - timedelta(days=999),
        last_checkin_attempt_ok=False,
    )
    assert result != LicenseState.RESTRICTED


def test_rule6_suspended_overrides_everything_including_fresh_checkin():
    anchor = new_anchor(NOW)
    result = evaluate(
        evidence=_evidence(installation_status="SUSPENDED"),
        anchor=anchor,
        local_wall_clock_now=NOW,
        last_successful_checkin_at=NOW,
        last_checkin_attempt_ok=True,
    )
    assert result == LicenseState.SUSPENDED


def test_rule7_revoked_overrides_everything():
    anchor = new_anchor(NOW)
    result = evaluate(
        evidence=_evidence(installation_status="REVOKED"),
        anchor=anchor,
        local_wall_clock_now=NOW,
        last_successful_checkin_at=NOW,
        last_checkin_attempt_ok=True,
    )
    assert result == LicenseState.REVOKED


def test_rule8_expired_license_status_overrides_fresh_checkin():
    anchor = new_anchor(NOW)
    result = evaluate(
        evidence=_evidence(license_status="EXPIRED"),
        anchor=anchor,
        local_wall_clock_now=NOW,
        last_successful_checkin_at=NOW,
        last_checkin_attempt_ok=True,
    )
    assert result == LicenseState.EXPIRED


def test_clock_rollback_short_circuits_before_any_other_rule():
    anchor = new_anchor(NOW)
    rolled_back_local_clock = NOW - timedelta(hours=5)
    result = evaluate(
        evidence=_evidence(installation_status="REVOKED"),  # would otherwise be REVOKED
        anchor=anchor,
        local_wall_clock_now=rolled_back_local_clock,
        last_successful_checkin_at=NOW,
        last_checkin_attempt_ok=True,
    )
    assert result == LicenseState.CLOCK_REVIEW_REQUIRED


def test_local_safety_ceiling_shortens_grace_never_lengthens():
    anchor = new_anchor(NOW)
    # Signed grace is 14 days; local ceiling of 1 day should force RESTRICTED
    # far earlier than the signed policy alone would.
    result = evaluate(
        evidence=_evidence(),
        anchor=anchor,
        local_wall_clock_now=NOW,
        last_successful_checkin_at=NOW - timedelta(days=2),
        last_checkin_attempt_ok=False,
        local_safety_ceiling_seconds=86400,
    )
    assert result == LicenseState.RESTRICTED


def test_local_safety_ceiling_cannot_extend_grace_beyond_signed_value():
    anchor = new_anchor(NOW)
    # A ceiling LARGER than the signed grace must not override the signed
    # (shorter) value -- min() semantics, not replacement.
    result = evaluate(
        evidence=_evidence(),
        anchor=anchor,
        local_wall_clock_now=NOW,
        last_successful_checkin_at=NOW - timedelta(days=20),
        last_checkin_attempt_ok=False,
        local_safety_ceiling_seconds=365 * 86400,
    )
    assert result == LicenseState.RESTRICTED
