"""Phase 9.5D Milestone 12 -- refund entitlement consequence policy.

Non-Negotiable Rule 7: refunds must invoke an explicit entitlement
consequence policy, never silently choose an outcome. This module is
the pure decision function; APPLYING the chosen outcome to a real
Subscription/License requires Milestone 13's fulfillment link (a
Subscription doesn't exist to suspend/revoke until fulfillment is
built) -- so this milestone provides the decision, honestly not yet
wired to a real mutation. See
docs/owner/phase9_5d/refund-entitlement-consequence-policy.md.
"""
from __future__ import annotations

SUSPEND_ENTITLEMENTS = "SUSPEND_ENTITLEMENTS"
REVOKE_ENTITLEMENTS = "REVOKE_ENTITLEMENTS"
RETAIN_WITH_APPROVED_EXCEPTION = "RETAIN_WITH_APPROVED_EXCEPTION"
NO_CONSEQUENCE = "NO_CONSEQUENCE"

# Real, minimal, honestly-scoped default policy: a full refund after
# fulfillment suspends entitlements (reversible -- matches this
# codebase's existing preference for EmergencyExtension-style temporary,
# reversible states over irreversible ones elsewhere in the commercial_ops
# domain); a partial refund after fulfillment has no automatic
# consequence (retaining entitlements is the safe default -- the
# customer paid for and received *something*, a partial refund doesn't
# by itself imply the product should stop working). REVOKE_ENTITLEMENTS
# and RETAIN_WITH_APPROVED_EXCEPTION are real, valid outcomes this
# function can return but the default policy below never selects
# REVOKE automatically -- that requires an explicit management decision
# (out of this module's pure-decision scope; a future route/service can
# call this with a management override, not implemented this phase).
DEFAULT_FULL_REFUND_CONSEQUENCE = SUSPEND_ENTITLEMENTS


def determine_entitlement_consequence(*, order_was_fulfilled: bool, is_full_refund: bool) -> str:
    """Pure decision function -- no I/O, no model access. Before
    fulfillment: cancelled/refunded sale must not create a Subscription
    or License in the first place (Milestone 13's own eligibility check
    handles this -- there is nothing to suspend/revoke, hence
    NO_CONSEQUENCE). After fulfillment: a full refund triggers the
    configured default; a partial refund has no automatic consequence
    (documented policy, not an oversight)."""
    if not order_was_fulfilled:
        return NO_CONSEQUENCE
    if is_full_refund:
        return DEFAULT_FULL_REFUND_CONSEQUENCE
    return NO_CONSEQUENCE
