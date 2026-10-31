"""Website -> Owner customer account API: /api/website/v1/account/*.

Server-to-server ONLY. The caller is the actionaura.me Pages Function (a BFF); a
browser never reaches this. So it is csrf-exempt (app/__init__.py): the HMAC
signature (app/customer_accounts/signature.py) is its CSRF-equivalent, because a
browser cannot produce it.

Order matters and is pinned by tests/test_customer_accounts_api.py:
  0. Any method but POST -> 404 (never a 405 that reveals the route).
  1. No active shared secret (OWNER_WEBSITE_ACCOUNT_SECRET[_2] unset or not a real
     secret) -> 404, identical to a missing route, before anything is read.
  2. Signature: well-formed header, active kid, within 60 s, HMAC matches -> else 401.
  3. Nonce: each (nonce) usable once in scope "website_account" -> reuse is 401.
     Consumed only AFTER the signature passed, so a forged request cannot burn a
     genuine nonce.
  4. Size, JSON. Then the handler.

Every failure the person can cause answers one of a few fixed shapes; the client IP
comes from the signed body (the website passes CF-Connecting-IP), trusted only
because step 2 passed. Nothing here logs an email, a password or a token.
"""
from __future__ import annotations

import functools
import logging

from flask import Blueprint, Response, abort, current_app, jsonify, request

from app.customer_accounts import policy, service, signature, views
from app.extensions import db_session
from app.licensing_service import ratelimit, replay
from app.releases import customer_offers, distribution

logger = logging.getLogger(__name__)

bp = Blueprint("website_account_api", __name__, url_prefix="/api/website/v1/account")

NONCE_SCOPE = "website_account"
NONCE_TTL_SECONDS = 2 * signature.MAX_SKEW_SECONDS + 60


def _json(payload: dict, status: int = 200):
    response = jsonify(payload)
    response.status_code = status
    response.headers["Cache-Control"] = "no-store"
    return response


def _unauthorized():
    return _json({"ok": False, "error": "unauthorized"}, 401)


def _account_view(account) -> dict:
    from app.extensions import db_session
    from app.models.customers import Customer

    customer = db_session.get(Customer, account.customer_id)
    return {
        "email": account.email, "language": account.language, "status": account.status,
        # New customer flow: the website/app send the person to "choose your own
        # password" while this is true; email_verified says the mailbox was proven.
        "password_must_change": bool(account.password_must_change),
        "email_verified": account.email_verified_at is not None,
        "customer_name": (customer.trade_name or customer.legal_name) if customer else None,
    }


def _gate():
    """Steps 0-4. Returns (body_dict, None) or (None, response)."""
    if request.method != "POST":
        abort(404)
    if not signature.active_keys(current_app.config):
        abort(404)
    limit = current_app.config["MAX_REQUEST_BYTES"]
    if request.content_length is not None and request.content_length > limit:
        return None, _json({"ok": False, "error": "too_large"}, 413)
    raw = request.get_data()
    try:
        parsed = signature.verify(current_app.config, request.headers.get(signature.HEADER, ""),
                                  request.method, request.path, raw)
    except signature.SignatureError:
        return None, _unauthorized()
    try:
        replay.consume_nonce(parsed.nonce, NONCE_SCOPE, NONCE_TTL_SECONDS)
    except replay.ReplayError:
        return None, _unauthorized()
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return None, _json({"ok": False, "error": "invalid_json"}, 400)
    return body, None


def _text(body: dict, key: str, limit: int = 1024) -> str:
    value = body.get(key)
    return value[:limit] if isinstance(value, str) else ""


class _MissingClientIp(Exception):
    pass


def _ip(body: dict) -> str:
    """The signed client address. The website always sends it; if it is missing the
    only address we could use is the website server's own, which would put every
    customer into ONE rate-limit bucket (one attacker locks everybody out) -- so
    the request is refused instead of guessed."""
    value = body.get("client_ip")
    if not isinstance(value, str) or not value.strip():
        raise _MissingClientIp()
    return service.clean_ip(value[:64])


def _locked(exc: service.SignInLocked):
    response = _json({"ok": False, "error": "rate_limited"}, 429)
    response.headers["Retry-After"] = str(exc.retry_after_seconds)
    return response


def _gated(handler):
    """Runs the gate (404 / signature / nonce / JSON) and hands the parsed body to
    the handler. Each handler sits under a literal `@bp.route(...)` so the route
    table is readable (and tests/test_route_reachability.py can see it)."""
    @functools.wraps(handler)
    def view():
        body, early = _gate()
        if early is not None:
            return early
        try:
            return handler(body)
        except _MissingClientIp:
            return _json({"ok": False, "error": "invalid_request"}, 400)
    return view


@bp.route("/sign-in", methods=["GET", "POST"])
@_gated
def sign_in(body):
    try:
        token, account = service.sign_in(_text(body, "email", 320), _text(body, "password", 256), _ip(body),
                                         _text(body, "user_agent", 512))
    except service.SignInLocked as exc:
        return _locked(exc)
    except service.SignInFailed:
        return _json({"ok": False, "error": "invalid_credentials"}, 401)
    return _json({"ok": True, "session_token": token, "account": _account_view(account),
                  "idle_seconds": int(service.SESSION_IDLE.total_seconds()),
                  "absolute_seconds": int(service.SESSION_ABSOLUTE.total_seconds())})


@bp.route("/sign-out", methods=["GET", "POST"])
@_gated
def sign_out(body):
    service.sign_out(_text(body, "session_token", 256))
    return _json({"ok": True})


@bp.route("/me", methods=["GET", "POST"])
@_gated
def me(body):
    found = service.get_session(_text(body, "session_token", 256))
    if found is None:
        return _json({"ok": False, "error": "session_invalid"}, 401)
    return _json({"ok": True, "account": _account_view(found[1])})


@bp.route("/password/forgot", methods=["GET", "POST"])
@_gated
def password_forgot(body):
    try:
        service.forgot_password(_text(body, "email", 320), _ip(body), _text(body, "lang", 8))
    except service.SignInLocked as exc:
        return _locked(exc)
    return _json({"ok": True}, 202)  # the same answer whatever the address was


@bp.route("/password/set", methods=["GET", "POST"])
@_gated
def password_set(body):
    try:
        account = service.set_password_with_token(_text(body, "token", 256), _text(body, "password", 256), _ip(body))
    except service.SignInLocked as exc:
        return _locked(exc)
    except service.AccountError:
        return _json({"ok": False, "error": "token_invalid"}, 400)
    except policy.PasswordPolicyError as exc:
        return _json({"ok": False, "error": "password_policy", "reason": exc.code}, 422)
    return _json({"ok": True, "account": _account_view(account)})


@bp.route("/password/change", methods=["GET", "POST"])
@_gated
def password_change(body):
    try:
        token, account = service.change_password(
            _text(body, "session_token", 256), _text(body, "current_password", 256),
            _text(body, "new_password", 256), _ip(body), _text(body, "user_agent", 512))
    except service.SignInLocked as exc:
        return _locked(exc)
    except service.SignInFailed:
        return _json({"ok": False, "error": "invalid_credentials"}, 401)
    except service.AccountError:
        return _json({"ok": False, "error": "session_invalid"}, 401)
    except policy.PasswordPolicyError as exc:
        return _json({"ok": False, "error": "password_policy", "reason": exc.code}, 422)
    return _json({"ok": True, "session_token": token, "account": _account_view(account)})


@bp.route("/email/verify", methods=["GET", "POST"])
@_gated
def email_verify(body):
    try:
        account = service.verify_email_with_token(_text(body, "token", 256), _ip(body))
    except service.SignInLocked as exc:
        return _locked(exc)
    except service.AccountError:
        return _json({"ok": False, "error": "token_invalid"}, 400)
    return _json({"ok": True, "account": _account_view(account)})


@bp.route("/email/confirm", methods=["GET", "POST"])
@_gated
def email_confirm(body):
    try:
        account = service.confirm_email_change(_text(body, "token", 256), _ip(body))
    except service.SignInLocked as exc:
        return _locked(exc)
    except service.AccountError:
        return _json({"ok": False, "error": "token_invalid"}, 400)
    return _json({"ok": True, "account": _account_view(account)})


# ---------------------------------------------------------------------------
# New customer flow, slice S4: what the account page shows, and the installers.
# The JSON below is exactly what the website's BFF codes against
# (website/functions/_lib/account-summary.ts and functions/api/account/downloads/*).

def _licence_view(row) -> dict:
    return {
        "license_public_id": str(row.license.id), "product_code": row.product.product_code,
        "product_name": row.product.name, "plan_code": row.plan.plan_code, "plan_name": row.plan.name,
        "branch_label": row.subscription.branch_label,
        "device_allowance": row.subscription.device_allowance if row.subscription.device_allowance is not None
        else row.plan.included_device_count,
        "trial": views.trial_view(row.pilot),
    }


@bp.route("/summary", methods=["GET", "POST"])
@_gated
def summary(body):
    found = service.get_session(_text(body, "session_token", 256))
    if found is None:
        return _json({"ok": False, "error": "session_invalid"}, 401)
    account = found[1]
    _ip(body)  # the website always forwards it; a missing one is refused like everywhere else
    offers = [] if account.password_must_change else [o.public_view() for o in customer_offers.offers_for_customer(account.customer_id)]
    return _json({
        "ok": True, "account": _account_view(account),
        "licences": [_licence_view(row) for row in views.licence_rows(account.customer_id)],
        "seller": views.seller_of(account.customer_id),
        # The installer list is empty while the temporary password stands: the
        # download endpoints refuse then too, so the page shows no dead buttons.
        "downloads": offers,
    })


@bp.route("/downloads/authorize", methods=["GET", "POST"])
@_gated
def downloads_authorize(body):
    found = service.get_session(_text(body, "session_token", 256))
    if found is None:
        return _json({"ok": False, "error": "session_invalid"}, 401)
    account = found[1]
    ip = _ip(body)
    if account.password_must_change:
        return _json({"ok": False, "error": "password_change_required"}, 403)
    try:
        ratelimit.check_and_increment("website_download_authorize", ratelimit.safe_bucket_component(str(account.id)))
    except ratelimit.RateLimitExceeded as exc:
        response = _json({"ok": False, "error": "rate_limited"}, 429)
        response.headers["Retry-After"] = str(exc.retry_after_seconds)
        return response
    offer = customer_offers.find_offer(account.customer_id, _text(body, "offer_id", 64))
    if offer is None:
        return _json({"ok": False, "error": "offer_unavailable"}, 400)
    ttl = current_app.config["WEBSITE_DOWNLOAD_TTL_SECONDS"]
    uses = current_app.config["WEBSITE_DOWNLOAD_MAX_USES"]
    _row, raw = distribution.authorize_customer_download(account, offer, ttl_seconds=ttl, max_uses=uses)
    logger.info("website download link made for an account (ip bucket %s)", service.ip_bucket(ip))
    return _json({"ok": True, "token": raw, "expires_in_seconds": ttl, "max_uses": uses,
                  "filename": offer.filename, "size_bytes": offer.size_bytes})


_FETCH_STATUS = {"TOKEN_NOT_FOUND": 404, "TOKEN_EXPIRED": 410, "TOKEN_ALREADY_USED": 410, "TOKEN_REVOKED": 410,
                 "RELEASE_NOT_PUBLISHED": 410, "ARTIFACT_UNAVAILABLE": 503}


@bp.route("/downloads/fetch", methods=["GET", "POST"])
@_gated
def downloads_fetch(body):
    ip = _ip(body)
    try:
        ratelimit.check_and_increment("website_download_fetch", ratelimit.safe_bucket_component(service.ip_bucket(ip)))
    except ratelimit.RateLimitExceeded as exc:
        response = _json({"ok": False, "error": "rate_limited"}, 429)
        response.headers["Retry-After"] = str(exc.retry_after_seconds)
        return response
    try:
        download = distribution.open_customer_download(_text(body, "token", 256))
    except distribution.DownloadAuthorizationRejected as exc:
        db_session.rollback()
        code = exc.internal_reason_code
        return _json({"ok": False, "error": code.lower()}, _FETCH_STATUS.get(code, 400))
    # STREAMED: Flask sends chunks as they are read; nothing holds the file in memory.
    response = Response(download.chunks, status=200, mimetype=download.content_type, direct_passthrough=True)
    response.headers["Content-Length"] = str(download.size_bytes)
    response.headers["Content-Disposition"] = f'attachment; filename="{download.filename}"'
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response
