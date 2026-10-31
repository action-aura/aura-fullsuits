"""The three device-signed endpoints of "sign in with your Aura account" (S5):

    account-sign-ins, account-password-changes, contract-acceptances

Same envelope as activation (contract_version, request_id, timestamp, nonce,
device_public_key, device_public_key_algorithm, Ed25519 `signature` over the
canonical bytes of every other field -- the password INCLUDED in those bytes so a
man in the middle cannot swap it). Order matters and mirrors activation, with one
improvement: the signature is checked BEFORE the nonce is spent, so a forged request
can never burn a genuine nonce.

WHAT THESE ENDPOINTS NEVER DO WITH THE PASSWORD: write it to the activation-request
log, to an idempotency record (none is used here), to the audit log, to a request
fingerprint, to a log line, or into an exception. Failures are logged by reason code
only. The body dict is local to the request and dropped when it ends
(tests/test_account_activation.py greps every table and the captured logs).
"""
from __future__ import annotations

from app.contracts import service as contracts
from app.customer_accounts import device_flow, policy, service
from app.extensions import db_session
from app.licensing_service import device_identity, replay
from app.licensing_service.canonical import canonicalize_bytes
from app.models.catalog import Platform, Product
from app.models.licensing_service import ActivationRequest
from sqlalchemy import select

SUPPORTED_CONTRACT_VERSIONS = ("v1",)
_MAX_FIELD = 512


class AccountRejected(Exception):
    """internal_reason_code goes through to_public_reason_code() in the route; the
    optional `extra` fields (policy_code) and `retry_after` ride along."""

    def __init__(self, internal_reason_code: str, http_status: int = 400, *, extra: dict | None = None,
                 retry_after: int | None = None):
        self.internal_reason_code = internal_reason_code
        self.http_status = http_status
        self.extra = extra or {}
        self.retry_after = retry_after
        super().__init__(internal_reason_code)


def _text(body: dict, key: str, *, max_len: int = _MAX_FIELD) -> str:
    value = body.get(key)
    if not isinstance(value, str) or not value or len(value) > max_len:
        raise AccountRejected("INVALID_REQUEST")
    return value


def _verify_envelope(body, *, nonce_scope: str, config: dict) -> str:
    """Shape, timestamp, device signature, then nonce. Returns the device-key
    fingerprint (sha256 hex of the raw public key) the request proved it holds."""
    if not isinstance(body, dict):
        raise AccountRejected("INVALID_REQUEST")
    for field in ("contract_version", "request_id", "timestamp", "nonce", "device_public_key",
                  "device_public_key_algorithm", "signature"):
        _text(body, field, max_len=4096)
    if body["contract_version"] not in SUPPORTED_CONTRACT_VERSIONS:
        raise AccountRejected("UNSUPPORTED_CONTRACT_VERSION")
    try:
        request_timestamp = replay.parse_request_timestamp(body["timestamp"])
    except (ValueError, TypeError, AttributeError):
        raise AccountRejected("INVALID_TIMESTAMP")
    try:
        replay.validate_timestamp(request_timestamp, config["timestamp_skew_seconds"])
    except replay.ReplayError as exc:
        raise AccountRejected(str(exc))
    try:
        public_key = device_identity.validate_public_key(body["device_public_key_algorithm"], body["device_public_key"])
    except device_identity.DeviceIdentityError as exc:
        raise AccountRejected(str(exc))
    signable = {k: v for k, v in body.items() if k != "signature"}
    if not device_identity.verify_signature(public_key, canonicalize_bytes(signable), body["signature"]):
        raise AccountRejected("INVALID_SIGNATURE")
    try:
        replay.consume_nonce(body["nonce"], scope=nonce_scope, ttl_seconds=config["nonce_ttl_seconds"])
    except replay.ReplayError as exc:
        raise AccountRejected(str(exc))
    return device_identity.fingerprint_of(body["device_public_key"])


def log_outcome(body, source_ip: str | None, result: str, reason_code: str, event_type: str) -> None:
    """One ActivationRequest row per decided request. ONLY ids and the reason code:
    never the body, never an email, never a password."""
    request_id = body.get("request_id") if isinstance(body, dict) and isinstance(body.get("request_id"), str) else "unknown"
    correlation = body.get("correlation_id") if isinstance(body, dict) and isinstance(body.get("correlation_id"), str) else None
    db_session.add(ActivationRequest(
        request_id=request_id[:128], correlation_id=(correlation or None) and correlation[:128], event_type=event_type,
        source_ip=source_ip, result=result, reason_code=reason_code,
    ))
    db_session.commit()


def _locked(exc: service.SignInLocked) -> AccountRejected:
    return AccountRejected("RATE_LIMITED", 429, retry_after=exc.retry_after_seconds)


def process_account_sign_in(body: dict, *, source_ip: str | None, config: dict) -> dict:
    fingerprint = _verify_envelope(body, nonce_scope="account_sign_in", config=config)
    email, password = _text(body, "email", max_len=320), _text(body, "password", max_len=256)
    product_code, platform_code = _text(body, "product_code", max_len=32), _text(body, "platform", max_len=32)
    _text(body, "app_version", max_len=64)
    ip = service.clean_ip(source_ip)
    if (db_session.execute(select(Product.id).where(Product.product_code == product_code)).first() is None
            or db_session.execute(select(Platform.id).where(Platform.platform_code == platform_code)).first() is None):
        raise AccountRejected("INVALID_REQUEST")
    try:
        answer = device_flow.sign_in(
            email=email, password=password, ip=ip, fingerprint=fingerprint, product_code=product_code, platform_code=platform_code,
        )
    except service.SignInLocked as exc:
        raise _locked(exc)
    except service.SignInFailed:
        raise AccountRejected("ACCOUNT_SIGN_IN_FAILED", 401)
    finally:
        password = None  # noqa: F841
        body.pop("password", None)  # the dict must not carry it any further
    return {
        "ticket": answer.ticket,
        "expires_at": answer.expires_at.isoformat(),
        "password_change_required": answer.password_change_required,
        "licences": answer.licences,
        "contract": answer.contract,
        "seller": answer.seller,
    }


def process_password_change(body: dict, *, source_ip: str | None, config: dict) -> dict:
    fingerprint = _verify_envelope(body, nonce_scope="account_password_change", config=config)
    ticket, new_password = _text(body, "ticket", max_len=256), _text(body, "new_password", max_len=256)
    try:
        fresh, expires = device_flow.change_password(
            ticket=ticket, new_password=new_password, ip=service.clean_ip(source_ip), fingerprint=fingerprint,
        )
    except service.SignInLocked as exc:
        raise _locked(exc)
    except device_flow.GrantInvalid:
        raise AccountRejected("ACCOUNT_SIGN_IN_FAILED", 401)
    except policy.PasswordPolicyError as exc:
        raise AccountRejected("PASSWORD_POLICY_FAILED", 400, extra={"policy_code": exc.code})
    finally:
        new_password = None  # noqa: F841
        body.pop("new_password", None)
    return {"ticket": fresh, "expires_at": expires.isoformat()}


def process_contract_acceptance(body: dict, *, source_ip: str | None, config: dict) -> dict:
    fingerprint = _verify_envelope(body, nonce_scope="contract_acceptance", config=config)
    ticket = _text(body, "ticket", max_len=256)
    version_id = _text(body, "contract_version_id", max_len=64)
    text_sha256 = _text(body, "text_sha256", max_len=64)
    installation_label = body.get("installation_id") if isinstance(body.get("installation_id"), str) else None
    try:
        row = device_flow.accept_contract(
            ticket=ticket, contract_version_id=version_id, text_sha256=text_sha256, fingerprint=fingerprint,
            device_signature=body["signature"], ip=service.clean_ip(source_ip),
            app_version=body.get("app_version") if isinstance(body.get("app_version"), str) else None,
            installation_label=installation_label,
        )
    except service.SignInLocked as exc:
        raise _locked(exc)
    except device_flow.GrantInvalid:
        raise AccountRejected("ACCOUNT_SIGN_IN_FAILED", 401)
    except contracts.ContractError:
        # Wrong id, retired version, text that is not the stored text, wrong audience:
        # all the same answer -- show the current contract again.
        raise AccountRejected("CONTRACT_ACCEPTANCE_REQUIRED", 409)
    return {"accepted": True, "accepted_at": row.accepted_at.isoformat()}
