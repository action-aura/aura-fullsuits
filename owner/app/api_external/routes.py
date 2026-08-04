"""External Licensing & Activation API (Part B). Versioned, isolated from every
internal staff blueprint. Only ever registered when OWNER_EXTERNAL_API_ENABLED
is true -- see app/__init__.py's conditional import, unchanged pattern from
Phase 5's app/api/routes.py."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from flask import Blueprint, current_app, jsonify, request

from app.extensions import db_session
from app.licensing_service import ratelimit
from app.licensing_service.activation import ActivationRejected, process_activation
from app.licensing_service.checkin import CheckInRejected, process_checkin
from app.licensing_service.deactivation import DeactivationRejected, process_deactivation
from app.licensing_service.health import is_service_ready, public_service_info
from app.licensing_service.reason_codes import to_public_reason_code
from app.licensing_service.signing import export_signed_keyset_manifest
from app.releases.distribution import DownloadAuthorizationRejected, authorize_download, fetch_download

bp = Blueprint("api_external_licensing", __name__, url_prefix="/api/licensing/v1")


def _service_config() -> dict:
    return {
        "timestamp_skew_seconds": current_app.config["ACTIVATION_TIMESTAMP_SKEW_SECONDS"],
        "nonce_ttl_seconds": current_app.config["NONCE_TTL_SECONDS"],
        "assertion_ttl_seconds": current_app.config["ASSERTION_TTL_SECONDS"],
        "license_pepper": current_app.config["LICENSE_PEPPER"],
        "signing_key_directory": current_app.config["SIGNING_KEY_DIRECTORY"],
        "download_token_ttl_seconds": current_app.config["RELEASE_DOWNLOAD_TOKEN_TTL_SECONDS"],
    }


def _error_response(reason_code: str, http_status: int, correlation_id: str | None = None) -> tuple:
    public_code = to_public_reason_code(reason_code)
    body = {
        "contract_version": "v1",
        "response_id": str(uuid.uuid4()),
        "correlation_id": correlation_id,
        "server_timestamp": datetime.now(timezone.utc).isoformat(),
        "result": "FAILURE",
        "reason_code": public_code,
        "decision": "REJECTED",
        "retry_guidance": "safe_to_retry_with_backoff" if public_code in ("RATE_LIMITED", "SERVICE_TEMPORARILY_UNAVAILABLE") else "do_not_retry_without_correction",
    }
    return jsonify(body), http_status


@bp.before_request
def _strict_content_type():
    if request.method == "POST" and request.content_type and not request.content_type.startswith("application/json"):
        return _error_response("INVALID_REQUEST", 415)


@bp.before_request
def _bounded_payload():
    # Phase 9R M6/M8: this blueprint's real bound is OWNER_MAX_REQUEST_BYTES
    # (64KB by default) -- but app.config["MAX_CONTENT_LENGTH"] is a Flask-
    # global setting, and Phase 9R raised it to accommodate expense
    # attachment uploads (10MB) elsewhere in the app. Without this check,
    # the licensing API would silently inherit that much larger global
    # ceiling instead of its own tighter, deliberately small limit --
    # enforced here explicitly, independent of whatever the global cap is.
    limit = current_app.config["MAX_REQUEST_BYTES"]
    if request.content_length is not None and request.content_length > limit:
        return _error_response("PAYLOAD_TOO_LARGE", 413)


@bp.errorhandler(413)
def _payload_too_large(_exc):
    return _error_response("PAYLOAD_TOO_LARGE", 413)


def _get_json_body() -> dict | None:
    try:
        return request.get_json(force=False, silent=False)
    except Exception:
        return None


def _client_bucket() -> str:
    return request.remote_addr or "unknown"


@bp.route("/activations", methods=["POST"])
def activations():
    ready, reason = is_service_ready(current_app.config["SIGNING_KEY_DIRECTORY"], replay_protection_required=current_app.config["REPLAY_PROTECTION_REQUIRED"])
    if not ready:
        return _error_response(reason, 503)

    try:
        ratelimit.check_and_increment("activation", _client_bucket())
    except ratelimit.RateLimitExceeded as exc:
        resp, status = _error_response("RATE_LIMITED", 429)
        resp.headers["Retry-After"] = str(exc.retry_after_seconds)
        return resp, status

    body = _get_json_body()
    if body is None:
        return _error_response("INVALID_REQUEST", 400)
    correlation_id = body.get("correlation_id") if isinstance(body, dict) else None

    try:
        response = process_activation(body, source_ip=request.remote_addr, config=_service_config())
        return jsonify(response), 200
    except ActivationRejected as exc:
        db_session.rollback()
        if exc.internal_reason_code == "INVALID_SIGNATURE":
            try:
                ratelimit.check_and_increment("activation_invalid_signature", _client_bucket())
            except ratelimit.RateLimitExceeded:
                pass
        if exc.internal_reason_code in ("LICENSE_NOT_FOUND", "LICENSE_SUSPENDED", "LICENSE_REVOKED", "LICENSE_EXPIRED"):
            try:
                ratelimit.check_and_increment("activation_invalid_license", _client_bucket())
            except ratelimit.RateLimitExceeded:
                pass
        return _error_response(exc.internal_reason_code, exc.http_status, correlation_id)
    except Exception:
        db_session.rollback()
        current_app.logger.exception("Internal decision failure during activation (request body not logged).")
        return _error_response("INTERNAL_DECISION_FAILURE", 500, correlation_id)


@bp.route("/activations/check", methods=["POST"])
def activation_check():
    """A lightweight status check for an already-activated installation --
    identical authentication/response shape to check-in, distinct route for
    clients that want to poll status without advancing check-in bookkeeping."""
    return check_ins()


@bp.route("/check-ins", methods=["POST"])
def check_ins():
    ready, reason = is_service_ready(current_app.config["SIGNING_KEY_DIRECTORY"], replay_protection_required=current_app.config["REPLAY_PROTECTION_REQUIRED"])
    if not ready:
        return _error_response(reason, 503)

    try:
        ratelimit.check_and_increment("check_in", _client_bucket())
    except ratelimit.RateLimitExceeded as exc:
        resp, status = _error_response("RATE_LIMITED", 429)
        resp.headers["Retry-After"] = str(exc.retry_after_seconds)
        return resp, status

    body = _get_json_body()
    if body is None:
        return _error_response("INVALID_REQUEST", 400)
    correlation_id = body.get("correlation_id") if isinstance(body, dict) else None

    try:
        response = process_checkin(body, source_ip=request.remote_addr, config=_service_config())
        return jsonify(response), 200
    except CheckInRejected as exc:
        db_session.rollback()
        return _error_response(exc.internal_reason_code, exc.http_status, correlation_id)
    except Exception:
        db_session.rollback()
        current_app.logger.exception("Internal decision failure during check-in (request body not logged).")
        return _error_response("INTERNAL_DECISION_FAILURE", 500, correlation_id)


@bp.route("/deactivations", methods=["POST"])
def deactivations():
    ready, reason = is_service_ready(current_app.config["SIGNING_KEY_DIRECTORY"], replay_protection_required=current_app.config["REPLAY_PROTECTION_REQUIRED"])
    if not ready:
        return _error_response(reason, 503)

    body = _get_json_body()
    if body is None:
        return _error_response("INVALID_REQUEST", 400)
    correlation_id = body.get("correlation_id") if isinstance(body, dict) else None

    try:
        response = process_deactivation(body, source_ip=request.remote_addr, config=_service_config())
        return jsonify(response), 200
    except DeactivationRejected as exc:
        db_session.rollback()
        return _error_response(exc.internal_reason_code, exc.http_status, correlation_id)
    except Exception:
        db_session.rollback()
        current_app.logger.exception("Internal decision failure during deactivation (request body not logged).")
        return _error_response("INTERNAL_DECISION_FAILURE", 500, correlation_id)


@bp.route("/signing-keys", methods=["GET"])
def signing_keys():
    try:
        ratelimit.check_and_increment("signing_keys", _client_bucket())
    except ratelimit.RateLimitExceeded as exc:
        resp, status = _error_response("RATE_LIMITED", 429)
        resp.headers["Retry-After"] = str(exc.retry_after_seconds)
        return resp, status
    response = jsonify(export_signed_keyset_manifest(current_app.config["SIGNING_KEY_DIRECTORY"]))
    response.headers["Cache-Control"] = "public, max-age=300"
    return response, 200


@bp.route("/service-info", methods=["GET"])
def service_info():
    try:
        ratelimit.check_and_increment("service_info", _client_bucket())
    except ratelimit.RateLimitExceeded as exc:
        resp, status = _error_response("RATE_LIMITED", 429)
        resp.headers["Retry-After"] = str(exc.retry_after_seconds)
        return resp, status
    return jsonify(public_service_info(current_app.config["SIGNING_KEY_DIRECTORY"])), 200


@bp.route("/releases/authorize-download", methods=["POST"])
def authorize_release_download():
    """Phase 9R M11. Device-signature-authenticated (same primitives as
    check-in) -- not a new auth mechanism. Returns a short-lived, single-use
    download token; never the artifact itself and never a storage
    credential."""
    try:
        ratelimit.check_and_increment("release_download_authorize", _client_bucket())
    except ratelimit.RateLimitExceeded as exc:
        resp, status = _error_response("RATE_LIMITED", 429)
        resp.headers["Retry-After"] = str(exc.retry_after_seconds)
        return resp, status

    body = _get_json_body()
    if body is None:
        return _error_response("INVALID_REQUEST", 400)
    correlation_id = body.get("correlation_id") if isinstance(body, dict) else None

    try:
        authorization, raw_token = authorize_download(body, source_ip=request.remote_addr, config=_service_config())
        return jsonify({
            "download_token": raw_token,
            "expires_at": authorization.expires_at.isoformat(),
            "product_version": authorization.product_version.version,
            "artifact_checksum_sha256": authorization.product_version.artifact_checksum_sha256,
            "artifact_size_bytes": authorization.product_version.artifact_size_bytes,
        }), 201
    except DownloadAuthorizationRejected as exc:
        db_session.rollback()
        if exc.internal_reason_code == "INVALID_SIGNATURE":
            try:
                ratelimit.check_and_increment("activation_invalid_signature", _client_bucket())
            except ratelimit.RateLimitExceeded:
                pass
        return _error_response(exc.internal_reason_code, exc.http_status, correlation_id)
    except Exception:
        db_session.rollback()
        return _error_response("INTERNAL_DECISION_FAILURE", 500, correlation_id)


@bp.route("/releases/download/<token>", methods=["GET"])
def fetch_release_download(token: str):
    """The token itself (single-use, short-lived, high-entropy) is the only
    credential this endpoint checks -- deliberately no device-signature
    requirement here, since the authorize-download step already proved
    device identity and the token is unguessable and one-shot. Streams the
    artifact directly; never a redirect to a raw storage path/credential."""
    try:
        ratelimit.check_and_increment("release_download_fetch", _client_bucket())
    except ratelimit.RateLimitExceeded as exc:
        resp, status = _error_response("RATE_LIMITED", 429)
        resp.headers["Retry-After"] = str(exc.retry_after_seconds)
        return resp, status

    try:
        content, release = fetch_download(token)
    except DownloadAuthorizationRejected as exc:
        db_session.rollback()
        return _error_response(exc.internal_reason_code, exc.http_status)
    except Exception:
        db_session.rollback()
        return _error_response("INTERNAL_DECISION_FAILURE", 500)

    from flask import Response

    safe_filename = f"release-{release.version}.bin"
    response = Response(content, mimetype="application/octet-stream")
    response.headers["Content-Disposition"] = f'attachment; filename="{safe_filename}"'
    response.headers["Content-Length"] = str(len(content))
    response.headers["Cache-Control"] = "no-store"
    return response, 200
