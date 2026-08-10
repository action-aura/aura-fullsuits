"""UI modernization Stage D.5 (Licensing Command Center) -- shared
status-badge-color helper for the Subscriptions list/detail screens.

No status-step (timeline) builder exists in this module, deliberately --
see the "Timeline-visualization decision" section of
docs/owner/ui-modernization/licensing-command-center-contract.md for the
full reasoning. Summary: Subscription's real VALID_TRANSITIONS graph
(app.subscriptions.services) has ACTIVE<->PAST_DUE<->SUSPENDED as genuinely
ordinary, repeatable, bidirectional business operations -- a subscription
cycling ACTIVE -> SUSPENDED -> ACTIVE -> PAST_DUE -> ACTIVE over its
lifetime is normal, not a deviation from some single "true path" toward a
shared "done" state (EXPIRED/CANCELLED/COMPLETED are three, not one, equally
valid terminal outcomes reachable from different start points). Forcing
this into components/commercial_record.html's steps() linear-progress-bar
macro would misrepresent the real state machine and suggest a false sense
of forward progress that doesn't exist for this entity -- a violation of
this phase's own "never suggest an invalid/misleading state" rule. This
module intentionally offers only a real status badge; the existing plain
chronological "Status history" table (subscriptions/detail.html, backed by
the real SubscriptionStatusHistory audit-trail model) is kept as the
honest way to show a subscription's real transition history.

Non-Negotiable: "the UI must never suggest an invalid state transition...
the backend state machine remains authoritative." This module never decides
whether a transition is valid and never writes anything.
"""
from __future__ import annotations

_SUCCESS = {"ACTIVE", "COMPLETED"}
_DANGER = {"EXPIRED", "CANCELLED"}
# Everything else (DRAFT, PILOT, PAST_DUE, SUSPENDED) renders "pending" --
# PAST_DUE and SUSPENDED are real, ordinary, often-temporary/recoverable
# operational states (see module docstring), not hard failures, so they are
# deliberately NOT mapped to "danger" (reserved for the two real, terminal,
# non-recoverable negative outcomes) -- the same "pending", not "danger",
# treatment app.licensing.status_presentation.license_badge_class gives
# SUSPENDED, for cross-entity consistency.


def subscription_badge_class(status: str) -> str:
    """subscriptions/list.html's and subscriptions/detail.html's existing
    inline ternary (`'active' if status == 'ACTIVE' else 'draft'`) only ever
    distinguished ACTIVE from everything else -- no danger/pending split
    existed at all across Subscription's 8 real statuses, a real, disclosed
    gap (unlike commercial-sales/expenses, which already had a 3-way
    ternary before this pass). 'active'/'draft' and 'success'/'pending' are
    the same CSS color buckets (components.css: `.badge.active,
    .badge.confirmed, .badge.success, .badge.ok` / `.badge.warn,
    .badge.pending, .badge.draft`) -- so ACTIVE's own color is visually
    unchanged; EXPIRED/CANCELLED moving from the old 'draft' (neutral) bucket
    into 'danger' (red) is the real, new, disclosed fix."""
    if status in _SUCCESS:
        return "success"
    if status in _DANGER:
        return "danger"
    return "pending"
