"""Internal admin views for the Phase 6 Licensing & Activation Service (Part R).
Never shows private signing keys, full license keys, license hashes, peppers,
raw request secrets, device private keys, or any customer business/medical
data -- only the safe metadata this service itself ever touches."""
from __future__ import annotations

from flask import Blueprint, current_app, jsonify, redirect, render_template, request, url_for
from sqlalchemy import select

from app.auth.session import load_current_staff
from app.extensions import db_session
from app.licensing_service import device_identity, offline_policy as offline_policy_service
from app.licensing_service import signing as signing_service
from app.licensing_service.entitlements import resolve_entitlements
from app.licensing_service.health import internal_health
from app.models.installations import Installation
from app.models.licensing import License
from app.models.licensing_service import ActivationRequest, DevicePublicKey, OfflinePolicy, SigningKey
from app.security.rbac import require_permission, require_recent_auth
from datetime import datetime, timezone

bp = Blueprint("licensing_admin", __name__, url_prefix="/licensing-admin")


@bp.route("", methods=["GET"])
@require_permission("activation_service.view")
def status():
    health = internal_health(current_app.config["SIGNING_KEY_DIRECTORY"])
    external_enabled = current_app.config.get("EXTERNAL_API_ENABLED", False)
    return render_template("licensing_admin/status.html", health=health, external_enabled=external_enabled)


@bp.route("/signing-keys", methods=["GET"])
@require_permission("signing_keys.view_public_metadata")
def signing_keys():
    keys = db_session.execute(select(SigningKey).order_by(SigningKey.created_at.desc())).scalars().all()
    return render_template("licensing_admin/signing_keys.html", keys=keys)


@bp.route("/signing-keys/generate", methods=["POST"])
@require_permission("signing_keys.manage")
@require_recent_auth
def generate_signing_key():
    actor = load_current_staff()
    signing_service.generate_signing_key(current_app.config["SIGNING_KEY_DIRECTORY"], actor.id)
    return redirect(url_for("licensing_admin.signing_keys"))


@bp.route("/signing-keys/<key_id>/activate", methods=["POST"])
@require_permission("signing_keys.manage")
@require_recent_auth
def activate_signing_key(key_id):
    actor = load_current_staff()
    try:
        signing_service.activate_signing_key(current_app.config["SIGNING_KEY_DIRECTORY"], key_id, actor.id)
    except signing_service.SigningKeyError as exc:
        return jsonify({"error": str(exc)}), 400
    return redirect(url_for("licensing_admin.signing_keys"))


@bp.route("/signing-keys/rotate", methods=["POST"])
@require_permission("signing_keys.manage")
@require_recent_auth
def rotate_signing_key():
    actor = load_current_staff()
    signing_service.rotate_signing_key(current_app.config["SIGNING_KEY_DIRECTORY"], request.form.get("reason", "manual_rotation"), actor.id)
    return redirect(url_for("licensing_admin.signing_keys"))


@bp.route("/signing-keys/<key_id>/revoke", methods=["POST"])
@require_permission("signing_keys.manage")
@require_recent_auth
def revoke_signing_key(key_id):
    actor = load_current_staff()
    signing_service.revoke_signing_key(key_id, request.form.get("reason", "compromise_response"), actor.id)
    return redirect(url_for("licensing_admin.signing_keys"))


@bp.route("/requests", methods=["GET"])
@require_permission("activation_requests.view")
def requests_list():
    event_type_filter = request.args.get("event_type")
    stmt = select(ActivationRequest).order_by(ActivationRequest.created_at.desc()).limit(200)
    if event_type_filter:
        stmt = stmt.where(ActivationRequest.event_type == event_type_filter)
    rows = db_session.execute(stmt).scalars().all()
    return render_template("licensing_admin/requests.html", rows=rows, event_type_filter=event_type_filter)


@bp.route("/device-keys", methods=["GET"])
@require_permission("device_keys.view")
def device_keys():
    rows = db_session.execute(select(DevicePublicKey).order_by(DevicePublicKey.created_at.desc()).limit(200)).scalars().all()
    return render_template("licensing_admin/device_keys.html", rows=rows)


@bp.route("/device-keys/<uuid:device_key_id>/revoke", methods=["POST"])
@require_permission("device_keys.revoke")
@require_recent_auth
def revoke_device_key(device_key_id):
    actor = load_current_staff()
    device_key = db_session.get(DevicePublicKey, device_key_id)
    if device_key is None:
        return jsonify({"error": "not_found"}), 404
    device_identity.revoke_device_key(device_key, actor.id)
    return redirect(url_for("licensing_admin.device_keys"))


@bp.route("/installations/<uuid:installation_id>/replace-device", methods=["POST"])
@require_permission("installations.replace_device")
@require_recent_auth
def replace_device(installation_id):
    actor = load_current_staff()
    installation = db_session.get(Installation, installation_id)
    if installation is None:
        return jsonify({"error": "not_found"}), 404
    old_key = device_identity.get_active_device_key(installation_id)
    if old_key is None:
        return jsonify({"error": "no_active_device_key"}), 400
    device_identity.revoke_device_key(old_key, actor.id)
    return redirect(url_for("licensing_admin.device_keys"))


@bp.route("/offline-policies", methods=["GET"])
@require_permission("offline_policies.view")
def offline_policies():
    rows = db_session.execute(select(OfflinePolicy)).scalars().all()
    return render_template("licensing_admin/offline_policies.html", policies=rows)


@bp.route("/licenses/<uuid:license_id>/offline-policy", methods=["POST"])
@require_permission("offline_policies.manage")
@require_recent_auth
def assign_offline_policy(license_id):
    actor = load_current_staff()
    license_row = db_session.get(License, license_id)
    if license_row is None:
        return jsonify({"error": "not_found"}), 404
    try:
        offline_policy_service.assign_policy(license_row, request.form.get("policy_code"), actor.id)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return redirect(url_for("licensing.detail", license_id=license_id))


@bp.route("/licenses/<uuid:license_id>/entitlement-preview", methods=["GET"])
@require_permission("entitlement_resolution.preview")
def entitlement_preview(license_id):
    license_row = db_session.get(License, license_id)
    if license_row is None:
        return jsonify({"error": "not_found"}), 404
    resolved = resolve_entitlements(license_row, datetime.now(timezone.utc), include_source=True)
    return render_template("licensing_admin/entitlement_preview.html", license=license_row, resolved=resolved)
