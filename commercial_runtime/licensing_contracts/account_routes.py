"""Local HTTP routes for 'Sign in with your Aura account' (new customer flow,
2026-10-31). Registered on the licensing blueprint by routes.py.

    POST /api/licensing/account/sign-in         {email, password}
    POST /api/licensing/account/password        {flow_id, new_password}
    POST /api/licensing/account/contract/accept {flow_id, agreed: true}
    POST /api/licensing/account/activate        {flow_id, license_public_id}

    (Android only, shared-secret, see routes.py)
    POST /api/licensing/_internal/account-sign-in-response   <Owner's raw answer>

Like the rest of the licensing surface these are never capability-gated: they
are how a person FIXES a missing licence.

The Owner ticket never leaves this process -- the page holds a `flow_id`
(account_session.py). Every refusal is a stable `reason_code`; nothing here
echoes the e-mail or password back, and nothing logs them.
"""
from __future__ import annotations

import hmac
from typing import Callable, Optional

from flask import Blueprint, jsonify, request

from .account_flow import (
    CONTRACT_HASH_MISMATCH,
    CONTRACT_REQUIRED,
    GRANT_INVALID,
    PASSWORD_POLICY_FAILED,
    RATE_LIMITED,
    SIGN_IN_FAILED,
    AccountFlowError,
    accept_contract,
    change_password,
    normalise_sign_in_response,
    sign_in,
)
from .account_session import AccountSessionStore
from .client import AccountTicket

#: Local, never from Owner.
FLOW_EXPIRED = "ACCOUNT_FLOW_EXPIRED"
PASSWORD_CHANGE_REQUIRED = "PASSWORD_CHANGE_REQUIRED"  # pragma: allowlist secret -- a reason-code name
PASSWORD_CHANGE_NOT_NEEDED = "PASSWORD_CHANGE_NOT_NEEDED"  # pragma: allowlist secret -- a reason-code name
CONTRACT_NOT_NEEDED = "CONTRACT_NOT_NEEDED"
INVALID_REQUEST = "INVALID_REQUEST"

#: Business refusals shown as a plain 4xx; everything else from a transport
#: failure is a 503 ("could not reach the licensing service").
_STATUS_FOR = {
    SIGN_IN_FAILED: 401,
    RATE_LIMITED: 429,
    PASSWORD_POLICY_FAILED: 400,
    CONTRACT_HASH_MISMATCH: 502,
    CONTRACT_REQUIRED: 409,
    GRANT_INVALID: 400,
}
_TRANSPORT_CODES = frozenset({
    "NETWORK_UNAVAILABLE", "REQUEST_TIMED_OUT", "TLS_VERIFICATION_FAILED",
    "SERVICE_TEMPORARILY_UNAVAILABLE", "MALFORMED_RESPONSE",
})


def _error_response(exc: AccountFlowError):
    status = _STATUS_FOR.get(exc.reason_code)
    if status is None:
        status = 503 if exc.reason_code in _TRANSPORT_CODES else 400
    body = {"reason_code": exc.reason_code}
    if exc.retry_after_seconds is not None:
        body["retry_after_seconds"] = exc.retry_after_seconds
    if exc.policy_code:
        body["policy_code"] = exc.policy_code
    response = jsonify(body)
    response.status_code = status
    if exc.retry_after_seconds is not None:
        response.headers["Retry-After"] = str(exc.retry_after_seconds)
    return response


def register_account_routes(
    bp: Blueprint,
    *,
    build_context: Callable,
    run_activation: Callable,
    product_code: str,
    platform: str,
    app_version: str,
    owner_configured: bool,
    internal_shared_secret: Optional[str] = None,
    store: Optional[AccountSessionStore] = None,
) -> AccountSessionStore:
    store = store or AccountSessionStore()

    def _unavailable():
        return jsonify({"reason_code": "SERVICE_TEMPORARILY_UNAVAILABLE",
                        "detail": "Owner licensing URL is not configured."}), 503

    def _flow_or_error(body):
        entry = store.get(body.get("flow_id"))
        if entry is None:
            return None, (jsonify({"reason_code": FLOW_EXPIRED,
                                   "detail": "Sign in again."}), 409)
        return entry, None

    @bp.route("/account/sign-in", methods=["POST"])
    def account_sign_in():
        if not owner_configured:
            return _unavailable()
        body = request.get_json(silent=True) or {}
        email = body.get("email")
        password = body.get("password")
        if (not isinstance(email, str) or not email.strip() or "@" not in email
                or not isinstance(password, str) or not password):
            # Same single public failure as a wrong password: this route must
            # not become a way to learn which half was malformed.
            return _error_response(AccountFlowError(SIGN_IN_FAILED))

        state_repository, event_recorder, trust_store, signer, client = build_context()
        if not signer.has_key():
            signer.generate_new_key()
        try:
            result = sign_in(
                client=client, signer=signer, email=email.strip(), password=password,
                product_code=product_code, platform=platform, app_version=app_version,
            )
        except AccountFlowError as exc:
            event_recorder.record("ACCOUNT_SIGN_IN_FAILED", {"reason_code": exc.reason_code})
            return _error_response(exc)
        finally:
            password = None
            if "password" in body:
                body["password"] = None
        event_recorder.record("ACCOUNT_SIGN_IN_SUCCEEDED")
        handle = store.create(result)
        view = result.public_view()
        view["flow_id"] = handle
        return jsonify(view), 200

    @bp.route("/account/password", methods=["POST"])
    def account_password():
        if not owner_configured:
            return _unavailable()
        body = request.get_json(silent=True) or {}
        entry, error = _flow_or_error(body)
        if error:
            return error
        if not entry.password_change_pending:
            return jsonify({"reason_code": PASSWORD_CHANGE_NOT_NEEDED}), 409
        new_password = body.get("new_password")
        _, event_recorder, _, signer, client = build_context()
        try:
            fresh = change_password(
                client=client, signer=signer, ticket=entry.ticket, new_password=new_password,
                product_code=product_code, platform=platform, app_version=app_version,
            )
        except AccountFlowError as exc:
            if exc.reason_code == SIGN_IN_FAILED:
                store.discard(body.get("flow_id"))  # ticket refused: start over
            return _error_response(exc)
        finally:
            new_password = None
            if "new_password" in body:
                body["new_password"] = None
        if fresh:
            store.replace_ticket(body["flow_id"], fresh)
        store.mark_password_changed(body["flow_id"])
        event_recorder.record("ACCOUNT_PASSWORD_CHANGED")
        return jsonify({"result": "SUCCESS"}), 200

    @bp.route("/account/contract/accept", methods=["POST"])
    def account_contract_accept():
        if not owner_configured:
            return _unavailable()
        body = request.get_json(silent=True) or {}
        entry, error = _flow_or_error(body)
        if error:
            return error
        if entry.password_change_pending:
            return jsonify({"reason_code": PASSWORD_CHANGE_REQUIRED}), 409
        if not entry.result.contract.required:
            return jsonify({"reason_code": CONTRACT_NOT_NEEDED}), 409
        # The page enforces scroll-to-end + the checkbox; the route insists on
        # an explicit `agreed: true` so acceptance can never be a side effect of
        # some other request that merely names a flow.
        if body.get("agreed") is not True:
            return jsonify({"reason_code": INVALID_REQUEST, "detail": "agreed must be true."}), 400
        _, event_recorder, _, signer, client = build_context()
        try:
            accept_contract(
                client=client, signer=signer, ticket=entry.ticket, contract=entry.result.contract,
                product_code=product_code, platform=platform, app_version=app_version,
            )
        except AccountFlowError as exc:
            if exc.reason_code == SIGN_IN_FAILED:
                store.discard(body.get("flow_id"))
            return _error_response(exc)
        store.mark_contract_accepted(body["flow_id"])
        event_recorder.record("CONTRACT_ACCEPTED")
        return jsonify({"result": "SUCCESS"}), 200

    @bp.route("/account/activate", methods=["POST"])
    def account_activate():
        if not owner_configured:
            return _unavailable()
        body = request.get_json(silent=True) or {}
        entry, error = _flow_or_error(body)
        if error:
            return error
        if entry.password_change_pending:
            return jsonify({"reason_code": PASSWORD_CHANGE_REQUIRED}), 409
        if entry.contract_pending:
            return jsonify({"reason_code": CONTRACT_REQUIRED}), 409
        license_public_id = body.get("license_public_id")
        if len(entry.result.licences) == 1 and not license_public_id:
            license_public_id = entry.result.licences[0].license_public_id
        if license_public_id not in entry.licence_ids():
            # Only a licence Owner itself listed for THIS sign-in may be sent.
            return jsonify({"reason_code": GRANT_INVALID}), 400
        credential = AccountTicket(entry.ticket, license_public_id)
        response, status = run_activation(credential=credential)
        payload = response.get_json(silent=True) or {}
        if status == 200 or payload.get("result") == "PENDING":
            store.discard(body["flow_id"])
        elif payload.get("reason_code") == GRANT_INVALID:
            store.discard(body["flow_id"])
        return response, status

    if internal_shared_secret:
        @bp.route("/_internal/account-sign-in-response", methods=["POST"])
        def internal_account_sign_in_response():
            """Android: Kotlin made the signed sign-in call (it holds the
            Keystore key) and hands Owner's RAW answer here so the contract
            text is checked against its hash and the answer normalised by the
            SAME code Windows uses. Returns the screen's view WITHOUT the
            ticket -- Kotlin already holds it from the answer it received."""
            provided = request.headers.get("X-Aura-Internal-Secret", "")
            if not hmac.compare_digest(provided, internal_shared_secret):
                return jsonify({"reason_code": "INVALID_REQUEST", "detail": "Unauthorized."}), 403
            owner_response = request.get_json(silent=True)
            if not isinstance(owner_response, dict):
                return jsonify({"reason_code": INVALID_REQUEST, "detail": "Expected a JSON object."}), 400
            try:
                result = normalise_sign_in_response(owner_response)
            except AccountFlowError as exc:
                return _error_response(exc)
            return jsonify(result.public_view()), 200

    return store
