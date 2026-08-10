"""UI modernization Stage D -- shared status-badge-color and status-step
(timeline) presentation helpers for the five commercial-sales detail
screens (Quote/Order/Invoice/Payment/Refund) and their list screens.

Non-Negotiable: "the UI must never suggest an invalid state transition...
the backend state machine remains authoritative." This module NEVER
decides whether a transition is valid and never writes anything -- it only
visualizes the entity's REAL, already-persisted status against:

- the real status vocabularies (QUOTE_STATUSES/SALES_ORDER_STATUSES/
  INVOICE_STATUSES/REFUND_STATUSES in app.models.commercial_sales,
  PAYMENT_STATUSES in app.subscriptions.services), and
- the real transition graphs (QUOTE_TRANSITIONS/SALES_ORDER_TRANSITIONS/
  INVOICE_TRANSITIONS/REFUND_TRANSITIONS in app.commercial_sales.errors),
  used here only to prove which "happy path" steps a real off-path
  terminal status (CANCELLED/REJECTED/EXPIRED/VOID/REFUNDED/
  PARTIALLY_REFUNDED/FAILED) could only have been reached through -- never
  to invent a timestamp or a step that didn't really happen.

Badge-color mapping is centralized here to fix a real, disclosed
inconsistency found while migrating these screens: every *_list.html
template already computed a real success/danger/pending badge class
per status inline, but three of the five *_detail.html templates
(quote_detail.html, order_detail.html, invoice_detail.html) rendered the
header's own status with a bare, uncolored `<span class="badge">` --
losing the exact same real color information the list screen for the
same entity already showed one click away, and payment_detail.html's own
inline ternary was missing the 'danger' (FAILED/VOIDED) branch its own
list screen already had. Every function below reproduces the *_list.html
template's existing color mapping byte-for-byte -- no color assignment is
new or changed, only shared instead of duplicated (and, for the three
detail pages that had none, applied for the first time)."""
from __future__ import annotations

from datetime import datetime

from app.i18n_labels import (
    commercial_approval_status_label,
    commercial_invoice_status_label,
    commercial_refund_status_label,
    commission_entry_status_label,
    commission_payout_batch_status_label,
    payment_status_label,
    quote_status_label,
    sales_order_status_label,
)

# --------------------------------------------------------------- Badges --

def quote_badge_class(status: str) -> str:
    """Mirrors quotes_list.html's existing ternary exactly."""
    if status == "ACCEPTED":
        return "success"
    if status in ("REJECTED", "CANCELLED", "EXPIRED"):
        return "danger"
    return "pending"


def order_badge_class(status: str) -> str:
    """Mirrors orders_list.html's existing ternary exactly."""
    if status in ("CONFIRMED", "FULFILLED"):
        return "success"
    if status == "CANCELLED":
        return "danger"
    return "pending"


def invoice_badge_class(status: str) -> str:
    """Mirrors invoices_list.html's existing ternary exactly."""
    if status == "PAID":
        return "success"
    if status == "VOID":
        return "danger"
    return "pending"


def payment_badge_class(status: str) -> str:
    """Mirrors payments_list.html's existing ternary exactly (the real,
    complete FAILED/VOIDED danger branch payment_detail.html's own inline
    ternary was missing before this pass)."""
    if status == "CONFIRMED":
        return "success"
    if status in ("FAILED", "VOIDED"):
        return "danger"
    return "pending"


def refund_badge_class(status: str) -> str:
    """Mirrors refunds_list.html / refund_detail.html's existing ternary
    exactly (the two were already consistent with each other)."""
    if status == "PAID":
        return "success"
    if status == "VOID":
        return "danger"
    return "pending"


def commission_entry_badge_class(status: str) -> str:
    """Mirrors commissions_list.html's existing ternary exactly."""
    if status in ("APPROVED", "PAID"):
        return "success"
    if status in ("REVERSED", "CANCELLED"):
        return "danger"
    return "pending"


def payout_batch_badge_class(status: str) -> str:
    """Mirrors payout_batches_list.html's existing ternary exactly."""
    if status in ("APPROVED", "PAID"):
        return "success"
    return "pending"


def approval_badge_class(status: str) -> str:
    """Mirrors quote_detail.html's existing pricing-approval ternary exactly."""
    if status == "APPROVED":
        return "success"
    if status in ("REJECTED", "CANCELLED", "EXPIRED"):
        return "danger"
    return "pending"


# ---------------------------------------------------------- Step builder --

def _build_steps(step_order: list[str], labels: dict[str, str], timestamps: dict[str, datetime | None], current_status: str, offpath_reached_rank: int | None) -> list[dict]:
    """Shared, entity-agnostic step-state computation.

    step_order: the real happy-path status codes, in display order.
    timestamps: {code: real datetime column value or None} -- a non-None
      value is only ever present here if that transition genuinely
      happened (never fabricated).
    current_status: the entity's real, current status.
    offpath_reached_rank: None if current_status is itself in step_order;
      otherwise the highest step_order index PROVEN reached (by the real
      transition graph) before the entity left the happy path -- steps up
      to and including that index render "done", the rest "upcoming"
      (never "current": the off-path terminal is the real current state,
      rendered as a separate chip by the caller).
    """
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


def quote_timeline(quote) -> dict:
    """QUOTE_TRANSITIONS: DRAFT->{SENT,CANCELLED}; SENT->{ACCEPTED,REJECTED,
    EXPIRED,CANCELLED}. REJECTED/EXPIRED are only ever reachable from SENT
    (so sent_at is always real/set when status is one of those); CANCELLED
    is reachable from DRAFT or SENT (reached rank depends on whether
    sent_at is actually set)."""
    step_order = ["DRAFT", "SENT", "ACCEPTED"]
    labels = {code: quote_status_label(code) for code in step_order}
    timestamps = {"DRAFT": quote.created_at, "SENT": quote.sent_at, "ACCEPTED": quote.accepted_at}

    offpath = None
    reached_rank = None
    if quote.status == "REJECTED":
        reached_rank = 1
        offpath = {"code": "REJECTED", "label": quote_status_label("REJECTED"), "timestamp": quote.rejected_at, "badge_class": quote_badge_class("REJECTED")}
    elif quote.status == "EXPIRED":
        reached_rank = 1
        # No dedicated expired_at column (app.models.commercial_sales.Quote)
        # -- expire_stale_quotes() only sets status+version, so the real,
        # closest-available timestamp is the row's own updated_at.
        offpath = {"code": "EXPIRED", "label": quote_status_label("EXPIRED"), "timestamp": quote.updated_at, "badge_class": quote_badge_class("EXPIRED")}
    elif quote.status == "CANCELLED":
        reached_rank = 1 if quote.sent_at else 0
        offpath = {"code": "CANCELLED", "label": quote_status_label("CANCELLED"), "timestamp": quote.cancelled_at, "badge_class": quote_badge_class("CANCELLED")}

    return {"steps": _build_steps(step_order, labels, timestamps, quote.status, reached_rank), "offpath": offpath}


def order_timeline(order) -> dict:
    """SALES_ORDER_TRANSITIONS: DRAFT->{CONFIRMED,CANCELLED};
    CONFIRMED->{FULFILLED,CANCELLED}. CANCELLED reachable from DRAFT or
    CONFIRMED."""
    step_order = ["DRAFT", "CONFIRMED", "FULFILLED"]
    labels = {code: sales_order_status_label(code) for code in step_order}
    timestamps = {"DRAFT": order.created_at, "CONFIRMED": order.confirmed_at, "FULFILLED": order.fulfilled_at}

    offpath = None
    reached_rank = None
    if order.status == "CANCELLED":
        reached_rank = 1 if order.confirmed_at else 0
        offpath = {"code": "CANCELLED", "label": sales_order_status_label("CANCELLED"), "timestamp": order.cancelled_at, "badge_class": order_badge_class("CANCELLED")}

    return {"steps": _build_steps(step_order, labels, timestamps, order.status, reached_rank), "offpath": offpath}


def invoice_timeline(invoice) -> dict:
    """INVOICE_TRANSITIONS: DRAFT->{ISSUED,VOID}; ISSUED->{VOID,
    PARTIALLY_PAID,PAID}; PARTIALLY_PAID->{PAID,PARTIALLY_REFUNDED};
    PAID->{PARTIALLY_REFUNDED,REFUNDED}. Neither PARTIALLY_PAID nor PAID
    has a dedicated "reached at" column (both are set by
    app.commercial_sales.allocation.allocate_payment, which only bumps
    status+version) -- no per-step timestamp is shown for them (never
    fabricated from updated_at, which would be overwritten by any later
    transition and so would misrepresent an earlier one).

    VOID is reachable from DRAFT or ISSUED (never PARTIALLY_PAID/PAID --
    void_invoice() itself rejects once any confirmed allocation exists).
    PARTIALLY_REFUNDED is reachable from PARTIALLY_PAID or PAID; REFUNDED
    only from PAID -- so REFUNDED proves the PAID rank was reached,
    PARTIALLY_REFUNDED proves at least the PARTIALLY_PAID rank."""
    step_order = ["DRAFT", "ISSUED", "PARTIALLY_PAID", "PAID"]
    labels = {code: commercial_invoice_status_label(code) for code in step_order}
    timestamps = {"DRAFT": invoice.created_at, "ISSUED": invoice.issued_at, "PARTIALLY_PAID": None, "PAID": None}

    offpath = None
    reached_rank = None
    if invoice.status == "VOID":
        reached_rank = 1 if invoice.issued_at else 0
        # No dedicated voided_at column on CommercialInvoice -- updated_at
        # is the real, closest-available "as of" timestamp (VOID is a real
        # terminal state; nothing transitions out of it afterward, so
        # updated_at cannot later be overwritten by a subsequent step the
        # way it could for a non-terminal status).
        offpath = {"code": "VOID", "label": commercial_invoice_status_label("VOID"), "timestamp": invoice.updated_at, "badge_class": invoice_badge_class("VOID")}
    elif invoice.status == "PARTIALLY_REFUNDED":
        reached_rank = 2
        offpath = {"code": "PARTIALLY_REFUNDED", "label": commercial_invoice_status_label("PARTIALLY_REFUNDED"), "timestamp": invoice.updated_at, "badge_class": invoice_badge_class("PARTIALLY_REFUNDED")}
    elif invoice.status == "REFUNDED":
        reached_rank = 3
        offpath = {"code": "REFUNDED", "label": commercial_invoice_status_label("REFUNDED"), "timestamp": invoice.updated_at, "badge_class": invoice_badge_class("REFUNDED")}

    return {"steps": _build_steps(step_order, labels, timestamps, invoice.status, reached_rank), "offpath": offpath}


def refund_timeline(refund) -> dict:
    """REFUND_TRANSITIONS: DRAFT->{APPROVED,VOID}; APPROVED->{PAID,VOID}.
    VOID reachable from DRAFT or APPROVED."""
    step_order = ["DRAFT", "APPROVED", "PAID"]
    labels = {code: commercial_refund_status_label(code) for code in step_order}
    timestamps = {"DRAFT": refund.created_at, "APPROVED": refund.approved_at, "PAID": refund.paid_at}

    offpath = None
    reached_rank = None
    if refund.status == "VOID":
        reached_rank = 1 if refund.approved_at else 0
        offpath = {"code": "VOID", "label": commercial_refund_status_label("VOID"), "timestamp": refund.voided_at, "badge_class": refund_badge_class("VOID")}

    return {"steps": _build_steps(step_order, labels, timestamps, refund.status, reached_rank), "offpath": offpath}


def payment_timeline(payment) -> dict:
    """No dedicated *_TRANSITIONS dict exists for PaymentRecord (it predates
    the Phase 9.5D commercial-sales transition-table convention) -- the
    real transitions are the ones app.commercial_sales.payments.py's own
    functions enforce directly: confirm_payment() only allows
    PENDING->CONFIRMED, reject_payment() only allows PENDING->FAILED.
    REFUNDED/VOIDED are real values in app.subscriptions.services.
    PAYMENT_STATUSES but, verified by reading every caller, no current
    commercial_sales_web route ever sets either -- so they are handled as
    an honest "unmapped off-path" case below (nothing assumed reached)
    rather than guessed at, should a future code path ever reach them.
    CONFIRMED has no dedicated "confirmed at" column on PaymentRecord
    either (only status + verified_by_staff_user_id + a correction-history
    row) -- no timestamp is fabricated for that step."""
    step_order = ["PENDING", "CONFIRMED"]
    labels = {code: payment_status_label(code) for code in step_order}
    timestamps = {"PENDING": payment.created_at, "CONFIRMED": None}

    offpath = None
    reached_rank = None
    if payment.status == "FAILED":
        reached_rank = 0
        offpath = {"code": "FAILED", "label": payment_status_label("FAILED"), "timestamp": payment.updated_at, "badge_class": payment_badge_class("FAILED")}
    elif payment.status in ("REFUNDED", "VOIDED"):
        # Unmapped by any real, currently-reachable web-route transition
        # (see docstring) -- reached_rank stays None (nothing assumed).
        offpath = {"code": payment.status, "label": payment_status_label(payment.status), "timestamp": payment.updated_at, "badge_class": payment_badge_class(payment.status)}

    return {"steps": _build_steps(step_order, labels, timestamps, payment.status, reached_rank), "offpath": offpath}
