"""Android-only local surface of the update channel:
POST /api/updates/_internal/verify-descriptor.

Why this exists: on Android the device key lives in Kotlin, so KOTLIN makes the
signed find-latest-release call to Owner (OwnerClient.kt). But trust stays here:
this process owns the Owner trust store and the licence state, so Kotlin hands
the raw Owner answer across and only believes the verdict that comes back --
never the checksum, size or version in the raw JSON. Same split as the licensing
`/_internal/sync-*` routes, same X-Aura-Internal-Secret.

Registered ONLY on Android with a secret (see products/retail/backend/app.py);
on Windows this blueprint is never built, so the URL is a plain 404.

Wire contract (BINDING; the Kotlin half parses exactly this):
  200 {"result": "OFFER", "offer": {...}}   a verified, strictly newer build
  200 {"result": "NO_UPDATE"}               verified, nothing newer
  409 {"result": "ERROR", "error_code": DESCRIPTOR_UNTRUSTED | DESCRIPTOR_MISMATCH
       | DESCRIPTOR_INVALID | NOT_ACTIVATED | OWNER_REFUSED}
  401                                       missing / wrong secret
A malformed body is 409 DESCRIPTOR_INVALID (not the licensing routes' 400):
Kotlin's parser collapses every non-409 error to LOCAL_UNAVAILABLE, and a body
Python cannot read is, from the user's side, an unusable descriptor.
401 (not the licensing routes' 403) because the contract says so.

`owner_response_raw` is a JSON STRING FIELD holding the exact bytes Owner sent,
so no parse-and-reserialize of ours or Kotlin's can disturb what was signed.
"""
from __future__ import annotations

import hmac
import json
import logging
from typing import Callable

from flask import Blueprint, jsonify, request

from .descriptor import UpdateError, verify_owner_release_response

log = logging.getLogger("aura.updates")

_KNOWN_ERROR_CODES = frozenset({
    "DESCRIPTOR_UNTRUSTED", "DESCRIPTOR_MISMATCH", "DESCRIPTOR_INVALID", "NOT_ACTIVATED", "OWNER_REFUSED",
})


def _error(code: str):
    return jsonify({"result": "ERROR", "error_code": code}), 409


def make_internal_updates_blueprint(
    *,
    shared_secret: str,
    trust_store_factory: Callable[[], object],
    state_repository_factory: Callable[[], object],
    app_version: str,
) -> Blueprint:
    if not shared_secret:
        # Fail at construction: an empty secret would make compare_digest("", "")
        # accept a request that sent no header at all.
        raise ValueError("an internal updates route needs a non-empty shared secret")
    bp = Blueprint("updates_internal_api", __name__, url_prefix="/api/updates")
    secret_bytes = shared_secret.encode("utf-8")

    def _authorized() -> bool:
        provided = request.headers.get("X-Aura-Internal-Secret", "").encode("utf-8")
        return hmac.compare_digest(provided, secret_bytes)

    def _installation_id():
        try:
            record = state_repository_factory().load()
        except Exception:
            log.warning("updates: could not read licence state", exc_info=True)
            return None
        return getattr(record, "owner_installation_id", None) or None

    @bp.route("/_internal/verify-descriptor", methods=["POST"])
    def verify_descriptor():
        if not _authorized():
            return jsonify({"result": "ERROR", "error_code": "UNAUTHORIZED"}), 401
        body = request.get_json(silent=True)
        raw = body.get("owner_response_raw") if isinstance(body, dict) else None
        request_id = body.get("request_id") if isinstance(body, dict) else None
        if not isinstance(raw, str) or not isinstance(request_id, str) or not request_id:
            log.info("updates: verify-descriptor refused -- malformed body")
            return _error("DESCRIPTOR_INVALID")
        installation_id = _installation_id()
        if not installation_id:
            log.info("updates: verify-descriptor refused -- this install is not activated")
            return _error("NOT_ACTIVATED")
        try:
            response = json.loads(raw)
        except ValueError:
            log.info("updates: verify-descriptor refused -- owner_response_raw is not JSON")
            return _error("DESCRIPTOR_INVALID")
        try:
            offer = verify_owner_release_response(
                response,
                trust_store=trust_store_factory(),
                expected_installation_id=installation_id,
                expected_request_id=request_id,
                current_version=app_version,
            )
        except UpdateError as exc:
            code = exc.code if exc.code in _KNOWN_ERROR_CODES else "DESCRIPTOR_INVALID"
            log.info("updates: verify-descriptor refused -- %s", code)
            return _error(code)
        except Exception:
            # Fail closed: anything unforeseen (unreadable trust store...) is
            # "not a descriptor we can vouch for", never an offer.
            log.exception("updates: verify-descriptor failed unexpectedly")
            return _error("DESCRIPTOR_INVALID")
        if offer is None:
            return jsonify({"result": "NO_UPDATE"}), 200
        return jsonify({
            "result": "OFFER",
            "offer": {
                "product_version_id": offer.product_version_id,
                "version": offer.version,
                "build_number": offer.build_number,
                "checksum_sha256": offer.checksum_sha256,
                "size_bytes": offer.size_bytes,
                "release_notes": offer.release_notes,
                "update_required": offer.update_required,
                "below_min_supported": offer.below_min_supported,
            },
        }), 200

    return bp
