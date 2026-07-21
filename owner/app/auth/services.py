"""Login/authentication service logic (Part E), separate from route glue so the
future activation API or a CLI can reuse it without touching Flask routing."""
from __future__ import annotations

from sqlalchemy import select

from app.extensions import db_session
from app.models.base import utcnow
from app.models.staff import StaffUser
from app.security.passwords import verify_password
from app.security.ratelimit import is_locked_out, record_attempt

MAX_ATTEMPTS_DEFAULT = 5
LOCKOUT_SECONDS_DEFAULT = 900


def find_staff_by_email(email: str) -> StaffUser | None:
    stmt = select(StaffUser).where(StaffUser.email == email.strip().lower())
    return db_session.execute(stmt).scalars().first()


def authenticate(
    email: str, password: str, ip_address: str | None, max_attempts: int, lockout_seconds: int
) -> tuple[StaffUser | None, str]:
    """Returns (staff_user_or_None, reason). reason is one of:
    ok / locked_out / invalid_credentials / account_disabled.
    Deliberately does not distinguish "no such email" from "wrong password" in
    the reason string surfaced to the caller for display (Part E)."""
    email_norm = (email or "").strip().lower()
    if is_locked_out(email_norm, ip_address, max_attempts, lockout_seconds):
        record_attempt(email_norm, ip_address, success=False, reason="locked_out")
        return None, "locked_out"

    staff = find_staff_by_email(email_norm)
    if staff is None or not verify_password(password, staff.password_hash):
        record_attempt(email_norm, ip_address, success=False, reason="invalid_credentials")
        return None, "invalid_credentials"

    if not staff.is_active or staff.disabled_at is not None:
        record_attempt(email_norm, ip_address, success=False, reason="account_disabled")
        return None, "account_disabled"

    staff.last_login_at = utcnow()
    db_session.commit()
    record_attempt(email_norm, ip_address, success=True)
    return staff, "ok"
