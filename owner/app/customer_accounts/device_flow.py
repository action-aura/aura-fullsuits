"""The Aura apps' door: "sign in with your Aura account" (new customer flow, S5).

    POST /api/licensing/v1/account-sign-ins        email + password  -> a TICKET
    POST /api/licensing/v1/account-password-changes ticket + new password -> a fresh ticket
    POST /api/licensing/v1/contract-acceptances     ticket + exact text hash -> evidence row
    POST /api/licensing/v1/activations              {account_ticket, license_public_id}
    (or an ACTIVATION CODE staff made for one licence)

A ticket is a permission slip, not a login: it can change the password, accept the
contract and activate ONE licence, nothing else. Its rules (each one proved by a
test that breaks it):
  * BOUND to the device: it records the fingerprint of the device key that signed
    the sign-in, and only a request signed by that same key may use it.
  * SINGLE USE for activation: the activation consumes it in one conditional UPDATE
    (two racing activations cannot both win) inside the activation transaction, so a
    refused activation leaves it usable.
  * SHORT: 10 minutes.
  * KILLED BY A PASSWORD CHANGE OR RESET: it carries the account's session_version at
    issue; every password change/reset bumps that number (customer_accounts.service).
  * the same LOCKOUT as the website: the email + password check is
    service.authenticate(), the very function the website's sign-in calls.

THE PASSWORD never leaves the request: it is passed straight to Argon2 and dropped.
Nothing in this module writes it anywhere -- not the activation-request log, not the
idempotency cache (these endpoints have none), not the audit log, not a log line.
tests/test_account_activation.py greps every table and the captured logs for it.
"""
from __future__ import annotations

import hmac
import logging
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update

from app.audit.services import record as audit_record
from app.contracts import service as contracts
from app.customer_accounts import mail, policy, service, views
from app.extensions import db_session
from app.licensing_service import ratelimit
from app.models.account_activation import CustomerActivationGrant
from app.models.customer_accounts import CustomerAccount
from app.models.licensing import License
from app.security.license_keys import hash_license_secret
from app.security.tokens import generate_token, hash_token

logger = logging.getLogger(__name__)

TICKET_TTL = timedelta(minutes=10)
CODE_TTL = timedelta(hours=72)
# Unambiguous alphabet (no 0/O, 1/I/L): a person reads the code over the phone.
_CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"  # pragma: allowlist secret -- the code alphabet, not a credential
_CODE_GROUPS = 3
_CODE_GROUP_LEN = 4


class GrantInvalid(Exception):
    """One public failure for every reason a ticket or code is not good
    (unknown, used, expired, wrong device, wrong licence, password not changed)."""


class ContractRequired(Exception):
    """The customer has not accepted the live contract yet."""


class TicketPolicyError(Exception):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ------------------------------------------------------------ rate limiting

def _take_device_slot(policy_name: str, fingerprint: str) -> None:
    try:
        ratelimit.check_and_increment(policy_name, ratelimit.safe_bucket_component(fingerprint))
    except ratelimit.RateLimitExceeded as exc:
        raise service.SignInLocked(exc.retry_after_seconds)


# ------------------------------------------------------------------- ticket

def _mint_ticket(account: CustomerAccount, fingerprint: str) -> tuple[str, datetime]:
    """Revokes this device's older unused tickets for the account (one live ticket
    per device), stores the new one's hash, returns (raw, expires_at). Caller commits."""
    now = _now()
    db_session.execute(
        update(CustomerActivationGrant)
        .where(
            CustomerActivationGrant.kind == "ACCOUNT_TICKET", CustomerActivationGrant.account_id == account.id,
            CustomerActivationGrant.device_key_fingerprint == fingerprint,
            CustomerActivationGrant.consumed_at.is_(None), CustomerActivationGrant.revoked_at.is_(None),
        )
        .values(revoked_at=now)
    )
    raw = generate_token()
    expires = now + TICKET_TTL
    db_session.add(CustomerActivationGrant(
        kind="ACCOUNT_TICKET", token_hash=hash_token(raw), customer_id=account.customer_id, account_id=account.id,
        device_key_fingerprint=fingerprint, session_version_at_issue=account.session_version, expires_at=expires,
    ))
    db_session.flush()
    return raw, expires


def load_ticket(raw_ticket: str, fingerprint: str) -> tuple[CustomerActivationGrant, CustomerAccount]:
    """The ticket and its account, or GrantInvalid. EVERY check lives here so the
    three endpoints cannot disagree about what a good ticket is."""
    row = db_session.execute(
        select(CustomerActivationGrant).where(
            CustomerActivationGrant.token_hash == hash_token(raw_ticket or ""), CustomerActivationGrant.kind == "ACCOUNT_TICKET"
        )
    ).scalars().first()
    if row is None or row.consumed_at is not None or row.revoked_at is not None or row.expires_at <= _now():
        raise GrantInvalid()
    # Bound to the device: a stolen ticket is useless without that device's key.
    if not hmac.compare_digest((row.device_key_fingerprint or "").encode("ascii", "replace"), fingerprint.encode("ascii", "replace")):
        raise GrantInvalid()
    account = db_session.get(CustomerAccount, row.account_id)
    if account is None or account.status != "ACTIVE" or account.session_version != row.session_version_at_issue:
        raise GrantInvalid()
    return row, account


# ----------------------------------------------------------------- sign-in

@dataclass(frozen=True)
class SignInAnswer:
    ticket: str
    expires_at: datetime
    password_change_required: bool
    licences: list
    contract: dict
    seller: dict


def contract_block(account: CustomerAccount) -> dict:
    """The `contract` object of the sign-in answer. required=true only while there
    is a live customer contract this customer has NOT accepted; body + sha256 then
    travel with it so the app can show exactly the text that will be recorded."""
    live = contracts.live_version_for("CUSTOMER", account.language)
    if live is None or contracts.customer_has_accepted(account.customer_id, live.id):
        return {"required": False, "version_id": str(live.id) if live else None, "language": None, "title": None,
                "body": None, "sha256": None, "lawyer_approved": bool(live.lawyer_approved) if live else False}
    return {"required": True, "version_id": str(live.id), "language": live.language, "title": live.title,
            "body": live.body, "sha256": live.body_sha256, "lawyer_approved": bool(live.lawyer_approved)}


def sign_in(*, email: str, password: str, ip: str, fingerprint: str, product_code: str, platform_code: str) -> SignInAnswer:
    """Raises service.SignInLocked (-> 429) or service.SignInFailed (-> the one public
    401). A correct password on an account with no licence for this product and
    platform fails the same way: the app must not learn which half was wrong."""
    _take_device_slot("account_signin_device", fingerprint)
    try:
        account = service.authenticate(email, password, ip)
    finally:
        password = None  # noqa: F841 -- drop our reference at once
    rows = views.licence_rows(account.customer_id, product_code=product_code, platform_code=platform_code)
    if not rows:
        service.record_success(account, ip)  # the password WAS right; do not count it as a bad guess
        raise service.SignInFailed()
    account.last_sign_in_at = _now()
    raw, expires = _mint_ticket(account, fingerprint)
    service.record_success(account, ip)  # commits the ticket row too
    today = views.jordan_today()
    licences = []
    for r in rows:
        trial = views.trial_view(r.pilot, today)
        licences.append({
            "license_public_id": str(r.license.id), "product_code": r.product.product_code, "plan_code": r.plan.plan_code,
            **views.plan_names(r.plan),
            "branch_label": r.subscription.branch_label, "trial": {"state": trial["state"], "ends_on": trial["ends_on"]},
        })
    audit_record(
        actor_staff_user_id=None, actor_role_snapshot=None, action_code="CUSTOMER_ACCOUNT_APP_SIGN_IN",
        entity_type="customer_account", entity_public_id=str(account.id),
        after_state={"customer_id": str(account.customer_id), "platform": platform_code, "device_fingerprint": fingerprint[:12]},
    )
    return SignInAnswer(
        ticket=raw, expires_at=expires, password_change_required=bool(account.password_must_change),
        licences=licences, contract=contract_block(account), seller=views.seller_of(account.customer_id) or {"name": None, "phone": None},
    )


# ---------------------------------------------------------- password change

def change_password(*, ticket: str, new_password: str, ip: str, fingerprint: str) -> tuple[str, datetime]:
    """Sets the password the customer chose and returns a FRESH ticket for this
    device (the old one dies with the old session_version, as does every website
    session and every other device's ticket). Raises GrantInvalid,
    policy.PasswordPolicyError (incl. code 'same_as_old') or SignInLocked."""
    _take_device_slot("account_ticket_ops", fingerprint)
    try:
        grant, account = load_ticket(ticket, fingerprint)
        service.policy_check(account, new_password)
        # The seller typed the temporary password and knows it: choosing it again
        # would leave the account exactly as insecure as before.
        if account.password_hash and service._verify_password(new_password, account.password_hash):
            raise policy.PasswordPolicyError("same_as_old")
        service.apply_new_password(account, new_password)
        db_session.execute(
            update(CustomerActivationGrant)
            .where(CustomerActivationGrant.account_id == account.id, CustomerActivationGrant.consumed_at.is_(None),
                   CustomerActivationGrant.revoked_at.is_(None))
            .values(revoked_at=_now())
        )
        raw, expires = _mint_ticket(account, fingerprint)
        db_session.commit()
    finally:
        new_password = None  # noqa: F841
    audit_record(
        actor_staff_user_id=None, actor_role_snapshot=None, action_code="CUSTOMER_ACCOUNT_PASSWORD_CHANGED",
        entity_type="customer_account", entity_public_id=str(account.id), after_state={"via": "app"},
    )
    mail.queue("customer_password_changed", account.email, lang=account.language)
    return raw, expires


# ---------------------------------------------------------------- contract

def accept_contract(
    *, ticket: str, contract_version_id: str, text_sha256: str, fingerprint: str, device_signature: str | None,
    ip: str, app_version: str | None, installation_label: str | None,
):
    """Records the acceptance (idempotent). Raises GrantInvalid, or
    contracts.ContractError('TEXT_MISMATCH' | 'VERSION_NOT_LIVE' | 'NOT_FOUND')."""
    _take_device_slot("account_ticket_ops", fingerprint)
    _grant, account = load_ticket(ticket, fingerprint)
    version = contracts.get_version(contract_version_id)
    if version is None:
        raise contracts.ContractError("NOT_FOUND")
    return contracts.record_customer_acceptance(
        account=account, version=version, text_sha256=text_sha256, language=version.language,
        device_key_fingerprint=fingerprint, device_signature=device_signature, ip=ip, app_version=app_version,
        installation_label=installation_label,
    )


# -------------------------------------------------------------- activation

def resolve_ticket_for_activation(raw_ticket: str, license_public_id: str, fingerprint: str) -> tuple[License, CustomerActivationGrant]:
    """Used by process_activation. Raises GrantInvalid / ContractRequired. Does NOT
    consume the ticket (consume_grant does, late, inside the transaction)."""
    grant, account = load_ticket(raw_ticket, fingerprint)
    if account.password_must_change:
        raise GrantInvalid()  # the temporary password has not been replaced yet
    try:
        licence_id = uuid.UUID(str(license_public_id))
    except (ValueError, TypeError):
        raise GrantInvalid()
    license_row = db_session.get(License, licence_id)
    if license_row is None or license_row.customer_id != grant.customer_id:
        raise GrantInvalid()
    if not contracts.customer_gate_is_open(grant.customer_id, account.language):
        raise ContractRequired()
    return license_row, grant


def consume_grant(grant: CustomerActivationGrant) -> None:
    """Spends the grant in ONE conditional UPDATE (the WHERE is re-evaluated under
    the row lock, so two racing activations cannot both succeed). Raises GrantInvalid."""
    now = _now()
    done = db_session.execute(
        update(CustomerActivationGrant)
        .where(CustomerActivationGrant.id == grant.id, CustomerActivationGrant.consumed_at.is_(None),
               CustomerActivationGrant.revoked_at.is_(None), CustomerActivationGrant.expires_at > now)
        .values(consumed_at=now)
        .returning(CustomerActivationGrant.id)
        .execution_options(synchronize_session=False)
    ).first()
    if done is None:
        raise GrantInvalid()


# ------------------------------------------------------- activation codes

def _normalise_code(raw: str) -> str:
    return "".join(ch for ch in (raw or "").upper() if ch.isalnum())


def format_code(compact: str) -> str:
    return "-".join(compact[i:i + _CODE_GROUP_LEN] for i in range(0, len(compact), _CODE_GROUP_LEN))


def make_activation_code(license_row: License, *, actor_staff_user_id, pepper: str) -> tuple[str, datetime]:
    """Support / Super Admin pressed 'Make activation code'. Returns (code shown
    ONCE, expires_at). Earlier unused codes for the same licence are revoked (one
    live code per licence). The caller checked permission and customer scope."""
    now = _now()
    db_session.execute(
        update(CustomerActivationGrant)
        .where(CustomerActivationGrant.kind == "ACTIVATION_CODE", CustomerActivationGrant.license_id == license_row.id,
               CustomerActivationGrant.consumed_at.is_(None), CustomerActivationGrant.revoked_at.is_(None))
        .values(revoked_at=now)
    )
    compact = "".join(secrets.choice(_CODE_ALPHABET) for _ in range(_CODE_GROUPS * _CODE_GROUP_LEN))
    expires = now + CODE_TTL
    db_session.add(CustomerActivationGrant(
        kind="ACTIVATION_CODE", token_hash=hash_license_secret(compact, pepper), customer_id=license_row.customer_id,
        license_id=license_row.id, expires_at=expires, created_by_staff_user_id=actor_staff_user_id,
    ))
    db_session.commit()
    audit_record(
        actor_staff_user_id=actor_staff_user_id, actor_role_snapshot=None, action_code="ACTIVATION_CODE_MADE",
        entity_type="license", entity_public_id=str(license_row.id),
        after_state={"expires_at": expires.isoformat()},  # never the code
    )
    return format_code(compact), expires


def resolve_code_for_activation(raw_code: str, pepper: str) -> tuple[License, CustomerActivationGrant]:
    compact = _normalise_code(raw_code)
    if len(compact) != _CODE_GROUPS * _CODE_GROUP_LEN:
        raise GrantInvalid()
    row = db_session.execute(
        select(CustomerActivationGrant).where(
            CustomerActivationGrant.token_hash == hash_license_secret(compact, pepper),
            CustomerActivationGrant.kind == "ACTIVATION_CODE",
        )
    ).scalars().first()
    if row is None or row.consumed_at is not None or row.revoked_at is not None or row.expires_at <= _now():
        raise GrantInvalid()
    license_row = db_session.get(License, row.license_id)
    if license_row is None:
        raise GrantInvalid()
    return license_row, row
