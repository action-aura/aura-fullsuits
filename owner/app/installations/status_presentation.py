"""UI modernization Stage D.5 (Licensing Command Center) -- shared
status-badge-color helper for the Installations list/detail screens.

No status-step (timeline) builder exists in this module, deliberately --
see the "Timeline-visualization decision" section of
docs/owner/ui-modernization/licensing-command-center-contract.md for the
full reasoning. Summary: Installation's real VALID_TRANSITIONS graph
(app.installations.services) has ACTIVE<->SUSPENDED as its central,
repeatable operating cycle -- a device is routinely suspended (support
hold, temporary device-slot reclaim, customer request) and reactivated
many times over its real functional life, with no single "done" state: an
installation typically just *lives* in ACTIVE (toggling to SUSPENDED and
back) for as long as the customer uses it -- DEACTIVATED/REPLACED are real
removals/retirements, not a "successful completion" milestone the way
License's EXPIRED (a normal, intended term conclusion) is. Forcing this
into components/commercial_record.html's steps() linear-progress-bar macro
would misrepresent the real state machine the same way it would for
Subscription. This module intentionally offers only a real status badge;
the detail template surfaces two real, plain chronological history lists
instead: the existing "Activation events" table (ActivationEvent, already
shown) and a real "Status history" table (InstallationStatusHistory) --
which, unlike Subscriptions/Licenses, this screen was NOT already showing
before this pass, a real, disclosed, additive gap fixed here (see the
contract doc's "Real bugs found and fixed" section).

Non-Negotiable: "the UI must never suggest an invalid state transition...
the backend state machine remains authoritative." This module never decides
whether a transition is valid and never writes anything.
"""
from __future__ import annotations

_SUCCESS = {"ACTIVE"}
_DANGER = {"DEACTIVATED"}
# Everything else (REGISTERED, PENDING_ACTIVATION, SUSPENDED, REPLACED)
# renders "pending" -- SUSPENDED is deliberately NOT "danger": a temporary,
# recoverable hold (ACTIVE<->SUSPENDED is a real, ordinary cycle, see module
# docstring), the same "pending" treatment License's own SUSPENDED gets, for
# cross-entity consistency. REPLACED is a neutral supersession, not a
# failure.


def installation_badge_class(status: str) -> str:
    """installations/list.html's and installations/detail.html's existing
    inline ternary (`'active' if status == 'ACTIVE' else 'draft'`) had NO
    danger branch at all across Installation's 6 real statuses -- a real,
    disclosed gap. 'active'/'draft' and 'success'/'pending' are the same CSS
    color buckets (components.css), so ACTIVE's own color is visually
    unchanged; DEACTIVATED moving from the old 'draft' (neutral) bucket into
    'danger' (red) is the real, new, disclosed fix."""
    if status in _SUCCESS:
        return "success"
    if status in _DANGER:
        return "danger"
    return "pending"
