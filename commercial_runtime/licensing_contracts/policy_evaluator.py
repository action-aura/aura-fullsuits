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
from datetime import date, datetime, time, timedelta, timezone, tzinfo
from typing import Optional

from .state_machine import LicenseState
from .trusted_time import TrustedTimeAnchor, detect_rollback, trusted_now as _trusted_now


class PolicyEvaluationError(ValueError):
    pass


# The 7-day trial ends at the END OF THE term_end DAY IN JORDAN, not at a UTC
# midnight: a shop in Amman that signed up on the 1st is told "7 days" and
# expects to sell through the whole of the 8th. A UTC boundary would lock the
# till at 03:00 local on the 8th (Jordan is UTC+3 all year since the 2022 DST
# abolition), three hours into the shop's last trading day.
TRIAL_TIMEZONE_NAME = "Asia/Amman"
# Signed subscription_status values that are a trial. PILOT is a trial still
# running (until its term_end day is over). COMPLETED is the terminal state
# Owner puts a pilot in when it ended without converting (PILOT -> COMPLETED
# has no way out), so it is locked from the moment it is signed.
TRIAL_STATUSES = frozenset({"PILOT", "COMPLETED"})


def _trial_zone() -> tzinfo:
    """Asia/Amman via the OS/tzdata database, with a fixed UTC+3 fallback for
    installs that ship no tz database (Windows without the tzdata package,
    Chaquopy). Both agree on every date since October 2022, which is the only
    range a live trial can fall in; the fallback exists so a missing database
    can never turn the lock into an exception on the selling path."""
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(TRIAL_TIMEZONE_NAME)
    except Exception:  # noqa: BLE001 -- ZoneInfoNotFoundError / ImportError / OSError
        return timezone(timedelta(hours=3))


def trial_cutoff(term_end: date) -> datetime:
    """First instant AFTER the term_end day, in Amman: the moment the trial is
    over. 23:59 on term_end is still inside the trial; 00:00 the next day is
    not."""
    return datetime.combine(term_end + timedelta(days=1), time.min, tzinfo=_trial_zone())


def trial_days_left(term_end: date, as_of: datetime) -> int:
    """Whole days of trial remaining for display, counting the term_end day
    itself (term_end = today -> 1 day left, the last trading day). 0 once the
    trial is over. Display only -- the lock decision is evaluate()'s."""
    remaining = trial_cutoff(term_end) - as_of
    if remaining <= timedelta(0):
        return 0
    return -(-int(remaining.total_seconds()) // 86400)  # ceiling


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
    # Phase 8V-P6: signed by Owner (assertion_fields.py::resolve_commercial_assertion_fields())
    # whenever the subscription is PAST_DUE; None otherwise (never PAST_DUE yet, or a signed
    # payload from before this field was consumed client-side -- absence is always safe, never
    # a crash). Distinct from offline_policy's own technical grace fields (Part D).
    commercial_grace_end: Optional[datetime] = None
    # New-customer flow: the lease already signs term_end (a DATE, Owner's
    # Subscription.end_date) and pilot_status; the client allowlist already
    # accepted them but nothing consumed them. None = absent or unparseable --
    # absence can never lock a till (a paid shop's lease always carries one,
    # and an Owner-side defect in a date must not stop paying customers).
    term_end: Optional[date] = None
    pilot_status: Optional[str] = None


def _trial_has_ended(
    evidence: AssertionEvidence, trial_now: datetime
) -> bool:
    if evidence.subscription_status == "COMPLETED":
        return True
    if evidence.subscription_status != "PILOT" or evidence.term_end is None:
        return False
    return trial_now >= trial_cutoff(evidence.term_end)


def evaluate(
    *,
    evidence: AssertionEvidence,
    anchor: TrustedTimeAnchor,
    local_wall_clock_now: datetime,
    last_successful_checkin_at: datetime,
    last_checkin_attempt_ok: bool,
    local_safety_ceiling_seconds: Optional[int] = None,
    offline_elapsed_floor_seconds: Optional[float] = None,
) -> LicenseState:
    """Implements offline-enforcement-policy.md's evaluation, in order.

    local_safety_ceiling_seconds, if given, is min()'d against the signed
    offline_grace_seconds -- it can only shorten the effective grace window,
    per Part O's "a local release may apply a stricter safety limit, but not
    silently extend" rule; it is never allowed to lengthen it.

    offline_elapsed_floor_seconds, if given, is a lower bound on the offline
    time: elapsed_offline = max(trusted computation, floor). The caller
    supplies a wall-clock based figure; because it is only a FLOOR it can make
    a device reach grace/restriction sooner, never later, so a rolled-back
    wall clock leaves the trusted value in charge. None = unchanged behaviour.
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
    # Phase 8V-P6: license_status == "SUSPENDED" was never checked here --
    # the assertion already carried it correctly, this evaluator just never
    # read it (found via real physical validation: a real sale completed
    # while the license was genuinely SUSPENDED in Owner's database). A
    # security-driven license suspension is treated like REVOKED for
    # override purposes: an emergency extension (a payment-timing relief
    # mechanism, Part E) must never mask it (Non-Negotiable Rule 8).
    if evidence.license_status == "SUSPENDED":
        return LicenseState.SUSPENDED

    # New-customer flow (owner 2026-10-30): the 7-day trial lock. Placed HERE
    # on purpose -- after the explicit SUSPENDED/REVOKED decisions (those win)
    # and BEFORE both the commercial block and the emergency extension: an
    # extension is payment-timing relief for a PAID subscription and must not
    # buy a free trial more days, and the commercial block below would
    # otherwise fall through to keep-selling for PILOT/COMPLETED.
    #
    # The clock is TRUSTED time, never the raw wall clock. When the caller
    # supplies the offline floor (a stale assertion, re-verified at its own
    # expires_at) the trial clock is also pushed forward by it, because after
    # a restart the anchor re-pins to the last check-in and would otherwise
    # report "no time has passed" however long the till sat offline. The floor
    # only ever moves the trial clock LATER (max), so rolling the wall clock
    # back cannot extend a trial; a roll-back past the anchor is already
    # CLOCK_REVIEW_REQUIRED above.
    trial_now = now
    if offline_elapsed_floor_seconds is not None:
        trial_now = max(
            now, last_successful_checkin_at + timedelta(seconds=offline_elapsed_floor_seconds)
        )
    if _trial_has_ended(evidence, trial_now):
        return LicenseState.TRIAL_ENDED

    # Phase 8V-P6 (Part C/D): commercial state -- driven by Subscription.status
    # and the signed commercial_grace_end, deliberately independent of
    # hard_expiry_behavior/elapsed offline time. subscription_status was
    # already signed and parsed into AssertionEvidence but never consulted
    # here; this closes that gap without weakening the technical offline
    # logic below, which still runs unchanged for a commercially ACTIVE/PILOT
    # subscription or one whose PAST_DUE grace hasn't yet elapsed.
    emergency_extension_effective = (
        policy.emergency_extension_allowed
        and policy.emergency_extension_until is not None
        and now < policy.emergency_extension_until
    )
    if not emergency_extension_effective:
        # Owner decision 2026-10-24 (Aura Growth Plan): a shop whose
        # subscription lapses UNPAID keeps selling. Only an EXPIRED
        # subscription, or PAST_DUE past its signed commercial grace, is an
        # unpaid lapse -> SUBSCRIPTION_LAPSED (sells; multi-device sync, online
        # extras and updates pause). Before this, both returned RESTRICTED and
        # a till that had not paid stopped taking money.
        # CANCELLED (deliberate end) and SUSPENDED (a full refund in Owner)
        # are NOT unpaid lapses and the owner has not said they keep selling,
        # so they deliberately stay RESTRICTED until that is decided.
        if evidence.subscription_status == "EXPIRED":
            return LicenseState.SUBSCRIPTION_LAPSED
        if evidence.subscription_status == "CANCELLED":
            return LicenseState.RESTRICTED
        if evidence.subscription_status == "SUSPENDED":
            return LicenseState.RESTRICTED
        if (
            evidence.subscription_status == "PAST_DUE"
            and evidence.commercial_grace_end is not None
            and now > evidence.commercial_grace_end
        ):
            return LicenseState.SUBSCRIPTION_LAPSED

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
    if offline_elapsed_floor_seconds is not None:
        elapsed_offline = max(elapsed_offline, offline_elapsed_floor_seconds)

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
