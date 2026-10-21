"""Website demo-request intake -- POST /api/intake/website-lead.

Server-to-server only: the caller is the actionaura.me Cloudflare Pages
Function (website/functions/_lib/notify.ts, the "owner" provider). No browser
and no Owner template calls this, so it is csrf-exempt (app/__init__.py). The
bearer secret is its CSRF-equivalent, because a browser cannot attach it
cross-site.

Order matters and is pinned by tests/test_website_intake.py:
  1. OWNER_WEBSITE_INTAKE_SECRET unset -> 404, identical to a missing route,
     before anything is read or written. Default-off, and never a compare
     against "".
  2. Rate limit (policy "website_intake", per client address), BEFORE the
     auth check, so guessing the secret costs budget too.
  3. Bearer check with hmac.compare_digest (constant time).
  4. Size, JSON and strict payload validation.
  5. Idempotent create (app/leads/website_intake.py): 201 new, 200 replay.

Never logs the name, phone or message -- only the website's lead_id.
"""
from __future__ import annotations

import hmac
import logging

from flask import Blueprint, abort, current_app, jsonify, request

from app.leads.errors import LeadError
from app.leads.website_intake import WebsiteIntakeError, ingest_website_lead, parse_website_lead_payload
from app.licensing_service import ratelimit

logger = logging.getLogger(__name__)

bp = Blueprint("website_intake", __name__, url_prefix="/api/intake")

RATE_LIMIT_POLICY = "website_intake"


def _bearer_matches(header_value: str, secret: str) -> bool:
    scheme, _, presented = (header_value or "").partition(" ")
    presented = presented.strip()
    if scheme.lower() != "bearer" or not presented:
        return False
    return hmac.compare_digest(presented.encode("utf-8"), secret.encode("utf-8"))


def _json(payload: dict, status: int):
    response = jsonify(payload)
    response.status_code = status
    response.headers["Cache-Control"] = "no-store"
    return response


@bp.route("/website-lead", methods=["POST"])
def website_lead():
    secret = current_app.config.get("WEBSITE_INTAKE_SECRET") or ""
    if not secret:
        abort(404)

    try:
        ratelimit.check_and_increment(RATE_LIMIT_POLICY, request.remote_addr or "unknown")
    except ratelimit.RateLimitExceeded as exc:
        response = _json({"ok": False, "error": "rate_limited"}, 429)
        response.headers["Retry-After"] = str(exc.retry_after_seconds)
        return response

    if not _bearer_matches(request.headers.get("Authorization", ""), secret):
        response = _json({"ok": False, "error": "unauthorized"}, 401)
        response.headers["WWW-Authenticate"] = 'Bearer realm="website-intake"'
        return response

    limit = current_app.config["MAX_REQUEST_BYTES"]
    if request.content_length is not None and request.content_length > limit:
        return _json({"ok": False, "error": "too_large"}, 413)
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return _json({"ok": False, "error": "invalid_json"}, 400)

    try:
        payload = parse_website_lead_payload(body)
        lead, created = ingest_website_lead(payload)
    except WebsiteIntakeError as exc:
        if exc.http_status >= 500:
            logger.error("website intake refused: %s", exc.code)
        out = {"ok": False, "error": exc.code}
        if exc.field:
            out["field"] = exc.field
        return _json(out, exc.http_status)
    except LeadError as exc:
        return _json({"ok": False, "error": exc.code}, 422)

    logger.info("website intake: lead_id=%s created=%s", payload.lead_id, created)
    return _json({"ok": True, "lead_id": str(lead.id), "created": created}, 201 if created else 200)
