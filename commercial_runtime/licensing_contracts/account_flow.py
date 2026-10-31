"""Sign in with an Aura account (new customer flow, 2026-10-31).

THE FLOW (Owner endpoints are defined in the shared plan, slice S5):

    account_sign_in   email + password (+ device signature)
                      -> ticket, licences, contract, seller, password_change_required
    change_password   ticket + new password            (only if password_change_required)
    accept_contract   ticket + the exact text hash     (only if contract.required)
    activate          {account_ticket, license_public_id} -> the same signed assertion as
                      a licence key gets; ActivationFailed on any refusal.

WHY THIS MODULE IS SEPARATE FROM client.py / activation.py. Windows makes the
Owner calls from Python (it holds the device key); Android makes them from Kotlin
(the Keystore holds the key) and hands the RAW response to the /_internal/account-*
routes. Both must read the sign-in answer through the SAME parsing, or the two
platforms drift: `normalise_sign_in_response` is that one place, and it is also
where the contract text is checked against its hash before any screen may show
it -- a person must never be asked to accept text that differs from what Owner
will record them as having accepted.

THE PASSWORD. It exists only as an argument of `sign_in` / `change_password` for
the length of one call. It is never stored, logged, cached or put in an event
detail (test_account_flow.py greps the logs and every database for a sentinel).
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Optional

from .client import LicensingClient, LicensingClientError

#: Reason codes the account flow can surface (Owner's public vocabulary, plan S5).
SIGN_IN_FAILED = "ACCOUNT_SIGN_IN_FAILED"
CONTRACT_REQUIRED = "CONTRACT_ACCEPTANCE_REQUIRED"
TRIAL_REQUIRES_UPDATE = "TRIAL_REQUIRES_UPDATE"
GRANT_INVALID = "ACTIVATION_GRANT_INVALID"
PASSWORD_POLICY_FAILED = "PASSWORD_POLICY_FAILED"  # pragma: allowlist secret -- a reason-code name, not a credential
RATE_LIMITED = "RATE_LIMITED"
#: Local (never from Owner): the contract body does not hash to what Owner said.
CONTRACT_HASH_MISMATCH = "CONTRACT_HASH_MISMATCH"

#: A new password must be at least this long (Owner's customer policy: 10).
#: Checked here only to answer instantly; Owner stays the authority.
MIN_NEW_PASSWORD_LENGTH = 10


class AccountFlowError(Exception):
    """A refusal or failure in the account flow. `reason_code` is stable and
    safe to show; `retry_after_seconds` is set for a lockout."""

    def __init__(self, reason_code: str, message: str = "", *, retry_after_seconds: Optional[int] = None,
                 policy_code: Optional[str] = None):
        super().__init__(message or reason_code)
        self.reason_code = reason_code
        self.retry_after_seconds = retry_after_seconds
        self.policy_code = policy_code


def _plan_names(raw: dict):
    """`plan_name` as Owner may send it: one string (used for both languages), or
    {"en": ..., "ar": ...}; `plan_name_ar` alone is also honoured. Anything else
    is None -- the screens fall back to their local map of `plan_code`."""
    name = raw.get("plan_name")
    en = ar = None
    if isinstance(name, dict):
        en, ar = _text(name.get("en")), _text(name.get("ar"))
    else:
        en = _text(name)
    ar = ar or _text(raw.get("plan_name_ar"))
    return en, ar


@dataclass(frozen=True)
class LicenceChoice:
    license_public_id: str
    product_code: str
    plan_code: Optional[str]
    branch_label: Optional[str]
    trial_state: Optional[str]
    trial_ends_on: Optional[str]
    #: rc.19: the friendly package name Owner sends next to `plan_code` (dress
    #: rehearsal 2026-10-31: the activation screen showed the raw code
    #: "AURA_RETAIL_P1_YEARLY"). Optional on purpose -- an older Owner sends
    #: neither, and the screens then map `plan_code` locally (plan-names in
    #: licensing-account.js / AccountFlow.kt). English + Arabic, either may be None.
    plan_name: Optional[str] = None
    plan_name_ar: Optional[str] = None

    def public_view(self) -> dict:
        return {
            "license_public_id": self.license_public_id,
            "product_code": self.product_code,
            "plan_code": self.plan_code,
            "plan_name": self.plan_name,
            "plan_name_ar": self.plan_name_ar,
            "branch_label": self.branch_label,
            "trial": {"state": self.trial_state, "ends_on": self.trial_ends_on},
        }


@dataclass(frozen=True)
class ContractOffer:
    required: bool
    version_id: Optional[str]
    language: Optional[str]
    title: Optional[str]
    body: Optional[str]
    sha256: Optional[str]
    #: Owner marks a version that has not been approved by a lawyer. The screen
    #: shows a DRAFT watermark for anything that is not explicitly True.
    lawyer_approved: bool

    def public_view(self) -> dict:
        return {
            "required": self.required,
            "version_id": self.version_id,
            "language": self.language,
            "title": self.title,
            "body": self.body,
            "sha256": self.sha256,
            "lawyer_approved": self.lawyer_approved,
            "draft": not self.lawyer_approved,
        }


@dataclass(frozen=True)
class SignInResult:
    # repr=False: a ticket must not leak through a stray repr()/log of this object.
    ticket: str = field(repr=False)
    expires_at: Optional[str]
    password_change_required: bool
    licences: tuple
    contract: ContractOffer
    seller_name: Optional[str]
    seller_phone: Optional[str]

    def public_view(self) -> dict:
        """What the screen may hold. NO ticket: the ticket stays on the device's
        Python side (see account_session.py) and the page gets a handle."""
        return {
            "password_change_required": self.password_change_required,
            "expires_at": self.expires_at,
            "licences": [c.public_view() for c in self.licences],
            "contract": self.contract.public_view(),
            "seller": {"name": self.seller_name, "phone": self.seller_phone},
        }


def contract_text_sha256(body: str) -> str:
    """sha256 (lowercase hex) of the contract body's UTF-8 bytes, exactly as
    Owner sent them. Owner's `contract.sha256` is defined the same way."""
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _text(value) -> Optional[str]:
    return value if isinstance(value, str) and value else None


def normalise_sign_in_response(response: dict) -> SignInResult:
    """Turn Owner's raw sign-in answer into a SignInResult, or raise
    AccountFlowError. Used by the Windows path and by Android's
    /_internal/account-sign-in-response so both read it identically."""
    if not isinstance(response, dict):
        raise AccountFlowError("MALFORMED_RESPONSE", "Owner answer was not an object.")
    reason = response.get("reason_code")
    ticket = response.get("ticket")
    if reason or not isinstance(ticket, str) or not ticket:
        # One public failure for every refusal (wrong password, unknown e-mail,
        # locked, no licence): ACCOUNT_SIGN_IN_FAILED. 429 keeps its own code.
        code = reason if reason in (RATE_LIMITED, TRIAL_REQUIRES_UPDATE) else SIGN_IN_FAILED
        retry_after = response.get("retry_after_seconds")
        raise AccountFlowError(
            code, "Sign-in was refused.",
            retry_after_seconds=retry_after if isinstance(retry_after, int) else None,
        )

    licences = []
    for raw in response.get("licences") or []:
        if not isinstance(raw, dict):
            continue
        public_id = _text(raw.get("license_public_id"))
        if not public_id:
            continue
        trial = raw.get("trial") if isinstance(raw.get("trial"), dict) else {}
        plan_name, plan_name_ar = _plan_names(raw)
        licences.append(LicenceChoice(
            license_public_id=public_id,
            product_code=_text(raw.get("product_code")) or "",
            plan_code=_text(raw.get("plan_code")),
            branch_label=_text(raw.get("branch_label")),
            trial_state=_text(trial.get("state")),
            trial_ends_on=_text(trial.get("ends_on")),
            plan_name=plan_name,
            plan_name_ar=plan_name_ar,
        ))
    if not licences:
        # Signed in fine, but nothing to activate: the same single failure --
        # the screen must not reveal which half was wrong.
        raise AccountFlowError(SIGN_IN_FAILED, "No licence on this account.")

    raw_contract = response.get("contract") if isinstance(response.get("contract"), dict) else {}
    required = bool(raw_contract.get("required"))
    body = _text(raw_contract.get("body"))
    sha = _text(raw_contract.get("sha256"))
    if required:
        if not (body and sha and _text(raw_contract.get("version_id"))):
            raise AccountFlowError(CONTRACT_HASH_MISMATCH, "Contract text is incomplete.")
        if contract_text_sha256(body) != sha.lower():
            raise AccountFlowError(CONTRACT_HASH_MISMATCH, "Contract text does not match its hash.")
    contract = ContractOffer(
        required=required,
        version_id=_text(raw_contract.get("version_id")),
        language=_text(raw_contract.get("language")),
        title=_text(raw_contract.get("title")),
        body=body,
        sha256=sha.lower() if sha else None,
        lawyer_approved=raw_contract.get("lawyer_approved") is True,
    )
    seller = response.get("seller") if isinstance(response.get("seller"), dict) else {}
    return SignInResult(
        ticket=ticket,
        expires_at=_text(response.get("expires_at")),
        password_change_required=bool(response.get("password_change_required")),
        licences=tuple(licences),
        contract=contract,
        seller_name=_text(seller.get("name")),
        seller_phone=_text(seller.get("phone")),
    )


def sign_in(
    *,
    client: LicensingClient,
    signer,
    email: str,
    password: str,
    product_code: str,
    platform: str,
    app_version: str,
) -> SignInResult:
    try:
        response = client.account_sign_in(
            email=email,
            password=password,
            product_code=product_code,
            platform=platform,
            app_version=app_version,
            device_public_key_b64=signer.get_public_key_b64(),
            signer=signer,
        )
    except LicensingClientError as exc:
        raise AccountFlowError(exc.reason_code, str(exc)) from exc
    finally:
        password = None  # noqa: F841 -- drop our reference; the caller clears its own
    return normalise_sign_in_response(response)


def _check_plain_answer(response: dict, *, default_failure: str) -> dict:
    """A password-change / acceptance answer: success is a 2xx with no
    reason_code; anything else is a refusal with Owner's reason_code."""
    status = response.get("http_status")
    reason = response.get("reason_code")
    ok_status = isinstance(status, int) and 200 <= status < 300
    if ok_status and not reason:
        return response
    retry_after = response.get("retry_after_seconds")
    policy = response.get("policy_code")
    raise AccountFlowError(
        reason if isinstance(reason, str) and reason else default_failure,
        "Owner refused the request.",
        retry_after_seconds=retry_after if isinstance(retry_after, int) else None,
        policy_code=policy if isinstance(policy, str) else None,
    )


def change_password(
    *,
    client: LicensingClient,
    signer,
    ticket: str,
    new_password: str,
    product_code: str,
    platform: str,
    app_version: str,
) -> Optional[str]:
    """Returns a REPLACEMENT ticket if Owner sent one, else None (keep the old).

    The length is checked here only to answer without a round trip; Owner's
    policy (length, common passwords, contains-your-e-mail) is the authority and
    its `policy_code` is passed through."""
    if not isinstance(new_password, str) or len(new_password) < MIN_NEW_PASSWORD_LENGTH:
        raise AccountFlowError(PASSWORD_POLICY_FAILED, "Password is too short.", policy_code="too_short")
    try:
        response = client.change_password(
            ticket=ticket,
            new_password=new_password,
            product_code=product_code,
            platform=platform,
            app_version=app_version,
            device_public_key_b64=signer.get_public_key_b64(),
            signer=signer,
        )
    except LicensingClientError as exc:
        raise AccountFlowError(exc.reason_code, str(exc)) from exc
    finally:
        new_password = None  # noqa: F841
    answer = _check_plain_answer(response, default_failure=PASSWORD_POLICY_FAILED)
    fresh = answer.get("ticket")
    return fresh if isinstance(fresh, str) and fresh else None


def accept_contract(
    *,
    client: LicensingClient,
    signer,
    ticket: str,
    contract: ContractOffer,
    product_code: str,
    platform: str,
    app_version: str,
) -> None:
    if not (contract.required and contract.version_id and contract.sha256 and contract.body):
        raise AccountFlowError(CONTRACT_HASH_MISMATCH, "There is no contract to accept.")
    # Re-hash at the moment of acceptance: what we record is what we hold.
    if contract_text_sha256(contract.body) != contract.sha256:
        raise AccountFlowError(CONTRACT_HASH_MISMATCH, "Contract text changed after it was shown.")
    try:
        response = client.accept_contract(
            ticket=ticket,
            contract_version_id=contract.version_id,
            text_sha256=contract.sha256,
            language=contract.language or "ar",
            product_code=product_code,
            platform=platform,
            app_version=app_version,
            device_public_key_b64=signer.get_public_key_b64(),
            signer=signer,
        )
    except LicensingClientError as exc:
        raise AccountFlowError(exc.reason_code, str(exc)) from exc
    _check_plain_answer(response, default_failure=CONTRACT_REQUIRED)
