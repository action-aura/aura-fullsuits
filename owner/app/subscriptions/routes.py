"""Subscription, renewal, and payment routes (Part L/M)."""
from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation

from flask import Blueprint, flash, jsonify, redirect, render_template, request, url_for
from sqlalchemy import select

from app.auth.session import has_recent_auth, load_current_staff
from app.commercial_sales.packages import check_payment_amount, next_payment_quote
from app.extensions import db_session
from app.i18n_labels import localize_payment_correction_error, payment_due_breakdown, payment_due_warning
from app.models.customers import Customer
from app.models.catalog import Plan
from app.models.subscriptions import PaymentRecord, Subscription
from app.security.rbac import get_staff_permission_codes, require_permission
from app.services.pagination import DEFAULT_PAGE_SIZE
from app.subscriptions import list_queries
from app.subscriptions.services import (
    VALID_TRANSITIONS,
    InvalidTransitionError,
    PaymentAllocatedError,
    PaymentDateInFutureError,
    PaymentSelfVerificationError,
    correct_payment,
    create_subscription,
    record_payment,
    record_renewal,
    transition_subscription,
)

bp = Blueprint("subscriptions", __name__, url_prefix="/subscriptions")

# 2026-10-31: SALES no longer holds subscriptions.update at all (it moved to
# SALES_MANAGER -- seed_data.py); the restriction below still matters because
# the manager and any custom role holding it are not meant to suspend/cancel.
# OWNER-SEC item 3 (review D SEC-03): subscriptions.update -- which SALES held
# so it could sell -- was enough to push a paying shop's subscription into a
# state that makes its till read-only at the next check-in. Transitions INTO
# these states now need the dedicated capability (held only by SUPER_ADMIN
# today) AND recent MFA, the same shape as licensing.transition's
# permission_by_target. Everything SALES needs to sell (DRAFT/PILOT -> ACTIVE,
# PILOT -> COMPLETED, PAST_DUE/SUSPENDED -> ACTIVE, ACTIVE -> PAST_DUE) is
# deliberately NOT listed. This is a ROUTE-layer rule: the system paths that
# legitimately suspend/expire (expiry scan, refund, pilot lifecycle) call
# transition_subscription() directly and are unaffected.
RESTRICTED_TARGET_PERMISSION = {
    "SUSPENDED": "subscriptions.suspend",
    "CANCELLED": "subscriptions.cancel",
    "EXPIRED": "subscriptions.cancel",
}


def _required_permission(from_status: str, to_status: str | None) -> str | None:
    # Cancelling a DRAFT discards something never sold: nothing live to cut off.
    if from_status == "DRAFT" and to_status == "CANCELLED":
        return None
    return RESTRICTED_TARGET_PERMISSION.get(to_status)


@bp.route("", methods=["GET"])
@require_permission("subscriptions.view")
def list_subscriptions():
    status_filter = request.args.get("status") or None
    search = request.args.get("q") or None
    sort = request.args.get("sort", "created_at")
    direction = request.args.get("dir", "desc")
    result = list_queries.list_subscriptions(
        page=request.args.get("page", 1, type=int), page_size=DEFAULT_PAGE_SIZE,
        status=status_filter, search=search, sort=sort, direction=direction,
    )
    return render_template("subscriptions/list.html", result=result, status_filter=status_filter, search=search)


@bp.route("/new", methods=["GET"])
@require_permission("subscriptions.create")
def new_form():
    customers = db_session.execute(select(Customer).where(Customer.archived_at.is_(None))).scalars().all()
    plans = db_session.execute(select(Plan)).scalars().all()
    return render_template("subscriptions/new.html", customers=customers, plans=plans)


@bp.route("", methods=["POST"])
@require_permission("subscriptions.create")
def create():
    actor = load_current_staff()
    plan = db_session.get(Plan, request.form.get("plan_id"))
    if plan is None:
        return jsonify({"error": "invalid_plan"}), 400
    subscription = create_subscription(
        {
            "customer_id": request.form.get("customer_id"),
            "product_id": plan.product_id,
            "plan_id": plan.id,
            "billing_cycle": request.form.get("billing_cycle") or None,
            "device_allowance": int(request.form["device_allowance"]) if request.form.get("device_allowance") else None,
        },
        actor.id,
    )
    return redirect(url_for("subscriptions.detail", subscription_id=subscription.id))


@bp.route("/<uuid:subscription_id>", methods=["GET"])
@require_permission("subscriptions.view")
def detail(subscription_id):
    subscription = db_session.get(Subscription, subscription_id)
    if subscription is None:
        return jsonify({"error": "not_found"}), 404
    codes = get_staff_permission_codes(load_current_staff())
    allowed_transitions = sorted(
        t for t in VALID_TRANSITIONS.get(subscription.status, set())
        if _required_permission(subscription.status, t) in (None, *codes)
    )
    # This page could RECORD a payment but never showed one, so
    # correct_payment_route -- complete and permission-gated -- was unreachable
    # for want of a payment id to act on (test_route_reachability.py's
    # KNOWN_GAPS). Newest first: a correction is nearly always to the payment
    # just entered.
    payments = db_session.execute(
        select(PaymentRecord)
        .where(PaymentRecord.subscription_id == subscription.id)
        .order_by(PaymentRecord.payment_date.desc(), PaymentRecord.created_at.desc())
    ).scalars().all()
    # Verifier finding (2026-10-30, HIGH): a monthly package's month-13 payment
    # carries the 100 JOD yearly activation, and nothing used to show staff what
    # was due -- they typed an amount by hand and the 100 was never charged. The
    # form below is now pre-filled with the amount due and says what it is made
    # of (packages.next_payment_quote). None for plans without that activation.
    due = next_payment_quote(subscription)
    return render_template(
        "subscriptions/detail.html", subscription=subscription,
        allowed_transitions=allowed_transitions, payments=payments,
        due=due, due_breakdown=payment_due_breakdown(due) if due is not None else None,
    )


@bp.route("/<uuid:subscription_id>/transition", methods=["POST"])
@require_permission("subscriptions.update")
def transition(subscription_id):
    actor = load_current_staff()
    subscription = db_session.get(Subscription, subscription_id)
    if subscription is None:
        return jsonify({"error": "not_found"}), 404
    to_status = request.form.get("to_status")
    needed = _required_permission(subscription.status, to_status)
    if needed is not None:
        if needed not in get_staff_permission_codes(actor):
            return jsonify({"error": "forbidden"}), 403
        if not has_recent_auth():
            return redirect(url_for("auth.reauth_form", next=url_for("subscriptions.detail", subscription_id=subscription_id)))
    try:
        transition_subscription(subscription, to_status, actor.id, request.form.get("reason"))
    except InvalidTransitionError as exc:
        return jsonify({"error": str(exc)}), 400
    return redirect(url_for("subscriptions.detail", subscription_id=subscription_id))


@bp.route("/<uuid:subscription_id>/renew", methods=["POST"])
@require_permission("subscriptions.renew")
def renew(subscription_id):
    actor = load_current_staff()
    subscription = db_session.get(Subscription, subscription_id)
    if subscription is None:
        return jsonify({"error": "not_found"}), 404
    record_renewal(
        subscription,
        date.fromisoformat(request.form.get("new_end_date")),
        request.form.get("new_plan_id") or None,
        request.form.get("reason"),
        actor.id,
    )
    return redirect(url_for("subscriptions.detail", subscription_id=subscription_id))


@bp.route("/payments", methods=["POST"])
@require_permission("payments.create")
def create_payment():
    actor = load_current_staff()
    # `status` still comes from the form (FAILED/REFUNDED/VOIDED records of
    # past events are legitimate), but record_payment now refuses CONFIRMED:
    # this route's gate is payments.create, which SALES holds, and it used to
    # let a salesperson record money as confirmed with no checker at all.
    #
    # Amount and date are parsed BEFORE that call (Step 0 fix round 2): a
    # mistyped amount raised decimal.InvalidOperation and a missing date a
    # TypeError -- neither is a ValueError, so both escaped as a 500. And
    # "NaN" parses as a Decimal that Postgres NUMERIC happily stores, which
    # would have recorded a payment of NaN. Not-a-finite-number is not an
    # amount.
    try:
        amount = Decimal(request.form.get("amount", "0"))
    except InvalidOperation:
        amount = None
    if amount is None or not amount.is_finite():
        return jsonify({"error": "invalid_amount", "message": "Amount must be a number, e.g. 75.00."}), 400
    try:
        payment_date = date.fromisoformat(request.form.get("payment_date") or "")
    except ValueError:
        return jsonify({"error": "invalid_payment_date", "message": "Payment date must be a date, e.g. 2026-10-24."}), 400
    subscription_id = request.form.get("subscription_id") or None
    subscription = db_session.get(Subscription, subscription_id) if subscription_id else None
    # A different amount from the one due is allowed (a part payment, two months
    # at once) -- never refused. It is flagged on screen and written into this
    # payment's audit entry so the gap can be found later.
    check = check_payment_amount(subscription, amount, request.form.get("currency", "USD"), payment_date=payment_date)
    try:
        payment = record_payment(
            {
                "customer_id": request.form.get("customer_id"),
                "subscription_id": subscription_id,
                "amount": amount,
                "currency": request.form.get("currency", "USD"),
                "method": request.form.get("method") or None,
                "payment_date": payment_date,
                "reference": request.form.get("reference") or None,
                "status": request.form.get("status", "PENDING"),
            },
            actor.id,
            audit_extra={"due_check": check.audit_state()} if check is not None else None,
        )
    except PaymentDateInFutureError as exc:
        # Step 0 fix round: a typed future date could place a payment in a
        # renewal period that has not started (ladder.py::_renewal_period).
        return jsonify({"error": "payment_date_in_future", "message": str(exc)}), 400
    except ValueError as exc:  # includes PaymentSelfVerificationError
        return jsonify({"error": str(exc)}), 400
    if check is not None and not check.matches:
        flash(payment_due_warning(check), "error")
    return redirect(url_for("subscriptions.detail", subscription_id=payment.subscription_id) if payment.subscription_id else url_for("dashboard.index"))


@bp.route("/payments/<uuid:payment_id>/correct", methods=["POST"])
@require_permission("payments.correct")
def correct_payment_route(payment_id):
    actor = load_current_staff()
    payment = db_session.get(PaymentRecord, payment_id)
    if payment is None:
        return jsonify({"error": "not_found"}), 404
    new_status = request.form.get("status")
    # Confirming is its own authority (payments.confirm, the one
    # commercial_sales' confirm routes gate on), not a kind of correcting.
    # Without this, any role given payments.correct alone would have a side
    # door around payments.confirm. FINANCE holds both today, so this changes
    # nothing for a seeded role -- it closes the shape, not a live grant.
    if new_status == "CONFIRMED" and "payments.confirm" not in get_staff_permission_codes(actor):
        return jsonify({"error": "forbidden"}), 403
    try:
        correct_payment(payment, new_status, request.form.get("note", ""), actor.id)
    except PaymentSelfVerificationError as exc:
        # The recorder trying to confirm their own payment: refused, same
        # rule (and same 403 shape) as commercial_sales' confirm route.
        return jsonify({"error": "self_verification_forbidden", "message": str(exc)}), 403
    except PaymentAllocatedError as exc:
        # Confirmed money with a live allocation (Step 0 fix round 4): 409,
        # the record's current state forbids it -- the translated message
        # names the way out (reverse the allocation first).
        return jsonify({"error": "payment_allocated", "message": localize_payment_correction_error(exc.code)}), 409
    except ValueError as exc:
        # An unknown status used to escape as an unhandled ValueError (500).
        return jsonify({"error": str(exc)}), 400
    return redirect(url_for("subscriptions.detail", subscription_id=payment.subscription_id) if payment.subscription_id else url_for("dashboard.index"))
