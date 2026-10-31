"""Customer website accounts: the rules (Step 2, slice CA1).

Everything here is about signing in to the website. NOTHING here reads or writes a
licence, a lease or a device -- disabling an account, resetting a password,
"sign out everywhere" and changing the email never touch them
(tests/test_customer_accounts_service.py::test_account_actions_never_touch_licences).

Each public function commits its own work. Raw tokens (invite / reset / session)
are returned to the caller exactly once and only their SHA-256 is stored.
"""
from __future__ import annotations

import hashlib
import ipaddress
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from app.audit.services import record as audit_record
from app.customer_accounts import mail, policy
from app.extensions import db_session
from app.licensing_service import ratelimit
from app.models.customer_accounts import (
    CustomerAccount,
    CustomerAccountToken,
    CustomerLoginAttempt,
    CustomerSession,
)
from app.models.customers import Customer
from app.security import passwords
from app.security.tokens import generate_token, hash_token

logger = logging.getLogger(__name__)

TOKEN_TTL = {
    "INVITE": timedelta(hours=72),
    "RESET": timedelta(minutes=30),
    "EMAIL_CHANGE": timedelta(hours=24),
    # New customer flow: proves the mailbox a SELLER typed is the customer's. Long
    # (14 days) because the customer may not open the mail until they have got
    # home; it grants nothing but the verified flag, so a longer life is harmless.
    "VERIFY_EMAIL": timedelta(days=14),
}
SESSION_IDLE = timedelta(minutes=60)
SESSION_ABSOLUTE = timedelta(hours=12)
LANGUAGES = ("en", "ar")
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class AccountError(Exception):
    """A refusal with a stable machine code (the staff screens and the website API
    turn it into words). Never carries a secret."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class SignInFailed(AccountError):
    def __init__(self):
        super().__init__("invalid_credentials")


class SignInLocked(AccountError):
    def __init__(self, retry_after_seconds: int):
        self.retry_after_seconds = retry_after_seconds
        super().__init__("rate_limited")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def normalize_email(raw: str) -> str:
    email = (raw or "").strip().lower()
    if not email or len(email) > 254 or not _EMAIL_RE.match(email) or any(ch in email for ch in "\r\n\x00,;<>"):
        raise AccountError("invalid_email")
    return email


def _digest(value: str) -> str:
    return hashlib.sha256((value or "").encode("utf-8")).hexdigest()


def clean_ip(raw: str | None) -> str:
    """The client address the website forwarded (trusted only because the request
    was HMAC-signed). Anything that is not an IP becomes 'unknown'."""
    try:
        return str(ipaddress.ip_address((raw or "").strip()))
    except ValueError:
        return "unknown"


def ip_bucket(ip: str) -> str:
    """The key rate limits count an address under. IPv4 is itself. An IPv6 address
    is counted as its /64: an ISP hands one customer (or attacker) a whole /64, so
    per-address buckets would give them a fresh budget on every request.
    IPv4-mapped IPv6 is the IPv4 address it wraps. Anything unparsable ('unknown')
    is its own bucket."""
    try:
        parsed = ipaddress.ip_address((ip or "").strip())
    except ValueError:
        return ip or "unknown"
    if isinstance(parsed, ipaddress.IPv6Address):
        if parsed.ipv4_mapped is not None:
            return str(parsed.ipv4_mapped)
        return str(ipaddress.ip_network(f"{parsed}/64", strict=False))
    return str(parsed)


# --------------------------------------------------------------------- reads

def get_account_for_customer(customer_id) -> CustomerAccount | None:
    return db_session.execute(
        select(CustomerAccount).where(CustomerAccount.customer_id == customer_id)
    ).scalars().first()


def get_account_by_email(email: str) -> CustomerAccount | None:
    return db_session.execute(select(CustomerAccount).where(CustomerAccount.email == email)).scalars().first()


def customer_has_account(customer_id) -> bool:
    return get_account_for_customer(customer_id) is not None


def _audit(action: str, account: CustomerAccount, *, actor_staff_user_id=None, reason: str | None = None, after: dict | None = None) -> None:
    audit_record(
        actor_staff_user_id=actor_staff_user_id, actor_role_snapshot=None, action_code=action,
        entity_type="customer_account", entity_public_id=str(account.id), reason=reason,
        after_state={"customer_id": str(account.customer_id), "status": account.status, **(after or {})},
    )


# -------------------------------------------------------------------- tokens

def _issue_token(account: CustomerAccount, kind: str, *, new_email: str | None = None, actor_staff_user_id=None) -> str:
    """Creates a token and REVOKES every earlier unused token of the same family
    (invite and reset are one family: only the newest link works). The caller
    commits. Returns the raw token."""
    now = _now()
    family = ("INVITE", "RESET") if kind in ("INVITE", "RESET") else (kind,)
    db_session.execute(
        update(CustomerAccountToken)
        .where(
            CustomerAccountToken.account_id == account.id,
            CustomerAccountToken.kind.in_(family),
            CustomerAccountToken.consumed_at.is_(None),
            CustomerAccountToken.revoked_at.is_(None),
        )
        .values(revoked_at=now)
    )
    raw = generate_token()
    db_session.add(CustomerAccountToken(
        account_id=account.id, kind=kind, token_hash=hash_token(raw), new_email=new_email,
        expires_at=now + TOKEN_TTL[kind], created_by_staff_user_id=actor_staff_user_id,
    ))
    db_session.flush()
    return raw


def _revoke_unused_links(account: CustomerAccount) -> None:
    """Kills every unused INVITE and RESET link of the account. A link is a bearer
    key sent to ONE mailbox; once the account's address changes (or is about to),
    a link still sitting in the old mailbox must not be able to set the password.
    The caller commits."""
    db_session.execute(
        update(CustomerAccountToken)
        .where(CustomerAccountToken.account_id == account.id,
               CustomerAccountToken.kind.in_(("INVITE", "RESET", "VERIFY_EMAIL")),
               CustomerAccountToken.consumed_at.is_(None), CustomerAccountToken.revoked_at.is_(None))
        .values(revoked_at=_now())
    )


def _consume_token(raw: str, kinds: tuple) -> CustomerAccountToken:
    """Looks the token up and marks it used in ONE conditional UPDATE, so two
    requests racing with the same link cannot both win. Raises
    AccountError('token_invalid') for unknown, wrong-kind, used, revoked or
    expired -- all the same answer."""
    now = _now()
    token = db_session.execute(
        select(CustomerAccountToken).where(CustomerAccountToken.token_hash == hash_token(raw or ""))
    ).scalars().first()
    if token is None or token.kind not in kinds:
        raise AccountError("token_invalid")
    result = db_session.execute(
        update(CustomerAccountToken)
        .where(
            CustomerAccountToken.id == token.id,
            CustomerAccountToken.consumed_at.is_(None),
            CustomerAccountToken.revoked_at.is_(None),
            CustomerAccountToken.expires_at > now,
        )
        .values(consumed_at=now)
    )
    if result.rowcount != 1:
        db_session.rollback()
        raise AccountError("token_invalid")
    db_session.refresh(token)
    return token


def peek_token_is_usable(raw: str, kinds: tuple) -> bool:
    now = _now()
    token = db_session.execute(
        select(CustomerAccountToken).where(CustomerAccountToken.token_hash == hash_token(raw or ""))
    ).scalars().first()
    return bool(token and token.kind in kinds and token.consumed_at is None and token.revoked_at is None
                and token.expires_at > now)


# ------------------------------------------------------------- staff actions

@dataclass(frozen=True)
class IssuedLink:
    """What a staff action produced: the link (shown ONCE when nothing was
    mailed) and whether a mail was queued."""
    account: CustomerAccount
    link: str
    emailed: bool


def _send_link(account: CustomerAccount, kind: str, raw: str, mail_kind: str, *, to_address: str | None = None) -> IssuedLink:
    link = mail.build_link(kind, raw, account.language)
    emailed = mail.queue(mail_kind, to_address or account.email, lang=account.language, link=link)
    return IssuedLink(account=account, link=link, emailed=emailed)


def create_account(customer: Customer, email: str, language: str, actor_staff_user_id) -> IssuedLink:
    """Staff pressed 'create website account'. One account per customer; one email
    per company. Status INVITED until the set-password link is used."""
    email = normalize_email(email)
    language = language if language in LANGUAGES else "en"
    if get_account_for_customer(customer.id) is not None:
        raise AccountError("account_exists")
    if get_account_by_email(email) is not None:
        raise AccountError("email_in_use")
    mail.build_link("INVITE", "x", language)  # fail BEFORE writing anything if the portal URL is not set
    account = CustomerAccount(customer_id=customer.id, email=email, language=language, status="INVITED",
                              session_version=1, created_by_staff_user_id=actor_staff_user_id)
    try:
        db_session.add(account)
        db_session.flush()
        raw = _issue_token(account, "INVITE", actor_staff_user_id=actor_staff_user_id)
        db_session.commit()
    except IntegrityError:
        # Two staff members raced past the checks above and the database's unique
        # constraint (one account per customer, one per email) caught the loser.
        # That is a refusal the screen can word, not a 500.
        db_session.rollback()
        if get_account_for_customer(customer.id) is not None:
            raise AccountError("account_exists")
        raise AccountError("email_in_use")
    _audit("CUSTOMER_ACCOUNT_CREATED", account, actor_staff_user_id=actor_staff_user_id, after={"language": language})
    return _send_link(account, "INVITE", raw, "customer_invitation")


def create_account_with_temp_password(
    customer: Customer, email: str, language: str, password: str, actor_staff_user_id
) -> tuple[CustomerAccount, "IssuedLink | None"]:
    """New customer flow: the seller is standing next to the customer, types their
    email and a TEMPORARY password, and the account is ACTIVE at once -- the
    customer signs in on the website with those two things and is made to choose
    their own password first (password_must_change). Differences from
    create_account (the staff 'send an invitation' path), each deliberate:
      - ACTIVE immediately: the customer has no mailbox round-trip before the
        trial can start, but email_verified_at stays NULL until they use the
        VERIFY_EMAIL link (the old rule 'ACTIVE implies verified' does not hold
        for these accounts; the column says so);
      - the password is checked against the customer-password policy (a seller
        must not be able to set 'password123' or the shop's own name);
      - the password is NEVER mailed and never audited; the mail carries only the
        verify link (or, with email off, the link is returned to be shown once).
    The caller (the onboarding action) holds the per-form lock; this commits its
    own rows. Returns (account, verify link or None when the portal URL is unset)."""
    email = normalize_email(email)
    language = language if language in LANGUAGES else "en"
    if get_account_for_customer(customer.id) is not None:
        raise AccountError("account_exists")
    if get_account_by_email(email) is not None:
        raise AccountError("email_in_use")
    policy.validate(password, email=email, legal_name=customer.legal_name or "", trade_name=customer.trade_name or "")
    now = _now()
    account = CustomerAccount(
        customer_id=customer.id, email=email, language=language, status="ACTIVE",
        password_hash=passwords.hash_secret_unchecked(password), session_version=1,
        password_changed_at=now, activated_at=now, password_must_change=True,
        password_set_by_staff_user_id=actor_staff_user_id, created_by_staff_user_id=actor_staff_user_id,
    )
    try:
        db_session.add(account)
        db_session.flush()
        raw = _issue_token(account, "VERIFY_EMAIL", actor_staff_user_id=actor_staff_user_id)
        db_session.commit()
    except IntegrityError:
        db_session.rollback()
        if get_account_for_customer(customer.id) is not None:
            raise AccountError("account_exists")
        raise AccountError("email_in_use")
    _audit("CUSTOMER_ACCOUNT_CREATED_WITH_TEMP_PASSWORD", account, actor_staff_user_id=actor_staff_user_id,
           after={"language": language, "password_must_change": True})
    try:
        return account, _send_link(account, "VERIFY_EMAIL", raw, "customer_welcome")
    except mail.PortalUrlNotSet:
        return account, None


def verify_email_with_token(raw_token: str, ip: str) -> CustomerAccount:
    """The customer opened the VERIFY_EMAIL link: the mailbox is theirs."""
    _take_token_slot(ip)
    token = _consume_token(raw_token, ("VERIFY_EMAIL",))
    account = db_session.get(CustomerAccount, token.account_id)
    if account is None or account.status == "DISABLED":
        db_session.rollback()
        raise AccountError("token_invalid")
    if account.email_verified_at is None:
        account.email_verified_at = _now()
    db_session.commit()
    _audit("CUSTOMER_ACCOUNT_EMAIL_VERIFIED", account)
    return account


def resend_invitation(account: CustomerAccount, actor_staff_user_id) -> IssuedLink:
    if account.status != "INVITED":
        raise AccountError("not_invited")
    raw = _issue_token(account, "INVITE", actor_staff_user_id=actor_staff_user_id)
    db_session.commit()
    _audit("CUSTOMER_ACCOUNT_INVITE_RESENT", account, actor_staff_user_id=actor_staff_user_id)
    return _send_link(account, "INVITE", raw, "customer_invitation")


def issue_reset_link(account: CustomerAccount, actor_staff_user_id) -> IssuedLink:
    """Staff-initiated password reset. Changes no password and no licence by
    itself; the customer sets the new one."""
    if account.status != "ACTIVE":
        raise AccountError("not_active")
    raw = _issue_token(account, "RESET", actor_staff_user_id=actor_staff_user_id)
    db_session.commit()
    _audit("CUSTOMER_ACCOUNT_RESET_LINK_ISSUED", account, actor_staff_user_id=actor_staff_user_id,
           after={"link_shown_to_staff": not mail.email_is_set_up()})
    return _send_link(account, "RESET", raw, "customer_password_reset")


def sign_out_everywhere(account: CustomerAccount, actor_staff_user_id, *, reason: str = "STAFF_SIGN_OUT_EVERYWHERE") -> int:
    revoked = _revoke_all_sessions(account, reason, bump_version=True)
    db_session.commit()
    _audit("CUSTOMER_ACCOUNT_SESSIONS_REVOKED", account, actor_staff_user_id=actor_staff_user_id, after={"sessions": revoked})
    return revoked


def disable_account(account: CustomerAccount, reason: str, actor_staff_user_id) -> None:
    """Blocks SIGN-IN only. Devices and licences keep working: no licence code is
    reachable from here. Outstanding invite/reset links die with it."""
    reason = (reason or "").strip()
    if not reason:
        raise AccountError("reason_required")
    if account.status == "DISABLED":
        return
    now = _now()
    account.status = "DISABLED"
    account.disabled_at = now
    account.disabled_reason = reason
    account.disabled_by_staff_user_id = actor_staff_user_id
    _revoke_all_sessions(account, "ACCOUNT_DISABLED", bump_version=True)
    db_session.execute(
        update(CustomerAccountToken)
        .where(CustomerAccountToken.account_id == account.id, CustomerAccountToken.consumed_at.is_(None),
               CustomerAccountToken.revoked_at.is_(None))
        .values(revoked_at=now)
    )
    db_session.commit()
    _audit("CUSTOMER_ACCOUNT_DISABLED", account, actor_staff_user_id=actor_staff_user_id, reason=reason)


def enable_account(account: CustomerAccount, actor_staff_user_id) -> None:
    """Back to ACTIVE when a password exists, else INVITED (staff then resend the
    invitation -- disabling revoked the old link)."""
    if account.status != "DISABLED":
        return
    account.status = "ACTIVE" if account.password_hash else "INVITED"
    account.disabled_at = None
    account.disabled_reason = None
    account.disabled_by_staff_user_id = None
    db_session.commit()
    _audit("CUSTOMER_ACCOUNT_ENABLED", account, actor_staff_user_id=actor_staff_user_id)


def request_email_change(account: CustomerAccount, new_email: str, reason: str, actor_staff_user_id) -> IssuedLink:
    """Staff, with a reason and recent auth. The address stays as it is until the
    link sent to the NEW address is used; the OLD address gets a notice now."""
    new_email = normalize_email(new_email)
    reason = (reason or "").strip()
    if not reason:
        raise AccountError("reason_required")
    if new_email == account.email:
        raise AccountError("same_email")
    if get_account_by_email(new_email) is not None:
        raise AccountError("email_in_use")
    if account.status == "DISABLED":
        raise AccountError("account_disabled")
    if account.status == "INVITED":
        # The invitation already went to the OLD address, which staff are replacing
        # (usually because it was mistyped). Whoever owns that mailbox must not be
        # able to activate the account in the meantime. After the new address is
        # confirmed the account is still INVITED and staff press 'resend' to send
        # a fresh invitation to it -- confirming cannot do that itself: the person
        # confirming is the website visitor and, with email off, nobody would be
        # shown the new link.
        _revoke_unused_links(account)
    raw = _issue_token(account, "EMAIL_CHANGE", new_email=new_email, actor_staff_user_id=actor_staff_user_id)
    db_session.commit()
    _audit("CUSTOMER_ACCOUNT_EMAIL_CHANGE_REQUESTED", account, actor_staff_user_id=actor_staff_user_id, reason=reason,
           after={"new_email_masked": mail.mask_email(new_email)})
    mail.queue("customer_email_changed_notice", account.email, lang=account.language, lines=[mail.mask_email(new_email)])
    return _send_link(account, "EMAIL_CHANGE", raw, "customer_email_change", to_address=new_email)


def confirm_email_change(raw_token: str, ip: str) -> CustomerAccount:
    _take_token_slot(ip)
    token = _consume_token(raw_token, ("EMAIL_CHANGE",))
    account = db_session.get(CustomerAccount, token.account_id)
    if account is None or account.status == "DISABLED":
        raise AccountError("token_invalid")
    if get_account_by_email(token.new_email) is not None:
        raise AccountError("email_in_use")
    old = account.email
    account.email = token.new_email
    # The link was sent to the NEW address and used, so that mailbox is proven.
    account.email_verified_at = _now()
    _revoke_all_sessions(account, "EMAIL_CHANGED", bump_version=True)
    # Any invite/reset link was mailed to the address that is going away.
    _revoke_unused_links(account)
    try:
        db_session.commit()
    except IntegrityError:
        # Someone took the address between the check above and now.
        db_session.rollback()
        raise AccountError("email_in_use")
    _audit("CUSTOMER_ACCOUNT_EMAIL_CHANGED", account, after={"new_email_masked": mail.mask_email(account.email)})
    mail.queue("customer_email_changed_notice", old, lang=account.language, lines=[mail.mask_email(account.email)])
    return account


# ----------------------------------------------------------------- passwords

def _verify_password(password: str, stored_hash: str) -> bool:
    """The one place a customer password is checked. Tests count calls to it."""
    return passwords.verify_password(password, stored_hash)


_dummy_hash: str | None = None


def _dummy() -> str:
    global _dummy_hash
    if _dummy_hash is None:
        _dummy_hash = passwords.hash_secret_unchecked("dummy-password-for-unknown-accounts")
    return _dummy_hash


def _policy_check(account: CustomerAccount, password: str) -> None:
    customer = db_session.get(Customer, account.customer_id)
    policy.validate(
        password, email=account.email,
        legal_name=customer.legal_name if customer else "", trade_name=(customer.trade_name if customer else "") or "",
    )


def _take_token_slot(ip: str) -> None:
    try:
        ratelimit.check_and_increment("customer_token_ip", ratelimit.safe_bucket_component(ip_bucket(ip)))
    except ratelimit.RateLimitExceeded as exc:
        raise SignInLocked(exc.retry_after_seconds)


def _apply_new_password(account: CustomerAccount, password: str) -> None:
    now = _now()
    account.password_hash = passwords.hash_secret_unchecked(password)
    account.password_changed_at = now
    # The customer choosing their own password is what ends "temporary password"
    # (new customer flow): a seller-typed one is cleared by every path that sets
    # a password the customer picked -- link, reset or signed-in change.
    account.password_must_change = False
    if account.status == "INVITED":
        account.status = "ACTIVE"
    if account.activated_at is None:
        account.activated_at = now
    _revoke_all_sessions(account, "PASSWORD_CHANGED", bump_version=True)
    db_session.execute(
        update(CustomerAccountToken)
        .where(CustomerAccountToken.account_id == account.id, CustomerAccountToken.kind.in_(("INVITE", "RESET")),
               CustomerAccountToken.consumed_at.is_(None), CustomerAccountToken.revoked_at.is_(None))
        .values(revoked_at=now)
    )


def set_password_with_token(raw_token: str, password: str, ip: str) -> CustomerAccount:
    """The ONLY way an account becomes ACTIVE (and so the email check): the
    invite / reset link. The token is checked and the password policy applied
    BEFORE the token is spent, so a too-weak password does not burn the link."""
    _take_token_slot(ip)
    if not peek_token_is_usable(raw_token, ("INVITE", "RESET")):
        raise AccountError("token_invalid")
    stored = db_session.execute(
        select(CustomerAccountToken).where(CustomerAccountToken.token_hash == hash_token(raw_token))
    ).scalars().first()
    account = db_session.get(CustomerAccount, stored.account_id)
    if account is None or account.status == "DISABLED":
        raise AccountError("token_invalid")
    _policy_check(account, password)  # PasswordPolicyError: link stays usable
    token = _consume_token(raw_token, ("INVITE", "RESET"))
    was_reset = token.kind == "RESET"
    _apply_new_password(account, password)
    db_session.commit()
    _audit("CUSTOMER_ACCOUNT_PASSWORD_SET", account, after={"via": token.kind})
    if was_reset:
        mail.queue("customer_password_changed", account.email, lang=account.language)
    return account


def change_password(session_token: str, current_password: str, new_password: str, ip: str, user_agent: str | None):
    """Signed-in change. Ends every other session and returns a fresh one so the
    person stays signed in. Wrong current password is rate-limited like a sign-in."""
    found = get_session(session_token)
    if found is None:
        raise AccountError("session_invalid")
    _, account = found
    _take_signin_slots(account.email, ip)
    if not _verify_password(current_password, account.password_hash or ""):
        raise SignInFailed()
    _policy_check(account, new_password)
    _apply_new_password(account, new_password)
    new_token = _create_session(account, ip, user_agent)
    db_session.commit()
    _audit("CUSTOMER_ACCOUNT_PASSWORD_CHANGED", account)
    mail.queue("customer_password_changed", account.email, lang=account.language)
    return new_token, account


def forgot_password(email: str, ip: str, lang: str | None = None) -> None:
    """Always returns None -- the caller answers the same whatever happened. Only
    the per-IP flood guard may raise (SignInLocked), which says nothing about the
    address. A third request for one address within an hour is silently dropped."""
    try:
        ratelimit.check_and_increment("customer_forgot_ip", ratelimit.safe_bucket_component(ip_bucket(ip)))
    except ratelimit.RateLimitExceeded as exc:
        raise SignInLocked(exc.retry_after_seconds)
    try:
        normalized = normalize_email(email)
    except AccountError:
        return
    if not mail.email_is_set_up():
        # Email is off, so nothing can reach the customer from here. Issuing a
        # token anyway would revoke the invite/reset link staff just handed over
        # by hand (one family: only the newest works) and mail nothing -- anyone
        # who knew the address could kill it from the public website. No token,
        # no revocation, same public answer. (No address in the line.)
        logger.info("forgot-password declined: customer email is not set up on this server")
        return
    try:
        ratelimit.check_and_increment("customer_forgot_address", ratelimit.safe_bucket_component(normalized))
    except ratelimit.RateLimitExceeded:
        return
    account = get_account_by_email(normalized)
    if account is None or account.status == "DISABLED":
        return
    try:
        if account.status == "ACTIVE":
            raw = _issue_token(account, "RESET")
            db_session.commit()
            _audit("CUSTOMER_ACCOUNT_RESET_REQUESTED", account)
            _send_link(account, "RESET", raw, "customer_password_reset")
        else:  # INVITED and lost the first mail: a fresh invitation
            raw = _issue_token(account, "INVITE")
            db_session.commit()
            _send_link(account, "INVITE", raw, "customer_invitation")
    except mail.PortalUrlNotSet:
        db_session.rollback()
        logger.warning("forgot-password: OWNER_CUSTOMER_PORTAL_BASE_URL is not set; nothing sent")


# ----------------------------------------------------------------- sign-in

def _take_signin_slots(email: str, ip: str) -> None:
    e, i = ratelimit.safe_bucket_component(email), ratelimit.safe_bucket_component(ip_bucket(ip))
    for policy_name, key in (("customer_signin_pair", f"{e}:{i}"), ("customer_signin_email", e), ("customer_signin_ip", i)):
        try:
            ratelimit.check_and_increment(policy_name, key)
        except ratelimit.RateLimitExceeded as exc:
            raise SignInLocked(exc.retry_after_seconds)


def _record_attempt(email: str, ip: str, success: bool, reason: str | None) -> None:
    db_session.add(CustomerLoginAttempt(email_hash=_digest(email), ip_hash=_digest(ip), success=success, reason=reason))
    db_session.commit()


def sign_in(email: str, password: str, ip: str, user_agent: str | None):
    """Returns (raw_session_token, account) or raises SignInFailed / SignInLocked.

    No user enumeration: every failure raises the same SignInFailed, and an
    unknown email, an INVITED/DISABLED account and a wrong password ALL run
    exactly one Argon2 verification (against a dummy hash when there is nothing
    real to check), so neither the answer nor the time says which it was."""
    try:
        normalized = normalize_email(email)
    except AccountError:
        normalized = (email or "").strip().lower()[:254]
        valid_shape = False
    else:
        valid_shape = True
    try:
        _take_signin_slots(normalized, ip)
    except SignInLocked:
        _record_attempt(normalized, ip, False, "LOCKED")
        raise
    account = get_account_by_email(normalized) if valid_shape else None
    if account is None or account.status != "ACTIVE" or not account.password_hash:
        _verify_password(password or "", _dummy())
        reason = "UNKNOWN" if account is None else ("DISABLED" if account.status == "DISABLED" else "NOT_ACTIVE")
        _record_attempt(normalized, ip, False, reason)
        raise SignInFailed()
    if not _verify_password(password or "", account.password_hash):
        _record_attempt(normalized, ip, False, "BAD_PASSWORD")
        raise SignInFailed()
    if passwords.needs_rehash(account.password_hash):
        account.password_hash = passwords.hash_secret_unchecked(password)
    account.last_sign_in_at = _now()
    token = _create_session(account, ip, user_agent)
    _record_attempt(normalized, ip, True, None)
    return token, account


# ----------------------------------------------------------------- sessions

def _create_session(account: CustomerAccount, ip: str, user_agent: str | None) -> str:
    now = _now()
    raw = generate_token()
    db_session.add(CustomerSession(
        account_id=account.id, token_hash=hash_token(raw), session_version_at_login=account.session_version,
        ip_address=(ip or "")[:64] or None, user_agent=(user_agent or "")[:512] or None,
        last_seen_at=now, absolute_expires_at=now + SESSION_ABSOLUTE,
    ))
    db_session.flush()
    return raw


def get_session(raw_token: str):
    """(session, account) for a live session, else None. Live means: not revoked,
    inside the 12 h absolute limit, used within the last 60 min, created under the
    account's CURRENT session_version, and the account is ACTIVE. Touches last_seen."""
    if not raw_token:
        return None
    now = _now()
    row = db_session.execute(
        select(CustomerSession).where(CustomerSession.token_hash == hash_token(raw_token))
    ).scalars().first()
    if row is None or row.revoked_at is not None or now >= row.absolute_expires_at or now - row.last_seen_at > SESSION_IDLE:
        return None
    account = db_session.get(CustomerAccount, row.account_id)
    if account is None or account.status != "ACTIVE" or account.session_version != row.session_version_at_login:
        return None
    row.last_seen_at = now
    db_session.commit()
    return row, account


def sign_out(raw_token: str) -> None:
    now = _now()
    db_session.execute(
        update(CustomerSession)
        .where(CustomerSession.token_hash == hash_token(raw_token or ""), CustomerSession.revoked_at.is_(None))
        .values(revoked_at=now, revoked_reason="SIGNED_OUT")
    )
    db_session.commit()


def _revoke_all_sessions(account: CustomerAccount, reason: str, *, bump_version: bool) -> int:
    result = db_session.execute(
        update(CustomerSession)
        .where(CustomerSession.account_id == account.id, CustomerSession.revoked_at.is_(None))
        .values(revoked_at=_now(), revoked_reason=reason)
    )
    if bump_version:
        account.session_version = account.session_version + 1
    return result.rowcount
