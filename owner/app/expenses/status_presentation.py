"""UI modernization Stage D -- shared status-badge-color and status-step
(timeline) presentation helpers for the Expenses list/detail screens.

Same structural/reasoning discipline as
app.commercial_sales.status_presentation (copied deliberately, not
imported/reused -- see finance-ui-contract.md: Expense's real transition
graph has a real revision loop (RETURNED -> resubmit -> a brand-new
approval cycle) that the append-only, forward-only commercial-sales graphs
(Quote/Order/Invoice/Refund/Payment) don't have, so a fresh, Expense-
specific module is the honest choice rather than forcing this shape through
the same helper).

Non-Negotiable: "the UI must never suggest an invalid state transition...
the backend state machine remains authoritative." This module NEVER decides
whether a transition is valid and never writes anything -- it only
visualizes the expense's REAL, already-persisted status against:

- EXPENSE_STATUSES (app.i18n_labels.expense_status_label's own vocabulary,
  sourced from app.models.expenses.Expense.status), and
- EXPENSE_TRANSITIONS (app.expenses.errors) -- used here only to prove
  which happy-path steps a real off-path status (RETURNED/REJECTED/VOID)
  could only have been reached through, never to invent a timestamp or a
  step that didn't really happen.

Badge-color mapping is centralized here for the same reason
commercial-flow-ui-contract.md centralized its own: expenses_list.html and
expense_detail.html already computed the identical inline ternary
independently (verified byte-for-byte identical before this pass) -- no
missing-color bug existed here, but duplicating the same real business
mapping in two templates is exactly the kind of drift the shared module
prevents going forward. The ternary below is reproduced exactly, not
changed."""
from __future__ import annotations

from app.i18n_labels import expense_status_label

# --------------------------------------------------------------- Badges --

def expense_badge_class(status: str) -> str:
    """Mirrors expenses_list.html's / expense_detail.html's existing
    ternary exactly."""
    if status in ("PAID", "APPROVED"):
        return "success"
    if status in ("REJECTED", "VOID"):
        return "danger"
    return "pending"


# ---------------------------------------------------------- Step builder --

def _build_steps(step_order: list[str], labels: dict[str, str], timestamps: dict, current_status: str, offpath_reached_rank: int | None) -> list[dict]:
    """Entity-agnostic step-state computation -- structurally identical to
    app.commercial_sales.status_presentation._build_steps (same discipline,
    a fresh copy per this module's own docstring, not a cross-module
    import)."""
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


def expense_timeline(expense, latest_approval) -> dict:
    """EXPENSE_TRANSITIONS (app.expenses.errors):
    DRAFT->{SUBMITTED,VOID}; SUBMITTED->{RETURNED,APPROVED,REJECTED,VOID};
    RETURNED->{SUBMITTED,VOID}; APPROVED->{PARTIALLY_PAID,PAID,VOID};
    PARTIALLY_PAID->{PAID}. Happy path: Draft -> Submitted -> Approved ->
    Partially paid -> Paid, with Partially paid a real, optional waypoint
    (an expense can be paid in one shot, going straight from Approved to
    Paid) -- same shape app.commercial_sales.status_presentation.
    invoice_timeline already uses for its own Partially paid/Paid pair.

    Expense has no submitted_at/approved_at/rejected_at column of its own
    (unlike Quote/Order) -- the real, closest-available timestamps live on
    ExpenseApproval (requested_at/decided_at), one row per approval CYCLE
    (append-only -- a RETURNED/REJECTED decision is never mutated further;
    resubmission opens a brand-new PENDING row, per
    app.expenses.approvals's own module docstring, Rule 6). `latest_approval`
    is the most-recently-*requested* ExpenseApproval for this expense (or
    None if it was voided straight from DRAFT, never submitted) --
    app.expenses.approvals.latest_approval_for_expense() -- always the row
    describing the expense's CURRENT cycle, so a post-RETURNED resubmission
    correctly shows the new cycle's timestamps, never the superseded one's.

    Neither Partially paid nor Paid has a dedicated "reached at" column
    (app.expenses.payments._recalculate_status() only ever sets
    Expense.status, nothing else) -- no timestamp is shown for either step,
    never fabricated from updated_at (which a later payment/reversal could
    overwrite and misrepresent an earlier one).

    RETURNED is real and, unlike every commercial-sales off-path state,
    genuinely NON-terminal (RETURNED -> SUBMITTED is a legal resubmission,
    RETURNED -> VOID is also legal) -- it is still rendered via the same
    off-path chip mechanism (a real state outside the fixed step list, not
    a bug to hide) since Expense.status is a single current value with no
    separate "in revision" flag; disclosed explicitly here, not silently
    treated as if it meant the same permanence as REJECTED/VOID. Both
    RETURNED and REJECTED are reachable ONLY from SUBMITTED (never from
    RETURNED itself for REJECTED, since decide_expense_approval() only acts
    on a PENDING approval, and RETURNED's own resubmission always creates a
    fresh PENDING cycle first) -- so both always have a deterministic
    reached rank of 1 (Submitted), no ambiguity to resolve from timestamps.

    VOID is reachable from DRAFT, SUBMITTED, RETURNED, or APPROVED only --
    never PARTIALLY_PAID (PARTIALLY_PAID's own EXPENSE_TRANSITIONS entry has
    no VOID target; once ANY payment is recorded, void_expense() can no
    longer even be attempted). Its reached rank is proven directly from
    `latest_approval`: rank 2 (Approved) if latest_approval.status ==
    "APPROVED", rank 1 (Submitted) if any approval row exists at all, else
    rank 0 (Draft) -- never guessed."""
    step_order = ["DRAFT", "SUBMITTED", "APPROVED", "PARTIALLY_PAID", "PAID"]
    labels = {code: expense_status_label(code) for code in step_order}
    approved_at = latest_approval.decided_at if latest_approval is not None and latest_approval.status == "APPROVED" else None
    timestamps = {
        "DRAFT": expense.created_at,
        "SUBMITTED": latest_approval.requested_at if latest_approval is not None else None,
        "APPROVED": approved_at,
        "PARTIALLY_PAID": None,
        "PAID": None,
    }

    offpath = None
    reached_rank = None
    was_submitted = latest_approval is not None
    was_approved = latest_approval is not None and latest_approval.status == "APPROVED"

    if expense.status == "RETURNED":
        reached_rank = 1
        offpath = {
            "code": "RETURNED", "label": expense_status_label("RETURNED"),
            "timestamp": latest_approval.decided_at if latest_approval is not None else None,
            "badge_class": expense_badge_class("RETURNED"),
        }
    elif expense.status == "REJECTED":
        reached_rank = 1
        offpath = {
            "code": "REJECTED", "label": expense_status_label("REJECTED"),
            "timestamp": latest_approval.decided_at if latest_approval is not None else None,
            "badge_class": expense_badge_class("REJECTED"),
        }
    elif expense.status == "VOID":
        reached_rank = 2 if was_approved else (1 if was_submitted else 0)
        # No dedicated voided_at column on Expense -- updated_at is the
        # real, closest-available "as of" timestamp (VOID is a real
        # terminal state; nothing transitions out of it afterward, so
        # updated_at cannot later be overwritten by a subsequent step the
        # way it could for a non-terminal status). Same reasoning
        # app.commercial_sales.status_presentation.invoice_timeline already
        # documents for its own VOID case.
        offpath = {"code": "VOID", "label": expense_status_label("VOID"), "timestamp": expense.updated_at, "badge_class": expense_badge_class("VOID")}

    return {"steps": _build_steps(step_order, labels, timestamps, expense.status, reached_rank), "offpath": offpath}
