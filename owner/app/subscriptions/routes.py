"""Subscription, renewal, and payment routes (Part L/M)."""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from flask import Blueprint, jsonify, redirect, render_template, request, url_for
from sqlalchemy import select

from app.auth.session import load_current_staff
from app.extensions import db_session
from app.models.customers import Customer
from app.models.catalog import Plan
from app.models.subscriptions import PaymentRecord, Subscription
from app.security.rbac import require_permission
from app.services.pagination import DEFAULT_PAGE_SIZE
from app.subscriptions import list_queries
from app.subscriptions.services import (
    VALID_TRANSITIONS,
    InvalidTransitionError,
    correct_payment,
    create_subscription,
    record_payment,
    record_renewal,
    transition_subscription,
)

bp = Blueprint("subscriptions", __name__, url_prefix="/subscriptions")


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
    allowed_transitions = sorted(VALID_TRANSITIONS.get(subscription.status, set()))
    return render_template("subscriptions/detail.html", subscription=subscription, allowed_transitions=allowed_transitions)


@bp.route("/<uuid:subscription_id>/transition", methods=["POST"])
@require_permission("subscriptions.update")
def transition(subscription_id):
    actor = load_current_staff()
    subscription = db_session.get(Subscription, subscription_id)
    if subscription is None:
        return jsonify({"error": "not_found"}), 404
    try:
        transition_subscription(subscription, request.form.get("to_status"), actor.id, request.form.get("reason"))
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
    payment = record_payment(
        {
            "customer_id": request.form.get("customer_id"),
            "subscription_id": request.form.get("subscription_id") or None,
            "amount": Decimal(request.form.get("amount", "0")),
            "currency": request.form.get("currency", "USD"),
            "method": request.form.get("method") or None,
            "payment_date": date.fromisoformat(request.form.get("payment_date")),
            "reference": request.form.get("reference") or None,
            "status": request.form.get("status", "PENDING"),
        },
        actor.id,
    )
    return redirect(url_for("subscriptions.detail", subscription_id=payment.subscription_id) if payment.subscription_id else url_for("dashboard.index"))


@bp.route("/payments/<uuid:payment_id>/correct", methods=["POST"])
@require_permission("payments.correct")
def correct_payment_route(payment_id):
    actor = load_current_staff()
    payment = db_session.get(PaymentRecord, payment_id)
    if payment is None:
        return jsonify({"error": "not_found"}), 404
    correct_payment(payment, request.form.get("status"), request.form.get("note", ""), actor.id)
    return redirect(url_for("subscriptions.detail", subscription_id=payment.subscription_id) if payment.subscription_id else url_for("dashboard.index"))
