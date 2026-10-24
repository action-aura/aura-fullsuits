"""Catalog routes: products, platforms, versions, plans, prices, add-ons, entitlements (Part I/J)."""
from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation

from flask import Blueprint, jsonify, redirect, render_template, request, url_for
from sqlalchemy import select

from app.audit.services import record as audit_record
from app.auth.session import load_current_staff
from app.catalog.services import add_plan_price, create_plan, set_addon_availability
from app.extensions import db_session
from app.models.catalog import Addon, EntitlementDefinition, Plan, Platform, Product, ProductVersion
from app.security.rbac import require_permission

bp = Blueprint("catalog", __name__, url_prefix="/catalog")


@bp.route("", methods=["GET"])
@require_permission("catalog.view")
def index():
    products = db_session.execute(select(Product)).scalars().all()
    plans = db_session.execute(select(Plan)).scalars().all()
    addons = db_session.execute(select(Addon)).scalars().all()
    versions = db_session.execute(select(ProductVersion).order_by(ProductVersion.imported_at.desc())).scalars().all()
    entitlements = db_session.execute(select(EntitlementDefinition)).scalars().all()
    return render_template(
        "catalog/index.html", products=products, plans=plans, addons=addons, versions=versions, entitlements=entitlements
    )


@bp.route("/plans/new", methods=["GET"])
@require_permission("catalog.manage_plans")
def new_plan_form():
    products = db_session.execute(select(Product)).scalars().all()
    return render_template("catalog/plan_new.html", products=products)


@bp.route("/plans", methods=["POST"])
@require_permission("catalog.manage_plans")
def create_plan_route():
    actor = load_current_staff()
    plan = create_plan(
        {
            "plan_code": request.form.get("plan_code"),
            "product_id": request.form.get("product_id"),
            "name": request.form.get("name"),
            "billing_model": request.form.get("billing_model"),
            "currency": request.form.get("currency", "USD"),
            "included_device_count": int(request.form.get("included_device_count", "1")),
        },
        actor.id,
    )
    return redirect(url_for("catalog.plan_detail", plan_id=plan.id))


@bp.route("/plans/<uuid:plan_id>", methods=["GET"])
@require_permission("catalog.view")
def plan_detail(plan_id):
    plan = db_session.get(Plan, plan_id)
    if plan is None:
        return jsonify({"error": "not_found"}), 404
    current_price = next((p for p in plan.prices if p.effective_until is None), None)
    return render_template("catalog/plan_detail.html", plan=plan, current_price=current_price)


@bp.route("/plans/<uuid:plan_id>/prices", methods=["POST"])
@require_permission("catalog.manage_prices")
def add_price(plan_id):
    actor = load_current_staff()
    plan = db_session.get(Plan, plan_id)
    if plan is None:
        return jsonify({"error": "not_found"}), 404
    # Step 0 packages: the form now carries the setup fee (pre-filled with
    # the current one). Before, every new price row was written with
    # setup_fee 0, so a price change from this screen silently dropped
    # Package 1's 150 first-time fee. A form without the field keeps the
    # current fee (add_plan_price's own default) rather than zeroing it.
    raw_setup_fee = (request.form.get("setup_fee") or "").strip()
    setup_fee = None
    if raw_setup_fee:
        try:
            setup_fee = Decimal(raw_setup_fee)
        except InvalidOperation:
            setup_fee = Decimal("NaN")
        if not setup_fee.is_finite() or setup_fee < 0:
            return jsonify({"error": "invalid_setup_fee"}), 400
    add_plan_price(
        plan,
        request.form.get("base_price"),
        request.form.get("currency", plan.currency),
        date.fromisoformat(request.form.get("effective_from")),
        actor.id,
        setup_fee=setup_fee,
    )
    return redirect(url_for("catalog.plan_detail", plan_id=plan_id))


@bp.route("/addons/<uuid:addon_id>/availability", methods=["POST"])
@require_permission("catalog.manage_addons")
def update_addon_availability(addon_id):
    actor = load_current_staff()
    addon = db_session.get(Addon, addon_id)
    if addon is None:
        return jsonify({"error": "not_found"}), 404
    set_addon_availability(addon, request.form.get("status"), actor.id)
    return redirect(url_for("catalog.index"))
