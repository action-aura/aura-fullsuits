"""UI modernization Stage D -- shared status-badge-color and status-step
(timeline) presentation helpers for the Cash Closing list/detail screens.

Same structural/reasoning discipline as
app.commercial_sales.status_presentation (copied deliberately, not
imported/reused -- see finance-ui-contract.md: CashClosing's real
transition graph has two real backward loops (REJECTED -> DRAFT,
REOPENED -> {DRAFT, SUBMITTED, REVIEW_REQUIRED, APPROVED}) that the
append-only, forward-only commercial-sales graphs don't have, so a fresh,
CashClosing-specific module is the honest choice).

Non-Negotiable: "the UI must never suggest an invalid state transition...
the backend state machine remains authoritative." This module NEVER decides
whether a transition is valid and never writes anything -- it only
visualizes the closing's REAL, already-persisted status against
CASH_CLOSING_TRANSITIONS (app.expenses.errors) and real timestamp columns
(app.models.cash_closing.CashClosing / CashClosingReopenEvent).

Badge-color mapping is centralized here for the same reason
commercial-flow-ui-contract.md centralized its own: cash_closings_list.html
and cash_closing_detail.html already computed the identical inline ternary
independently (verified byte-for-byte identical before this pass) -- no
missing-color bug existed here, but the mapping is reproduced exactly, not
changed, and shared going forward instead of duplicated a third time."""
from __future__ import annotations

from app.i18n_labels import cash_closing_status_label

# --------------------------------------------------------------- Badges --

def cash_closing_badge_class(status: str) -> str:
    """Mirrors cash_closings_list.html's / cash_closing_detail.html's
    existing ternary exactly."""
    if status == "CLOSED":
        return "success"
    if status == "REJECTED":
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


def cash_closing_timeline(closing, latest_reopen_event) -> dict:
    """CASH_CLOSING_TRANSITIONS (app.expenses.errors):
    DRAFT->{SUBMITTED}; SUBMITTED->{REVIEW_REQUIRED,APPROVED,REJECTED};
    REVIEW_REQUIRED->{APPROVED,REJECTED}; APPROVED->{CLOSED,REOPENED};
    REJECTED->{DRAFT}; REOPENED->{DRAFT,SUBMITTED,REVIEW_REQUIRED,APPROVED};
    CLOSED->{REOPENED}. Happy path: Draft -> Submitted -> Approved ->
    Closed -- unlike every commercial-sales entity, all four real
    timestamp columns already exist directly on CashClosing itself
    (created_at/submitted_at/approved_at/closed_at), each unconditionally
    overwritten by its own real transition function
    (submit_closing/decide_closing/close_closing, app.cash_closing.services)
    -- so after a REOPENED cycle they always describe the LATEST cycle,
    never a stale earlier one (real column semantics, not fabricated).

    Review required and Rejected are real, non-terminal deviations (both
    can lead back toward Approved/Draft -- REJECTED -> DRAFT is a legal
    resubmission path) -- rendered via the same off-path chip mechanism
    commercial-sales' permanent terminal states use, disclosed here
    explicitly as non-terminal (a real state outside the fixed step list,
    not a permanent dead end). Both are reachable only from Submitted (or,
    for Review required -> Rejected, from Review required itself, which is
    itself only reachable from Submitted) -- so both always have a
    deterministic reached rank of 1 (Submitted), no ambiguity to resolve.

    Reopened is reachable only from Approved or Closed
    (app.cash_closing.services.reopen_closing()).
    CashClosingReopenEvent.prior_status (a real, append-only column,
    snapshotted BEFORE the mutation, per reopen_closing()'s own
    "snapshots the prior approved state before it's mutated" docstring)
    proves which one -- `latest_reopen_event` is the most recent reopen row
    for this closing (app.cash_closing.services.latest_reopen_event()),
    never None when status == REOPENED (reopen_closing() always creates one
    in the same transaction as the status change)."""
    step_order = ["DRAFT", "SUBMITTED", "APPROVED", "CLOSED"]
    labels = {code: cash_closing_status_label(code) for code in step_order}
    timestamps = {
        "DRAFT": closing.created_at,
        "SUBMITTED": closing.submitted_at,
        "APPROVED": closing.approved_at,
        "CLOSED": closing.closed_at,
    }

    offpath = None
    reached_rank = None
    if closing.status == "REVIEW_REQUIRED":
        reached_rank = 1
        offpath = {
            "code": "REVIEW_REQUIRED", "label": cash_closing_status_label("REVIEW_REQUIRED"),
            "timestamp": closing.submitted_at, "badge_class": cash_closing_badge_class("REVIEW_REQUIRED"),
        }
    elif closing.status == "REJECTED":
        reached_rank = 1
        offpath = {
            "code": "REJECTED", "label": cash_closing_status_label("REJECTED"),
            "timestamp": closing.reviewed_at, "badge_class": cash_closing_badge_class("REJECTED"),
        }
    elif closing.status == "REOPENED":
        reached_rank = 3 if (latest_reopen_event is not None and latest_reopen_event.prior_status == "CLOSED") else 2
        offpath = {
            "code": "REOPENED", "label": cash_closing_status_label("REOPENED"),
            "timestamp": latest_reopen_event.reopened_at if latest_reopen_event is not None else None,
            "badge_class": cash_closing_badge_class("REOPENED"),
        }

    return {"steps": _build_steps(step_order, labels, timestamps, closing.status, reached_rank), "offpath": offpath}
