"""License routes (Part N/O). Issuance and full-key reveal require recent MFA
re-authentication (Part F)."""
from __future__ import annotations

import uuid

from flask import Blueprint, current_app, jsonify, redirect, render_template, request, url_for
from sqlalchemy import select

from app.auth.session import has_recent_auth, load_current_staff
from app.commercial_ops.device_slot_ops import DeviceSlotError
from app.commercial_ops.device_slot_ops import add_devices as add_license_devices
from app.extensions import db_session
from app.licensing import list_queries
from app.licensing.services import (
    VALID_TRANSITIONS,
    InvalidLicenseTransitionError,
    create_license,
    issue_license_key,
    replace_license,
    transition_license,
)
from app.models.customers import Customer
from app.models.licensing import License
from app.models.subscriptions import Subscription
from app.security.rbac import require_login, require_permission, require_recent_auth
from app.services.pagination import DEFAULT_PAGE_SIZE

bp = Blueprint("licensing", __name__, url_prefix="/licenses")


@bp.route("", methods=["GET"])
@require_permission("licenses.view")
def list_licenses():
    status_filter = request.args.get("status") or None
    search = request.args.get("q") or None
    sort = request.args.get("sort", "created_at")
    direction = request.args.get("dir", "desc")
    result = list_queries.list_licenses(
        page=request.args.get("page", 1, type=int), page_size=DEFAULT_PAGE_SIZE,
        status=status_filter, search=search, sort=sort, direction=direction,
    )
    return render_template("licensing/list.html", result=result, status_filter=status_filter, search=search)


@bp.route("/new", methods=["GET"])
@require_permission("licenses.create")
def new_form():
    subscriptions = db_session.execute(select(Subscription)).scalars().all()
    return render_template("licensing/new.html", subscriptions=subscriptions)


@bp.route("", methods=["POST"])
@require_permission("licenses.create")
def create():
    actor = load_current_staff()
    subscription = db_session.get(Subscription, request.form.get("subscription_id"))
    if subscription is None:
        return jsonify({"error": "invalid_subscription"}), 400
    license_row = create_license(
        {
            "customer_id": subscription.customer_id,
            "subscription_id": subscription.id,
            "product_id": subscription.product_id,
            "plan_id": subscription.plan_id,
            "allowed_platforms": request.form.get("allowed_platforms", "WINDOWS,ANDROID"),
            "device_limit": int(request.form.get("device_limit", "1")),
        },
        actor.id,
    )
    return redirect(url_for("licensing.detail", license_id=license_row.id))


@bp.route("/<uuid:license_id>", methods=["GET"])
@require_permission("licenses.view")
def detail(license_id):
    license_row = db_session.get(License, license_id)
    if license_row is None:
        return jsonify({"error": "not_found"}), 404
    allowed_transitions = sorted(VALID_TRANSITIONS.get(license_row.status, set()))
    # revealed_key is only ever populated inline by issue() below -- reloading this page never re-shows a full key.
    return render_template(
        "licensing/detail.html", license=license_row, revealed_key=None,
        allowed_transitions=allowed_transitions, issue_idempotency_key=str(uuid.uuid4()),
        add_devices_idempotency_key=str(uuid.uuid4()),
        recent_auth_ok=has_recent_auth(),
    )


@bp.route("/<uuid:license_id>/issue", methods=["POST"])
@require_permission("licenses.issue")
@require_recent_auth
def issue(license_id):
    actor = load_current_staff()
    license_row = db_session.get(License, license_id)
    if license_row is None:
        return jsonify({"error": "not_found"}), 404
    idempotency_key = request.form.get("idempotency_key") or request.headers.get("Idempotency-Key")
    if not idempotency_key:
        return jsonify({"error": "idempotency_key_required"}), 400
    try:
        license_row, full_key = issue_license_key(license_row, current_app.config["LICENSE_PEPPER"], idempotency_key, actor.id)
    except InvalidLicenseTransitionError as exc:
        return jsonify({"error": str(exc)}), 400
    allowed_transitions = sorted(VALID_TRANSITIONS.get(license_row.status, set()))
    # full_key is shown exactly once, in this response only -- reloading /licenses/<id> never shows it again.
    return render_template(
        "licensing/detail.html", license=license_row, revealed_key=full_key,
        allowed_transitions=allowed_transitions, recent_auth_ok=has_recent_auth(),
    )


@bp.route("/<uuid:license_id>/add-devices", methods=["POST"])
@require_permission("subscriptions.renew")
@require_recent_auth
def add_devices_route(license_id):
    # Same permission as the renewal pipeline (`subscriptions.renew`) --
    # this is a fast-path alternative for the exact same commercial field
    # (`subscription.device_allowance`), not a new authority.
    actor = load_current_staff()
    license_row = db_session.get(License, license_id)
    if license_row is None:
        return jsonify({"error": "not_found"}), 404
    try:
        additional_devices = int(request.form["additional_devices"])
    except (KeyError, ValueError):
        return jsonify({"error": "invalid_additional_devices"}), 400
    idempotency_key = request.form.get("idempotency_key") or request.headers.get("Idempotency-Key")
    try:
        add_license_devices(
            license_row, additional_devices=additional_devices, reason=request.form.get("reason", ""),
            actor_staff_user_id=actor.id, idempotency_key=idempotency_key,
        )
    except DeviceSlotError as exc:
        return jsonify({"error": str(exc)}), 400
    return redirect(url_for("licensing.detail", license_id=license_id))


@bp.route("/<uuid:license_id>/transition", methods=["POST"])
@require_login
@require_recent_auth
def transition(license_id):
    actor = load_current_staff()
    license_row = db_session.get(License, license_id)
    if license_row is None:
        return jsonify({"error": "not_found"}), 404
    to_status = request.form.get("to_status")
    # ACTIVE requires licenses.reactivate specifically (Part Q) -- reactivating
    # a suspended license is a distinct, more sensitive action than the
    # suspend/revoke path and must not be reachable by whoever merely holds
    # licenses.suspend.
    permission_by_target = {"REVOKED": "licenses.revoke", "SUSPENDED": "licenses.suspend", "ACTIVE": "licenses.reactivate"}
    from app.security.rbac import get_staff_permission_codes

    if permission_by_target.get(to_status) not in get_staff_permission_codes(actor):
        return jsonify({"error": "forbidden"}), 403
    try:
        transition_license(license_row, to_status, actor.id, request.form.get("reason"))
    except InvalidLicenseTransitionError as exc:
        return jsonify({"error": str(exc)}), 400
    return redirect(url_for("licensing.detail", license_id=license_id))


@bp.route("/<uuid:license_id>/replace", methods=["POST"])
@require_permission("licenses.replace")
@require_recent_auth
def replace(license_id):
    actor = load_current_staff()
    license_row = db_session.get(License, license_id)
    if license_row is None:
        return jsonify({"error": "not_found"}), 404
    try:
        new_license = replace_license(license_row, actor.id)
    except InvalidLicenseTransitionError as exc:
        return jsonify({"error": str(exc)}), 400
    return redirect(url_for("licensing.detail", license_id=new_license.id))
