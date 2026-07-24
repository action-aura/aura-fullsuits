"""LicenseCapabilityGuard (Part T) -- decides whether a local operation is
allowed. This is the ONLY place that decision is made; product route
decorators/service-layer checks call this and nothing else. Deny-by-default
(Part S/10): an unrecognized state or missing entitlement always denies,
never defaults to allow.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Optional

from .state_machine import ACTIVE_FAMILY, DATA_PRESERVED_FAMILY, LicenseState

# Distinguishes WHY a capability was denied -- Part S requires the guard to
# tell apart "feature not built" (a caller-side concept: this guard was
# never asked about a capability that doesn't exist yet, so it can't
# produce this reason itself) from "feature not licensed"
# (CAPABILITY_NOT_ENTITLED) from "license inactive" (LICENSE_INACTIVE).
DENIAL_REASON_LICENSE_INACTIVE = "LICENSE_INACTIVE"
DENIAL_REASON_NOT_ENTITLED = "CAPABILITY_NOT_ENTITLED"


@dataclass(frozen=True)
class CapabilityDecision:
    allowed: bool
    denial_reason: Optional[str] = None


def evaluate_capability(
    *,
    capability_code: str,
    current_state: LicenseState,
    restricted_mode_allowlist: frozenset,
    required_entitlement: Optional[str] = None,
    entitlements: Optional[Mapping[str, object]] = None,
) -> CapabilityDecision:
    """Pure decision function -- no I/O, no side effects, fully unit
    testable. current_state should be the value already computed by
    LicensePolicyEvaluator (policy_evaluator.py) / LicenseStateRepository,
    not re-derived here.

    Precedence: state gate first (can this capability even be considered
    right now), then entitlement gate (is it actually licensed) -- matching
    entitlement-resolution-design.md's own deny-by-default philosophy
    reused client-side.
    """
    if current_state in ACTIVE_FAMILY:
        pass  # full normal operation, subject only to the entitlement gate below
    elif current_state in DATA_PRESERVED_FAMILY:
        if capability_code not in restricted_mode_allowlist:
            return CapabilityDecision(False, DENIAL_REASON_LICENSE_INACTIVE)
    else:
        # Not in either family (e.g. NOT_CONFIGURED reached in a state where
        # even that read/backup allowance line hasn't been drawn -- current
        # design has no such state left over after the Part Y fix, but this
        # branch is the deny-by-default backstop if one is ever added).
        return CapabilityDecision(False, DENIAL_REASON_LICENSE_INACTIVE)

    if required_entitlement is not None:
        value = (entitlements or {}).get(required_entitlement, False)
        if not value:
            return CapabilityDecision(False, DENIAL_REASON_NOT_ENTITLED)

    return CapabilityDecision(True, None)
