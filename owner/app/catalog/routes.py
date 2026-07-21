"""Catalog routes: products, platforms, versions, plans, prices, add-ons, entitlements (Part I/J)."""
from __future__ import annotations

from datetime import date

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
    return render_template("catalog/plan_detail.html", plan=plan)


@bp.route("/plans/<uuid:plan_id>/prices", methods=["POST"])
@require_permission("catalog.manage_prices")
def add_price(plan_id):
    actor = load_current_staff()
    plan = db_session.get(Plan, plan_id)
    if plan is None:
        return jsonify({"error": "not_found"}), 404
    add_plan_price(
        plan,
        request.form.get("base_price"),
        request.form.get("currency", plan.currency),
        date.fromisoformat(request.form.get("effective_from")),
        actor.id,
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
