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

import logging

from flask import Blueprint, abort, current_app, jsonify, request

from app.customer_accounts import policy, service, signature
from app.licensing_service import replay

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


def _ip(body: dict) -> str:
    return service.clean_ip(_text(body, "client_ip", 64)) if body.get("client_ip") else service.clean_ip(request.remote_addr)


def _locked(exc: service.SignInLocked):
    response = _json({"ok": False, "error": "rate_limited"}, 429)
    response.headers["Retry-After"] = str(exc.retry_after_seconds)
    return response


def _route(path: str):
    def decorator(handler):
        @bp.route(path, methods=["GET", "POST"], endpoint=handler.__name__)
        def view():
            body, early = _gate()
            if early is not None:
                return early
            return handler(body)
        return view
    return decorator


@_route("/sign-in")
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


@_route("/sign-out")
def sign_out(body):
    service.sign_out(_text(body, "session_token", 256))
    return _json({"ok": True})


@_route("/me")
def me(body):
    found = service.get_session(_text(body, "session_token", 256))
    if found is None:
        return _json({"ok": False, "error": "session_invalid"}, 401)
    return _json({"ok": True, "account": _account_view(found[1])})


@_route("/password/forgot")
def password_forgot(body):
    try:
        service.forgot_password(_text(body, "email", 320), _ip(body), _text(body, "lang", 8))
    except service.SignInLocked as exc:
        return _locked(exc)
    return _json({"ok": True}, 202)  # the same answer whatever the address was


@_route("/password/set")
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


@_route("/password/change")
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


@_route("/email/confirm")
def email_confirm(body):
    try:
        account = service.confirm_email_change(_text(body, "token", 256), _ip(body))
    except service.SignInLocked as exc:
        return _locked(exc)
    except service.AccountError:
        return _json({"ok": False, "error": "token_invalid"}, 400)
    return _json({"ok": True, "account": _account_view(account)})
