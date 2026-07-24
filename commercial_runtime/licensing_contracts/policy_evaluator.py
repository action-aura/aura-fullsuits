"""LicensePolicyEvaluator (Part O) -- local evaluation of Owner's signed
offline policy against trusted elapsed time.

See docs/licensing/phase7/offline-enforcement-policy.md for the full design.
This module computes *what state the installation should be in right now*
given the last verified assertion and trusted time; it does not decide
whether to attempt a check-in (that's LicenseCheckInScheduler) and it does
not enforce anything (that's LicenseCapabilityGuard) -- pure classification
only, deterministic and independently testable without any network or
filesystem dependency.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

from .state_machine import LicenseState
from .trusted_time import TrustedTimeAnchor, detect_rollback, trusted_now as _trusted_now


class PolicyEvaluationError(ValueError):
    pass


@dataclass(frozen=True)
class OfflinePolicy:
    """Mirrors owner_offline_policies exactly (Phase 6). Read only from the
    currently-verified assertion's embedded offline_policy object -- see
    offline-enforcement-policy.md's "Hard limits" section for why no other
    source is ever consulted."""

    check_in_interval_seconds: int
    retry_interval_seconds: int
    offline_grace_seconds: int
    warning_start_seconds: int
    hard_expiry_behavior: str  # "WARN_ONLY" | "RESTRICT_COMMERCIAL_FEATURES_PRESERVE_DATA"
    clock_rollback_tolerance_seconds: int
    assertion_refresh_threshold_seconds: int
    emergency_extension_allowed: bool = False
    emergency_extension_until: Optional[datetime] = None

    def __post_init__(self):
        if self.hard_expiry_behavior not in ("WARN_ONLY", "RESTRICT_COMMERCIAL_FEATURES_PRESERVE_DATA"):
            raise PolicyEvaluationError(
                f"Unknown hard_expiry_behavior {self.hard_expiry_behavior!r} -- refusing to guess a safe default."
            )


@dataclass(frozen=True)
class AssertionEvidence:
    """The subset of a verified assertion's payload this evaluator needs.
    Built by AssertionVerifier only after full cryptographic and structural
    verification has already passed -- this evaluator trusts every field
    here unconditionally, because trust was already established upstream."""

    not_before: datetime
    expires_at: datetime
    license_status: str
    installation_status: str
    subscription_status: str
    offline_policy: OfflinePolicy


def evaluate(
    *,
    evidence: AssertionEvidence,
    anchor: TrustedTimeAnchor,
    local_wall_clock_now: datetime,
    last_successful_checkin_at: datetime,
    last_checkin_attempt_ok: bool,
    local_safety_ceiling_seconds: Optional[int] = None,
) -> LicenseState:
    """Implements offline-enforcement-policy.md's evaluation, in order.

    local_safety_ceiling_seconds, if given, is min()'d against the signed
    offline_grace_seconds -- it can only shorten the effective grace window,
    per Part O's "a local release may apply a stricter safety limit, but not
    silently extend" rule; it is never allowed to lengthen it.
    """
    policy = evidence.offline_policy

    # Clock-rollback short-circuits every other rule (Part N).
    if detect_rollback(anchor, local_wall_clock_now, policy.clock_rollback_tolerance_seconds):
        return LicenseState.CLOCK_REVIEW_REQUIRED

    now = _trusted_now(anchor)

    # Explicit signed decisions override every timer-based computation.
    if evidence.installation_status == "SUSPENDED":
        return LicenseState.SUSPENDED
    if evidence.installation_status == "REVOKED":
        return LicenseState.REVOKED
    if evidence.license_status in ("EXPIRED", "REVOKED"):
        return LicenseState.EXPIRED

    if now < evidence.not_before or now > evidence.expires_at:
        # Assertion is not currently valid by its own signed dates -- fall
        # through to grace/restriction evaluation exactly as if offline,
        # since an expired assertion and "we haven't checked in" are the
        # same practical situation from the local product's point of view.
        pass

    effective_grace_seconds = policy.offline_grace_seconds
    if local_safety_ceiling_seconds is not None:
        effective_grace_seconds = min(effective_grace_seconds, local_safety_ceiling_seconds)

    if policy.emergency_extension_allowed and policy.emergency_extension_until is not None:
        if now < policy.emergency_extension_until:
            extension_seconds = int((policy.emergency_extension_until - now).total_seconds())
            effective_grace_seconds = max(effective_grace_seconds, effective_grace_seconds + extension_seconds)

    elapsed_offline = (now - last_successful_checkin_at).total_seconds()

    if last_checkin_attempt_ok and elapsed_offline < policy.check_in_interval_seconds:
        return LicenseState.ACTIVE_ONLINE

    if elapsed_offline < effective_grace_seconds:
        warning_boundary = effective_grace_seconds - policy.warning_start_seconds
        if elapsed_offline >= warning_boundary:
            return LicenseState.WARNING
        return LicenseState.ACTIVE_OFFLINE if not last_checkin_attempt_ok else LicenseState.ACTIVE_ONLINE

    if elapsed_offline < effective_grace_seconds + policy.retry_interval_seconds:
        return LicenseState.GRACE_PERIOD

    # Grace fully exhausted.
    if policy.hard_expiry_behavior == "WARN_ONLY":
        # Every capability stays enabled; the presenter is responsible for
        # surfacing a persistent warning. Modeled as GRACE_PERIOD rather than
        # a separate state, since WARN_ONLY means "commercial mutation is
        # never blocked by time alone" -- RESTRICTED is reserved for when
        # hard_expiry_behavior actually says to restrict.
        return LicenseState.GRACE_PERIOD
    return LicenseState.RESTRICTED
