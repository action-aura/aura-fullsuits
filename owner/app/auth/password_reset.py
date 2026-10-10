"""Staff password reset -- the tokenless design (no table, no stored token).

A reset link carries a signed, timed payload and nothing else is stored:

    URLSafeTimedSerializer(SECRET_KEY, salt="owner-staff-password-reset")
        payload {"uid": <staff id>, "ph": <first 16 hex of sha256(password_hash)>}
        max_age 30 minutes

It is valid only while the account still exists, is active and not disabled,
AND `ph` still matches the account's CURRENT password hash. That last check is
what makes a link single-use without a used-token table: the moment the
password changes -- by this very reset, or by an ordinary change-password -- the
hash changes and every outstanding link dies. (A link can also be used again
within its 30 minutes only if the password did not change, which it always does
on a successful use.)

Resetting a password NEVER touches MFA: the person still needs their
authenticator code at the next login, so a reset link on its own is not enough
to get in.

request_reset() is the whole of the "forgot password" decision and is written so
that nothing observable differs between an unknown address, a disabled account,
a rate-limited address and a real one: the caller always shows the same page.
"""
from __future__ import annotations

import hashlib
import logging

from flask import current_app, url_for
from itsdangerous import BadData, URLSafeTimedSerializer

from app.auth.services import find_staff_by_email
from app.extensions import db_session
from app.models.staff import StaffUser
from app.security.ratelimit import count_reset_requests, record_reset_request

logger = logging.getLogger(__name__)

SALT = "owner-staff-password-reset"
MAX_AGE_SECONDS = 30 * 60
REQUESTS_PER_HOUR = 3
_HTTPS_ENVS = ("staging", "production")


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(current_app.config["SECRET_KEY"], salt=SALT)


def _password_fingerprint(staff: StaffUser) -> str:
    return hashlib.sha256((staff.password_hash or "").encode("utf-8")).hexdigest()[:16]


def make_token(staff: StaffUser) -> str:
    return _serializer().dumps({"uid": str(staff.id), "ph": _password_fingerprint(staff)})


def staff_for_token(token: str) -> StaffUser | None:
    """The account a token is still good for, or None for ANY reason (bad
    signature, expired, unknown or disabled account, password changed since).
    One undifferentiated failure on purpose."""
    if not token:
        return None
    try:
        payload = _serializer().loads(token, max_age=MAX_AGE_SECONDS)
    except BadData:
        return None
    if not isinstance(payload, dict) or not isinstance(payload.get("uid"), str) or not isinstance(payload.get("ph"), str):
        return None
    try:
        import uuid

        staff = db_session.get(StaffUser, uuid.UUID(payload["uid"]))
    except ValueError:
        return None
    if staff is None or not staff.is_active or staff.disabled_at is not None:
        return None
    if payload["ph"] != _password_fingerprint(staff):
        return None
    return staff


def build_link(token: str) -> str:
    kwargs = {"token": token, "_external": True}
    if str(current_app.config.get("ENV", "")).lower() in _HTTPS_ENVS:
        kwargs["_scheme"] = "https"
    return url_for("auth.reset_password_form", **kwargs)


def request_reset(email: str, ip_address: str | None) -> None:
    """Everything behind the forgot-password form. Returns nothing: the route
    shows the same page whatever happened here."""
    email_norm = (email or "").strip().lower()
    if not email_norm or len(email_norm) > 254:
        return
    # Counted BEFORE looking at the account and for every address alike, so the
    # limit cannot be used to tell a real address from an unknown one.
    over_limit = count_reset_requests(email_norm) >= REQUESTS_PER_HOUR
    record_reset_request(email_norm, ip_address)
    if over_limit:
        return

    staff = find_staff_by_email(email_norm)
    if staff is None or not staff.is_active or staff.disabled_at is not None:
        return

    from app.mail.invitations import email_is_set_up, queue_staff_password_reset_email

    if not email_is_set_up():
        return
    queue_staff_password_reset_email(staff.email, build_link(make_token(staff)))
