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


def _evidence(
    policy=STANDARD_POLICY, license_status="ACTIVE", installation_status="ACTIVE",
    subscription_status="ACTIVE", commercial_grace_end=None,
):
    return AssertionEvidence(
        not_before=NOW - timedelta(days=1),
        expires_at=NOW + timedelta(days=30),
        license_status=license_status,
        installation_status=installation_status,
        subscription_status=subscription_status,
        offline_policy=policy,
        commercial_grace_end=commercial_grace_end,
    )


def _extension_policy(until, allowed=True):
    return OfflinePolicy(
        check_in_interval_seconds=STANDARD_POLICY.check_in_interval_seconds,
        retry_interval_seconds=STANDARD_POLICY.retry_interval_seconds,
        offline_grace_seconds=STANDARD_POLICY.offline_grace_seconds,
        warning_start_seconds=STANDARD_POLICY.warning_start_seconds,
        hard_expiry_behavior=STANDARD_POLICY.hard_expiry_behavior,
        clock_rollback_tolerance_seconds=STANDARD_POLICY.clock_rollback_tolerance_seconds,
        assertion_refresh_threshold_seconds=STANDARD_POLICY.assertion_refresh_threshold_seconds,
        emergency_extension_allowed=allowed,
        emergency_extension_until=until,
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


# -- Phase 8V-P6: subscription_status / license SUSPENDED / commercial grace / emergency extension --

def test_license_suspended_now_restricts_even_with_fresh_checkin():
    # Phase 8V-P5's real finding: license_status == SUSPENDED was never
    # checked here at all, so a real sale completed while genuinely
    # SUSPENDED in Owner's database. Regression for the fix.
    anchor = new_anchor(NOW)
    result = evaluate(
        evidence=_evidence(license_status="SUSPENDED"),
        anchor=anchor,
        local_wall_clock_now=NOW,
        last_successful_checkin_at=NOW,
        last_checkin_attempt_ok=True,
    )
    assert result == LicenseState.SUSPENDED


def test_license_suspended_not_overridden_by_emergency_extension():
    # Non-Negotiable Rule 8: an emergency extension must never mask an
    # explicit security-driven suspension.
    anchor = new_anchor(NOW)
    ext_policy = _extension_policy(NOW + timedelta(days=1))
    result = evaluate(
        evidence=_evidence(policy=ext_policy, license_status="SUSPENDED"),
        anchor=anchor,
        local_wall_clock_now=NOW,
        last_successful_checkin_at=NOW,
        last_checkin_attempt_ok=True,
    )
    assert result == LicenseState.SUSPENDED


def test_subscription_expired_restricts_even_though_license_stays_active():
    # The core Phase 8V-P6 gap: license_status can legitimately stay ACTIVE
    # (history preserved) while Subscription.status goes EXPIRED. This must
    # now restrict locally, independent of hard_expiry_behavior.
    anchor = new_anchor(NOW)
    warn_only_policy = OfflinePolicy(
        check_in_interval_seconds=86400, retry_interval_seconds=3600, offline_grace_seconds=14 * 86400,
        warning_start_seconds=10 * 86400, hard_expiry_behavior="WARN_ONLY",
        clock_rollback_tolerance_seconds=300, assertion_refresh_threshold_seconds=86400,
    )
    result = evaluate(
        evidence=_evidence(policy=warn_only_policy, subscription_status="EXPIRED"),
        anchor=anchor,
        local_wall_clock_now=NOW,
        last_successful_checkin_at=NOW,
        last_checkin_attempt_ok=True,
    )
    assert result == LicenseState.RESTRICTED


def test_subscription_cancelled_restricts():
    anchor = new_anchor(NOW)
    result = evaluate(
        evidence=_evidence(subscription_status="CANCELLED"),
        anchor=anchor,
        local_wall_clock_now=NOW,
        last_successful_checkin_at=NOW,
        last_checkin_attempt_ok=True,
    )
    assert result == LicenseState.RESTRICTED


def test_subscription_suspended_restricts():
    anchor = new_anchor(NOW)
    result = evaluate(
        evidence=_evidence(subscription_status="SUSPENDED"),
        anchor=anchor,
        local_wall_clock_now=NOW,
        last_successful_checkin_at=NOW,
        last_checkin_attempt_ok=True,
    )
    assert result == LicenseState.RESTRICTED


def test_subscription_past_due_within_signed_grace_stays_operational():
    # Part C: "not immediately treated as cryptographic failure... may
    # remain operational until signed commercial grace expires."
    anchor = new_anchor(NOW)
    result = evaluate(
        evidence=_evidence(subscription_status="PAST_DUE", commercial_grace_end=NOW + timedelta(days=5)),
        anchor=anchor,
        local_wall_clock_now=NOW,
        last_successful_checkin_at=NOW,
        last_checkin_attempt_ok=True,
    )
    assert result == LicenseState.ACTIVE_ONLINE


def test_subscription_past_due_after_signed_grace_restricts():
    anchor = new_anchor(NOW)
    result = evaluate(
        evidence=_evidence(subscription_status="PAST_DUE", commercial_grace_end=NOW - timedelta(hours=1)),
        anchor=anchor,
        local_wall_clock_now=NOW,
        last_successful_checkin_at=NOW,
        last_checkin_attempt_ok=True,
    )
    assert result == LicenseState.RESTRICTED


def test_subscription_past_due_with_no_commercial_grace_end_does_not_restrict():
    # commercial_grace_end absent (subscription never actually reached
    # PAST_DUE server-side, or an older payload) -- must never crash and
    # must never restrict purely on subscription_status alone without a
    # signed grace boundary to compare against.
    anchor = new_anchor(NOW)
    result = evaluate(
        evidence=_evidence(subscription_status="PAST_DUE", commercial_grace_end=None),
        anchor=anchor,
        local_wall_clock_now=NOW,
        last_successful_checkin_at=NOW,
        last_checkin_attempt_ok=True,
    )
    assert result == LicenseState.ACTIVE_ONLINE


def test_emergency_extension_overrides_subscription_expired():
    anchor = new_anchor(NOW)
    ext_policy = _extension_policy(NOW + timedelta(hours=2))
    result = evaluate(
        evidence=_evidence(policy=ext_policy, subscription_status="EXPIRED"),
        anchor=anchor,
        local_wall_clock_now=NOW,
        last_successful_checkin_at=NOW,
        last_checkin_attempt_ok=True,
    )
    assert result == LicenseState.ACTIVE_ONLINE


def test_emergency_extension_stops_applying_after_its_own_expiry():
    # Same subscription-EXPIRED case, but "now" has passed the extension's
    # own signed until -- must revert to RESTRICTED, no manual action needed.
    anchor = new_anchor(NOW)
    ext_policy = _extension_policy(NOW - timedelta(minutes=1))
    result = evaluate(
        evidence=_evidence(policy=ext_policy, subscription_status="EXPIRED"),
        anchor=anchor,
        local_wall_clock_now=NOW,
        last_successful_checkin_at=NOW,
        last_checkin_attempt_ok=True,
    )
    assert result == LicenseState.RESTRICTED


def test_subscription_active_unaffected_by_new_commercial_checks():
    # Baseline: normal ACTIVE subscription must behave exactly as before.
    anchor = new_anchor(NOW)
    result = evaluate(
        evidence=_evidence(subscription_status="ACTIVE"),
        anchor=anchor,
        local_wall_clock_now=NOW,
        last_successful_checkin_at=NOW - timedelta(hours=1),
        last_checkin_attempt_ok=True,
    )
    assert result == LicenseState.ACTIVE_ONLINE
