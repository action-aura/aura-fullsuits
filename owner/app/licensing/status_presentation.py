"""UI modernization Stage D.5 (Licensing Command Center) -- shared
status-badge-color and status-step (timeline) presentation helpers for the
License detail/list screens.

Timeline-visualization decision: License's real VALID_TRANSITIONS graph
(app.licensing.services) is genuinely, mostly linear-with-real-off-path-
deviations -- DRAFT->ISSUED->ACTIVE->EXPIRED[->REPLACED] is the real,
intended lifecycle (create the record, issue the key, the customer
activates and uses it, the term ends, optionally issue a replacement).
SUSPENDED is a real, non-terminal deviation (exactly the same shape as
Expense's RETURNED in finance-ui-contract.md: a temporary hold that can
legally return to ACTIVE, or move on to REVOKED/EXPIRED) rather than an
ordinary, repeatable business cycle the way Subscription's own
ACTIVE<->PAST_DUE<->SUSPENDED triangle is (see
app.subscriptions.status_presentation's own docstring for that contrast) --
a license is suspended for a specific, exceptional reason (billing dispute,
compliance hold, security concern), not as a routine part of its operating
life. REVOKED is a real terminal off-path (like REJECTED/VOID elsewhere).
This module therefore reuses components/commercial_record.html's steps()
macro (imported by licensing/detail.html), the same shape
commercial-flow-ui-contract.md and finance-ui-contract.md already used for
every genuinely mostly-linear entity. Full reasoning (including why
Installation, which has a structurally similar ACTIVE<->SUSPENDED edge,
gets a DIFFERENT (cyclic, badge+history) treatment):
docs/owner/ui-modernization/licensing-command-center-contract.md.

Non-Negotiable: "the UI must never suggest an invalid state transition...
the backend state machine remains authoritative." This module never decides
whether a transition is valid and never writes anything -- it only
visualizes the license's REAL, already-persisted status against:

- the real status vocabulary (app.i18n_labels.license_status_label,
  sourced from app.models.licensing.License.status), and
- the real transition graph (VALID_TRANSITIONS in app.licensing.services),
  used here only to prove which happy-path steps a real off-path status
  (SUSPENDED/REVOKED/REPLACED) could only have been reached through --
  never to invent a timestamp or a step that didn't really happen.

Real, per-transition timestamps: unlike Quote/Order/Invoice (Stage D.3) or
Expense (D.4), License already has a real, generic per-transition audit
table -- LicenseStatusHistory (app.models.licensing), a row written by
every real transition_license()/issue_license_key() call, recording
from_status/to_status/changed_by_staff_user_id/reason/created_at. This
module uses that table as the authoritative source for each step's real
"reached at" timestamp (the most recent history row whose to_status matches
the step) -- more precise than a fallback to updated_at (which a later,
unrelated transition could silently overwrite), and never fabricated.

Badge-color mapping is centralized here for the same reason
commercial-flow-ui-contract.md/finance-ui-contract.md centralized their
own: licensing/list.html's existing inline ternary (`'active' if status in
('ACTIVE','ISSUED') else ('danger' if status == 'REVOKED' else 'draft')`)
had REVOKED as its only danger branch -- EXPIRED (a real, non-usable,
negative-outcome status) fell into the same neutral 'draft' bucket as
DRAFT/SUSPENDED/REPLACED, a real, disclosed gap. Fixed by adding EXPIRED to
the danger set; ACTIVE/ISSUED/REVOKED are unchanged (reproduced exactly).
SUSPENDED deliberately stays 'pending', not 'danger' -- a temporary,
recoverable hold, not a hard failure (same "pending" treatment
Subscription's PAST_DUE/SUSPENDED get, for cross-entity consistency)."""
from __future__ import annotations

from app.i18n_labels import license_status_label

_SUCCESS = {"ACTIVE", "ISSUED"}
_DANGER = {"REVOKED", "EXPIRED"}


def license_badge_class(status: str) -> str:
    if status in _SUCCESS:
        return "success"
    if status in _DANGER:
        return "danger"
    return "pending"


# ---------------------------------------------------------- Step builder --

def _build_steps(step_order: list[str], labels: dict[str, str], timestamps: dict, current_status: str, offpath_reached_rank: int | None) -> list[dict]:
    """Entity-agnostic step-state computation -- structurally identical to
    app.commercial_sales.status_presentation._build_steps / app.expenses.
    status_presentation._build_steps (same discipline, a fresh copy per this
    module's own docstring, not a cross-module import)."""
    if current_status in step_order:
        current_rank = step_order.index(current_status)
        is_offpath = False
    else:
        current_rank = offpath_reached_rank if offpath_reached_rank is not None else -1
        is_offpath = True

    steps = []
    for i, code in enumerate(step_order):
        if not is_offpath and i == current_rank:
            state = "current"
        elif i <= current_rank:
            state = "done"
        else:
            state = "upcoming"
        steps.append({"code": code, "label": labels[code], "state": state, "timestamp": timestamps.get(code)})
    return steps


def _latest_transition_at(history, to_status: str):
    """Most recent real LicenseStatusHistory row's created_at where
    to_status == the given code, or None if that status was never reached.
    Real audit-trail timestamps only -- never fabricated."""
    matches = [h.created_at for h in history if h.to_status == to_status]
    return max(matches) if matches else None


def license_timeline(license_row) -> dict:
    """VALID_TRANSITIONS (app.licensing.services): DRAFT->{ISSUED};
    ISSUED->{ACTIVE,SUSPENDED,REVOKED}; ACTIVE->{SUSPENDED,EXPIRED,REVOKED};
    SUSPENDED->{ACTIVE,REVOKED,EXPIRED}; EXPIRED->{REPLACED}; REVOKED/
    REPLACED terminal. Happy path shown: Draft -> Issued -> Active ->
    Expired (REPLACED is a real, optional follow-on action from Expired,
    not shown as a 5th happy-path step -- see below).

    ISSUED can reach SUSPENDED directly, without ever passing through
    ACTIVE -- unlike Expense's RETURNED (always deterministically reachable
    only from SUBMITTED), a license's SUSPENDED/REVOKED status alone does
    NOT prove ACTIVE was ever reached. This is resolved from the real
    LicenseStatusHistory audit trail (never guessed): `was_active` is True
    iff any real history row has to_status == "ACTIVE". REPLACED, by
    contrast, is deterministic -- VALID_TRANSITIONS["EXPIRED"] == {"REPLACED"}
    is License's only entry into REPLACED, so a REPLACED license always
    proves EXPIRED (and therefore every step before it) was reached, no
    ambiguity to resolve.

    SUSPENDED and REVOKED are both real off-path deviations, rendered via
    the same off-path chip mechanism as every other Stage D pass (SUSPENDED
    explicitly disclosed as non-terminal -- ACTIVE/REVOKED/EXPIRED are all
    still legally reachable from it -- the same "real but not terminal"
    treatment Expense's RETURNED already established).

    The generic on-path step calculation (`_build_steps`, reused unmodified
    from the commercial-sales/expenses precedent) marks every step up to and
    including the current status's own list position as "done" once the
    current status is itself in step_order -- so an EXPIRED license that
    went ISSUED -> SUSPENDED -> EXPIRED without ever touching ACTIVE will
    still show "Active: done". This is the exact same accepted imprecision
    app.commercial_sales.status_presentation.invoice_timeline's own
    docstring already documents for PARTIALLY_PAID ("an invoice can also go
    straight from Issued to Paid in one payment" -- PARTIALLY_PAID still
    renders done): an interior step on an otherwise-monotonic happy path is
    treated as an implied milestone, not a literally-visited-status claim,
    for consistency with that established precedent -- only genuine
    off-path deviations (SUSPENDED/REVOKED here) get the stricter,
    evidence-verified reached-rank treatment."""
    step_order = ["DRAFT", "ISSUED", "ACTIVE", "EXPIRED"]
    labels = {code: license_status_label(code) for code in step_order}
    history = license_row.status_history
    timestamps = {
        "DRAFT": license_row.created_at,
        "ISSUED": _latest_transition_at(history, "ISSUED") or license_row.issued_at,
        "ACTIVE": _latest_transition_at(history, "ACTIVE"),
        "EXPIRED": _latest_transition_at(history, "EXPIRED"),
    }
    was_active = any(h.to_status == "ACTIVE" for h in history)

    offpath = None
    reached_rank = None
    if license_row.status == "SUSPENDED":
        reached_rank = 2 if was_active else 1
        offpath = {
            "code": "SUSPENDED", "label": license_status_label("SUSPENDED"),
            "timestamp": _latest_transition_at(history, "SUSPENDED"),
            "badge_class": license_badge_class("SUSPENDED"),
        }
    elif license_row.status == "REVOKED":
        reached_rank = 2 if was_active else 1
        offpath = {
            "code": "REVOKED", "label": license_status_label("REVOKED"),
            "timestamp": _latest_transition_at(history, "REVOKED"),
            "badge_class": license_badge_class("REVOKED"),
        }
    elif license_row.status == "REPLACED":
        # Deterministic -- REPLACED is only reachable from EXPIRED (see docstring).
        reached_rank = 3
        offpath = {
            "code": "REPLACED", "label": license_status_label("REPLACED"),
            "timestamp": _latest_transition_at(history, "REPLACED"),
            "badge_class": license_badge_class("REPLACED"),
        }

    return {"steps": _build_steps(step_order, labels, timestamps, license_row.status, reached_rank), "offpath": offpath}
