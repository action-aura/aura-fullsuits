"""Local license state machine (Part M).

One table-driven evaluator, shared by every product. No route or UI
component may set state directly -- see
docs/licensing/phase7/local-license-state-machine.md for the full design and
the transition table this module implements. SERVICE_UNAVAILABLE is
deliberately not a member of LicenseState: it is a transient overlay the
presenter layer shows on top of whatever the last persisted state was, never
a stored state itself (see that document's explicit note).
"""
from __future__ import annotations

from enum import Enum


class LicenseState(str, Enum):
    NOT_CONFIGURED = "NOT_CONFIGURED"
    ACTIVATION_REQUIRED = "ACTIVATION_REQUIRED"
    ACTIVATING = "ACTIVATING"
    ACTIVE_ONLINE = "ACTIVE_ONLINE"
    ACTIVE_OFFLINE = "ACTIVE_OFFLINE"
    WARNING = "WARNING"
    GRACE_PERIOD = "GRACE_PERIOD"
    RESTRICTED = "RESTRICTED"
    SUSPENDED = "SUSPENDED"
    REVOKED = "REVOKED"
    EXPIRED = "EXPIRED"
    # Aura Growth Plan (owner decision 2026-10-24): a subscription that ran
    # out UNPAID (signed subscription_status EXPIRED, or PAST_DUE past its
    # signed commercial_grace_end). The shop KEEPS SELLING -- every local
    # commercial capability stays on -- and only loses multi-device sync,
    # online extras and feature updates until it renews. Deliberately NOT
    # RESTRICTED: RESTRICTED still means "commercial mutation is blocked"
    # (refund/cancel/offline hard-expiry), and folding the unpaid case into
    # it is exactly what used to stop an unpaid shop's till.
    SUBSCRIPTION_LAPSED = "SUBSCRIPTION_LAPSED"
    # New-customer flow (owner voice note 2026-10-30): the 7-day trial is a
    # signed PILOT subscription, and once its term_end day is over the till
    # LOCKS until the shop pays. Unlike SUBSCRIPTION_LAPSED (a PAID shop that
    # stopped paying -- keeps selling) a trial was never paid for, so selling
    # stops. The data is not held hostage: this state is in
    # DATA_PRESERVED_FAMILY, so read, reports, backup and export keep working
    # through the existing restricted allowlist. Deliberately NOT in
    # SELLING_FAMILY and NOT in SYNC_PAUSED_STATES: sync stays on so the
    # device sees the paid lease the moment it reconnects and unlocks itself.
    TRIAL_ENDED = "TRIAL_ENDED"
    DEVICE_DEACTIVATED = "DEVICE_DEACTIVATED"
    DEVICE_REPLACED = "DEVICE_REPLACED"
    CLOCK_REVIEW_REQUIRED = "CLOCK_REVIEW_REQUIRED"
    LOCAL_STATE_CORRUPT = "LOCAL_STATE_CORRUPT"


# States in which normal commercial operation is expected (used by the
# offline-policy evaluator and the presenter to decide what "currently
# licensed" means for elapsed-time bookkeeping purposes).
ACTIVE_FAMILY = frozenset(
    {
        LicenseState.ACTIVE_ONLINE,
        LicenseState.ACTIVE_OFFLINE,
        LicenseState.WARNING,
        LicenseState.GRACE_PERIOD,
    }
)

# States that preserve read/backup/export access even though commercial
# mutation is blocked (Part P). Kept as an explicit set here, consumed by
# capability_guard.py, rather than re-derived ad hoc at each call site.
#
# Includes the pre-activation states too, not just post-activation
# restriction states -- Part Y is explicit that an rc.1-upgraded install
# with no licensing state yet must still let a customer view/backup/restore/
# export their existing data before activation ("keep backup, restore,
# export, and data viewing available before activation"). Read access being
# blocked during ACTIVATION_REQUIRED was caught here, before
# capability_guard.py was built on top of an incomplete set, by re-reading
# Part Y while designing the guard's test cases.
DATA_PRESERVED_FAMILY = ACTIVE_FAMILY | frozenset(
    {
        LicenseState.NOT_CONFIGURED,
        LicenseState.ACTIVATION_REQUIRED,
        LicenseState.ACTIVATING,
        LicenseState.RESTRICTED,
        LicenseState.SUSPENDED,
        LicenseState.REVOKED,
        LicenseState.EXPIRED,
        LicenseState.SUBSCRIPTION_LAPSED,
        LicenseState.TRIAL_ENDED,
        LicenseState.DEVICE_DEACTIVATED,
        LicenseState.CLOCK_REVIEW_REQUIRED,
        LicenseState.LOCAL_STATE_CORRUPT,
    }
)

# States in which every local commercial capability is allowed (subject only
# to the entitlement gate) -- what capability_guard.py consults first. It is
# ACTIVE_FAMILY plus SUBSCRIPTION_LAPSED and nothing else. SUBSCRIPTION_LAPSED
# is kept OUT of ACTIVE_FAMILY on purpose: ACTIVE_FAMILY also answers "is
# this install currently paid up" for the hub/sync decisions, and an unpaid
# shop must keep selling without getting the paid extras back.
SELLING_FAMILY = ACTIVE_FAMILY | frozenset({LicenseState.SUBSCRIPTION_LAPSED})

# States in which multi-device sync (cloud relay AND the in-shop LAN hub) is
# paused for commercial reasons. Only the unpaid lapse -- every other state
# keeps exactly the sync behaviour it had before this set existed (sync was
# never licence-gated), so an install with licensing switched off
# (NOT_CONFIGURED) is untouched. The outbox keeps filling while paused and
# drains on the first sync after renewal; nothing is dropped.
SYNC_PAUSED_STATES = frozenset({LicenseState.SUBSCRIPTION_LAPSED})


def licence_pauses_sync(state: object) -> bool:
    """True only for a state whose commercial status pauses sync. Accepts a
    LicenseState, its raw stored string, or None; anything unrecognized is
    NOT a pause -- sync was never licence-gated before, so an unreadable
    state keeps today's behaviour rather than silently cutting a paying
    shop off (selling is already handled by the capability guard's own
    fail-closed LOCAL_STATE_CORRUPT path)."""
    try:
        return LicenseState(state) in SYNC_PAUSED_STATES
    except ValueError:
        return False

TERMINAL_UNTIL_REACTIVATION = frozenset({LicenseState.REVOKED, LicenseState.DEVICE_DEACTIVATED})


class InvalidTransitionError(ValueError):
    pass


# Explicit adjacency: {from_state: {event: to_state}}. An event not listed
# for the current state is invalid and raises rather than silently no-op'ing
# -- a caller passing an unexpected event/state combination is a bug to
# surface, not paper over.
_TRANSITIONS: dict[LicenseState, dict[str, LicenseState]] = {
    LicenseState.NOT_CONFIGURED: {
        "device_key_generated": LicenseState.ACTIVATION_REQUIRED,
    },
    LicenseState.ACTIVATION_REQUIRED: {
        "activation_submitted": LicenseState.ACTIVATING,
    },
    LicenseState.ACTIVATING: {
        "owner_approved": LicenseState.ACTIVE_ONLINE,
        "owner_rejected": LicenseState.ACTIVATION_REQUIRED,
        "network_failure": LicenseState.ACTIVATION_REQUIRED,
    },
    LicenseState.ACTIVE_ONLINE: {
        "checkin_succeeded": LicenseState.ACTIVE_ONLINE,
        "checkin_failed_network": LicenseState.ACTIVE_OFFLINE,
    },
    LicenseState.ACTIVE_OFFLINE: {
        "checkin_succeeded": LicenseState.ACTIVE_ONLINE,
        "warning_threshold_crossed": LicenseState.WARNING,
    },
    LicenseState.WARNING: {
        "checkin_succeeded": LicenseState.ACTIVE_ONLINE,
        "grace_threshold_crossed": LicenseState.GRACE_PERIOD,
    },
    LicenseState.GRACE_PERIOD: {
        "checkin_succeeded": LicenseState.ACTIVE_ONLINE,
        "grace_exhausted": LicenseState.RESTRICTED,
    },
    LicenseState.RESTRICTED: {
        "checkin_succeeded": LicenseState.ACTIVE_ONLINE,
    },
    LicenseState.TRIAL_ENDED: {
        # Payment lands in Owner -> the next check-in carries an ACTIVE lease.
        "checkin_succeeded": LicenseState.ACTIVE_ONLINE,
    },
    LicenseState.SUSPENDED: {
        "owner_reactivated": LicenseState.ACTIVE_ONLINE,
    },
    LicenseState.CLOCK_REVIEW_REQUIRED: {
        "online_verification_succeeded": LicenseState.ACTIVE_ONLINE,
    },
    LicenseState.LOCAL_STATE_CORRUPT: {
        "reset_completed": LicenseState.ACTIVATION_REQUIRED,
    },
    LicenseState.DEVICE_DEACTIVATED: {
        "replacement_completed": LicenseState.DEVICE_REPLACED,
        # Same device, re-activated with the same key after a deactivation
        # (accidental or deliberate-then-undone) -- distinct from
        # replacement (which implies a NEW device key). Goes through the
        # normal activation flow again since Owner's device-limit
        # accounting is the authority on whether this is allowed, not a
        # local decision.
        "reactivation_requested": LicenseState.ACTIVATION_REQUIRED,
    },
    LicenseState.DEVICE_REPLACED: {
        "device_key_generated": LicenseState.ACTIVATION_REQUIRED,
    },
}

# Events valid from ANY state (checked before the per-state table above).
_GLOBAL_EVENTS: dict[str, LicenseState] = {
    "owner_suspended": LicenseState.SUSPENDED,
    "owner_revoked": LicenseState.REVOKED,
    "owner_expired": LicenseState.EXPIRED,
    "device_deactivation_confirmed": LicenseState.DEVICE_DEACTIVATED,
    "clock_rollback_suspected": LicenseState.CLOCK_REVIEW_REQUIRED,
    "local_state_corruption_detected": LicenseState.LOCAL_STATE_CORRUPT,
}


def transition(current: LicenseState, event: str) -> LicenseState:
    """Pure function: (current state, event) -> new state. Raises
    InvalidTransitionError for any (state, event) pair not explicitly
    modeled -- there is no silent fallback."""
    if event in _GLOBAL_EVENTS:
        return _GLOBAL_EVENTS[event]
    table = _TRANSITIONS.get(current, {})
    if event not in table:
        raise InvalidTransitionError(f"No transition for event {event!r} from state {current.value!r}.")
    return table[event]
