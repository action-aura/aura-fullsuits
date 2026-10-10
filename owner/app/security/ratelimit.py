"""Login/MFA/recovery-code throttling, backed by the persistent
owner_login_attempts table (real PostgreSQL, shared by every Gunicorn
worker -- not an in-memory counter).

Phase 9R M7: owner-threat-model.md #14 (Phase 5) describes this as a
"single-process" limitation needing Redis at scale -- that was accurate
when threat #14 was written, but not for the current implementation: every
worker process queries the same Postgres table via the same
is_locked_out()/record_attempt() functions below, so the limit is already
enforced consistently across every worker, not per-process. Verified by
owner/tests/test_phase9r_rate_limit_multi_worker.py, which simulates two
independent worker processes as two separate DB sessions. The threat-model
entry is stale and should be read historically, not as current status."""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select

from app.extensions import db_session
from app.models.base import utcnow
from app.models.staff import LoginAttempt


def record_attempt(email: str, ip_address: str | None, success: bool, reason: str | None = None) -> None:
    db_session.add(LoginAttempt(email_attempted=email.strip().lower(), ip_address=ip_address, success=success, reason=reason))
    db_session.commit()


_RESET_KEY_PREFIX = "pwreset:"
_RESET_REASON = "password_reset_request"
_RESET_WINDOW_SECONDS = 3600


def _reset_key(email: str) -> str:
    """Password-reset requests are throttled in the same table but under a
    NAMESPACED key (a hash, never the address), so they can never be mistaken
    for -- or reset -- the login lockout above, which matches the plain
    address. (A success row under the real address would unlock a locked-out
    account; a failure row would lock a person out of logging in by merely
    asking for a reset.)"""
    import hashlib

    return _RESET_KEY_PREFIX + hashlib.sha256(email.strip().lower().encode("utf-8")).hexdigest()


def record_reset_request(email: str, ip_address: str | None) -> None:
    db_session.add(LoginAttempt(email_attempted=_reset_key(email), ip_address=ip_address, success=False, reason=_RESET_REASON))
    db_session.commit()


def count_reset_requests(email: str) -> int:
    """Reset requests for this address in the last hour (known or unknown
    address alike -- the caller counts every request)."""
    window_start = utcnow() - timedelta(seconds=_RESET_WINDOW_SECONDS)
    stmt = (
        select(LoginAttempt)
        .where(LoginAttempt.email_attempted == _reset_key(email))
        .where(LoginAttempt.created_at >= window_start)
    )
    return len(db_session.execute(stmt).scalars().all())


def is_locked_out(email: str, ip_address: str | None, max_attempts: int, lockout_seconds: int) -> bool:
    window_start = utcnow() - timedelta(seconds=lockout_seconds)
    stmt = (
        select(LoginAttempt)
        .where(LoginAttempt.email_attempted == email.strip().lower())
        .where(LoginAttempt.created_at >= window_start)
        .where(LoginAttempt.success.is_(False))
        .order_by(LoginAttempt.created_at.desc())
    )
    recent_failures = db_session.execute(stmt).scalars().all()
    if len(recent_failures) < max_attempts:
        return False
    # A successful login after the most recent failure resets the counter.
    success_stmt = (
        select(LoginAttempt)
        .where(LoginAttempt.email_attempted == email.strip().lower())
        .where(LoginAttempt.success.is_(True))
        .where(LoginAttempt.created_at >= recent_failures[0].created_at)
    )
    return db_session.execute(success_stmt).scalars().first() is None
