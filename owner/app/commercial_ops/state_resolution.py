"""Deterministic commercial state-resolution service (Phase 8 Part B).

Formalizes the relationship between subscription status and license status
into one authoritative answer. Pure decision function over already-known
values -- no I/O, no session -- mirroring the existing pattern in
commercial_runtime/licensing_contracts/capability_guard.py (state gate,
deny-by-default backstop, nothing re-derived ad hoc at each call site).

Nothing here re-derives or overrides Subscription.status / License.status --
those remain the single source of truth, set only via
transition_subscription()/transition_license() (see
owner/app/subscriptions/services.py and owner/app/licensing/services.py).
This service's job is narrower: given the current values of both, answer
the questions every caller -- the activation route, the check-in route, the
internal dashboard, and Milestone 2's renewal-application logic -- would
otherwise re-derive ad hoc and inconsistently:

  1. What is the one authoritative commercial state right now?
  2. May a NEW activation happen?
  3. May an EXISTING installation's check-in result in a fresh signed
     assertion?
  4. What does the caller need to do about it (reason code + required
     action), for logging/notifications/UI?

`CommercialState` is deliberately distinct from, and an input to, the
product-local `LicenseState`
(commercial_runtime/licensing_contracts/state_machine.py). One
`CommercialState` can still fan out into several product-local states
depending on elapsed offline time (e.g. `ACTIVE` commercially can locally be
`ACTIVE_ONLINE`, `ACTIVE_OFFLINE`, `WARNING`, or `GRACE_PERIOD` depending on
how long since the last successful check-in) -- this module only answers
"what does Owner's commercial record say right now," never "what has the
device locally decided."
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date
from enum import Enum


class CommercialState(str, Enum):
    NO_LICENSE = "NO_LICENSE"  # subscription exists, no license issued yet
    ACTIVE = "ACTIVE"  # subscription ACTIVE, license ACTIVE/ISSUED
    PILOT_ACTIVE = "PILOT_ACTIVE"  # subscription PILOT, license ACTIVE/ISSUED
    PAST_DUE = "PAST_DUE"  # subscription PAST_DUE
    SUSPENDED = "SUSPENDED"  # subscription SUSPENDED or license SUSPENDED
    EXPIRED = "EXPIRED"  # subscription EXPIRED or license EXPIRED
    CANCELLED = "CANCELLED"  # subscription CANCELLED
    PILOT_COMPLETED = "PILOT_COMPLETED"  # subscription COMPLETED, no conversion/extension yet
    REVOKED = "REVOKED"  # license REVOKED -- always wins, an explicit signed security decision
    INVALID = "INVALID"  # unrecognized/inconsistent combination -- deny-by-default backstop


@dataclass(frozen=True)
class CommercialStateDecision:
    state: CommercialState
    effective_date: date | None
    governing_record: str  # "subscription" or "license" -- which record's status decided this
    reason_code: str
    required_action: str | None
    may_issue_assertion: bool
    may_activate_new_installation: bool
    may_check_in_existing_installation: bool
    audit_context: dict
    # Populated once Milestone 3 (Part I) builds the commercial policy
    # model; left None until then rather than guessed at, so this dataclass
    # shape does not need to change later. Never conflated with the
    # product-local *technical* offline policy (Part I is explicit these
    # are distinct concepts).
    applicable_policy_code: str | None = None


_ACTIVE_LICENSE_STATUSES = ("ACTIVE", "ISSUED")


def resolve_commercial_state(
    *,
    subscription_status: str,
    license_status: str | None,
    subscription_end_date: date | None,
    license_valid_until: date | None,
    as_of: date,
    cancellation_effective_date: date | None = None,
    emergency_extension_active: bool = False,
) -> CommercialStateDecision:
    """Pure function. `license_status` is `None` when no license has been
    issued for this subscription yet -- a real, valid state (`NO_LICENSE`),
    not an error.

    `emergency_extension_active` (Part N, Milestone 4): caller-supplied --
    this function stays I/O-free and never queries EmergencyExtension
    itself (see commercial_ops/emergency_extensions.py's
    is_emergency_extension_active(), the only place that looks the row up).
    When True, overrides denial of assertion issuance/check-in on the
    decision this function would otherwise return -- EXCEPT REVOKED, which
    is resolved and returned before this flag is even consulted, so an
    emergency extension can never override an explicit security
    revocation."""
    decision = _resolve_base(
        subscription_status=subscription_status,
        license_status=license_status,
        subscription_end_date=subscription_end_date,
        license_valid_until=license_valid_until,
        as_of=as_of,
        cancellation_effective_date=cancellation_effective_date,
    )
    if emergency_extension_active and decision.state != CommercialState.REVOKED:
        decision = replace(
            decision,
            may_issue_assertion=True,
            may_check_in_existing_installation=True,
            required_action=None,
            reason_code=f"{decision.reason_code}_EMERGENCY_EXTENSION_ACTIVE",
        )
    return decision


def _resolve_base(
    *,
    subscription_status: str,
    license_status: str | None,
    subscription_end_date: date | None,
    license_valid_until: date | None,
    as_of: date,
    cancellation_effective_date: date | None = None,
) -> CommercialStateDecision:

    # REVOKED always wins -- an explicit signed security decision, never
    # overridden by a still-ACTIVE subscription (spec Part I: "a revoked
    # license must remain explicit and signed").
    if license_status == "REVOKED":
        return CommercialStateDecision(
            state=CommercialState.REVOKED,
            effective_date=as_of,
            governing_record="license",
            reason_code="LICENSE_REVOKED",
            required_action="Contact Action Aura support to review revocation.",
            may_issue_assertion=False,
            may_activate_new_installation=False,
            may_check_in_existing_installation=False,
            audit_context={"subscription_status": subscription_status, "license_status": license_status},
        )

    if subscription_status == "CANCELLED":
        # Spec Part B: "a cancelled subscription may remain usable until its
        # effective end date." Immediate vs end-of-term cancellation is
        # distinguished entirely by whether `cancellation_effective_date` is
        # still in the future relative to `as_of` -- never inferred from
        # `cancellation_date` alone (that column records *when cancellation
        # was recorded*, not when it takes effect; Milestone 2's renewal/
        # cancellation workflow is responsible for setting
        # `cancellation_effective_date` explicitly for the immediate-vs-
        # end-of-term distinction the spec requires).
        within_effective_period = (
            cancellation_effective_date is not None and as_of <= cancellation_effective_date
        )
        return CommercialStateDecision(
            state=CommercialState.CANCELLED,
            effective_date=cancellation_effective_date,
            governing_record="subscription",
            reason_code=(
                "SUBSCRIPTION_CANCELLED_EFFECTIVE_PERIOD" if within_effective_period else "SUBSCRIPTION_CANCELLED"
            ),
            required_action=(
                None if within_effective_period
                else "Subscription has ended. Renewal required to continue commercial features."
            ),
            may_issue_assertion=within_effective_period,
            may_activate_new_installation=False,
            may_check_in_existing_installation=within_effective_period,
            audit_context={
                "subscription_status": subscription_status,
                "cancellation_effective_date": str(cancellation_effective_date) if cancellation_effective_date else None,
            },
        )

    if subscription_status == "COMPLETED":
        return CommercialStateDecision(
            state=CommercialState.PILOT_COMPLETED,
            effective_date=subscription_end_date,
            governing_record="subscription",
            reason_code="PILOT_COMPLETED_NO_CONVERSION",
            required_action="Convert pilot to a paid plan or create an explicit extension to continue.",
            may_issue_assertion=False,
            may_activate_new_installation=False,
            may_check_in_existing_installation=False,
            audit_context={"subscription_status": subscription_status},
        )

    if subscription_status == "SUSPENDED" or license_status == "SUSPENDED":
        return CommercialStateDecision(
            state=CommercialState.SUSPENDED,
            effective_date=as_of,
            governing_record="subscription" if subscription_status == "SUSPENDED" else "license",
            reason_code="SUBSCRIPTION_SUSPENDED" if subscription_status == "SUSPENDED" else "LICENSE_SUSPENDED",
            required_action="Contact Action Aura support to resolve suspension.",
            may_issue_assertion=False,
            may_activate_new_installation=False,
            # A rejected check-in still reaches the product's own
            # reevaluate-on-failure pipeline (Phase 7V-A Part I/J) even
            # though no fresh assertion is issued -- "check-in honored, no
            # assertion" is the correct distinction here, not "check-in
            # refused outright."
            may_check_in_existing_installation=True,
            audit_context={"subscription_status": subscription_status, "license_status": license_status},
        )

    if subscription_status == "EXPIRED" or license_status == "EXPIRED":
        return CommercialStateDecision(
            state=CommercialState.EXPIRED,
            effective_date=subscription_end_date or license_valid_until,
            governing_record="subscription" if subscription_status == "EXPIRED" else "license",
            reason_code="SUBSCRIPTION_EXPIRED" if subscription_status == "EXPIRED" else "LICENSE_EXPIRED",
            required_action="Renewal required to continue commercial features.",
            may_issue_assertion=False,
            may_activate_new_installation=False,
            may_check_in_existing_installation=True,
            audit_context={"subscription_status": subscription_status, "license_status": license_status},
        )

    if subscription_status == "PAST_DUE":
        return CommercialStateDecision(
            state=CommercialState.PAST_DUE,
            effective_date=as_of,
            governing_record="subscription",
            reason_code="SUBSCRIPTION_PAST_DUE",
            required_action="Payment required to avoid restriction after commercial grace ends.",
            # Spec Part I: past-due must NOT automatically mean
            # revoked/blocked -- normal operation continues while a license
            # remains ACTIVE/ISSUED. A distinct commercial-grace policy
            # (Milestone 3) may later narrow this further as time in
            # PAST_DUE accumulates; this function only encodes the
            # immediate, unconditional part of the rule.
            may_issue_assertion=license_status in _ACTIVE_LICENSE_STATUSES,
            may_activate_new_installation=False,
            may_check_in_existing_installation=True,
            audit_context={"subscription_status": subscription_status, "license_status": license_status},
        )

    if subscription_status in ("ACTIVE", "PILOT"):
        if license_status is None:
            return CommercialStateDecision(
                state=CommercialState.NO_LICENSE,
                effective_date=None,
                governing_record="subscription",
                reason_code="NO_LICENSE_ISSUED",
                required_action="Issue a license for this subscription before activation.",
                may_issue_assertion=False,
                may_activate_new_installation=False,
                may_check_in_existing_installation=False,
                audit_context={"subscription_status": subscription_status},
            )
        if license_status in _ACTIVE_LICENSE_STATUSES:
            state = CommercialState.PILOT_ACTIVE if subscription_status == "PILOT" else CommercialState.ACTIVE
            return CommercialStateDecision(
                state=state,
                effective_date=subscription_end_date or license_valid_until,
                governing_record="subscription",
                reason_code="COMMERCIALLY_ACTIVE",
                required_action=None,
                may_issue_assertion=True,
                may_activate_new_installation=True,
                may_check_in_existing_installation=True,
                audit_context={"subscription_status": subscription_status, "license_status": license_status},
            )
        # ACTIVE/PILOT subscription but a license status that should not
        # coexist with it (e.g. still DRAFT) -- deny-by-default, not a
        # crash and not silently treated as active. Part B: "do not allow
        # independent arbitrary states to drift without reconciliation."
        return CommercialStateDecision(
            state=CommercialState.INVALID,
            effective_date=None,
            governing_record="license",
            reason_code="INCONSISTENT_LICENSE_STATE",
            required_action="Reconciliation required -- subscription is active but license state is unexpected.",
            may_issue_assertion=False,
            may_activate_new_installation=False,
            may_check_in_existing_installation=False,
            audit_context={"subscription_status": subscription_status, "license_status": license_status},
        )

    if subscription_status == "DRAFT":
        return CommercialStateDecision(
            state=CommercialState.INVALID,
            effective_date=None,
            governing_record="subscription",
            reason_code="SUBSCRIPTION_NOT_YET_ACTIVE",
            required_action="Subscription is not yet active.",
            may_issue_assertion=False,
            may_activate_new_installation=False,
            may_check_in_existing_installation=False,
            audit_context={"subscription_status": subscription_status},
        )

    # Any future/unrecognized subscription status -- deny-by-default
    # backstop (spec Part B's own instruction).
    return CommercialStateDecision(
        state=CommercialState.INVALID,
        effective_date=None,
        governing_record="subscription",
        reason_code="UNRECOGNIZED_SUBSCRIPTION_STATE",
        required_action="Reconciliation required -- unrecognized subscription state.",
        may_issue_assertion=False,
        may_activate_new_installation=False,
        may_check_in_existing_installation=False,
        audit_context={"subscription_status": subscription_status, "license_status": license_status},
    )
