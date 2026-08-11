"""Installation, device, and activation-event routes (Part P/Q). Registration
this phase is manual/staff-driven only (Phase 5 does not run a live product-side
activation client -- see owner-api-contract-preparation.md)."""
from __future__ import annotations

from flask import Blueprint, jsonify, redirect, render_template, request, url_for
from sqlalchemy import select

from app.auth.session import load_current_staff
from app.extensions import db_session
from app.installations import list_queries
from app.installations.services import (
    VALID_TRANSITIONS,
    InvalidInstallationTransitionError,
    register_installation,
    transition_installation,
)
from app.models.catalog import Platform
from app.models.installations import Installation
from app.models.licensing import License
from app.security.rbac import require_permission
from app.services.pagination import DEFAULT_PAGE_SIZE

bp = Blueprint("installations", __name__, url_prefix="/installations")


@bp.route("", methods=["GET"])
@require_permission("installations.view")
def list_installations():
    status_filter = request.args.get("status") or None
    search = request.args.get("q") or None
    sort = request.args.get("sort", "created_at")
    direction = request.args.get("dir", "desc")
    result = list_queries.list_installations(
        page=request.args.get("page", 1, type=int), page_size=DEFAULT_PAGE_SIZE,
        status=status_filter, search=search, sort=sort, direction=direction,
    )
    return render_template("installations/list.html", result=result, status_filter=status_filter, search=search)


@bp.route("/new", methods=["GET"])
@require_permission("installations.register")
def new_form():
    licenses = db_session.execute(select(License).where(License.status.in_(["ISSUED", "ACTIVE"]))).scalars().all()
    platforms = db_session.execute(select(Platform)).scalars().all()
    return render_template("installations/new.html", licenses=licenses, platforms=platforms)


@bp.route("", methods=["POST"])
@require_permission("installations.register")
def create():
    actor = load_current_staff()
    license_row = db_session.get(License, request.form.get("license_id"))
    if license_row is None:
        return jsonify({"error": "invalid_license"}), 400
    installation = register_installation(
        {
            "customer_id": license_row.customer_id,
            "subscription_id": license_row.subscription_id,
            "license_id": license_row.id,
            "product_id": license_row.product_id,
            "platform_id": request.form.get("platform_id"),
            "installation_label": request.form.get("installation_label") or None,
            "device_label": request.form.get("device_label") or None,
        },
        actor.id,
    )
    return redirect(url_for("installations.detail", installation_id=installation.id))


@bp.route("/<uuid:installation_id>", methods=["GET"])
@require_permission("installations.view")
def detail(installation_id):
    installation = db_session.get(Installation, installation_id)
    if installation is None:
        return jsonify({"error": "not_found"}), 404
    allowed_transitions = sorted(VALID_TRANSITIONS.get(installation.status, set()))
    return render_template("installations/detail.html", installation=installation, allowed_transitions=allowed_transitions)


@bp.route("/<uuid:installation_id>/transition", methods=["POST"])
@require_permission("installations.update")
def transition(installation_id):
    actor = load_current_staff()
    installation = db_session.get(Installation, installation_id)
    if installation is None:
        return jsonify({"error": "not_found"}), 404
    try:
        transition_installation(installation, request.form.get("to_status"), actor.id, request.form.get("reason"))
    except InvalidInstallationTransitionError as exc:
        return jsonify({"error": str(exc)}), 400
    return redirect(url_for("installations.detail", installation_id=installation_id))
