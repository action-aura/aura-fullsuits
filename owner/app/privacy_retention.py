"""Retention for the two personal-data stores SEC-09 found growing forever:
relayed/owned e-mail bodies (`owner_email_outbox`) and login telemetry
(`owner_login_attempts`: attempted address + client IP per attempt).
Run from `flask commercial housekeeping` (nightly).

E-MAIL: a SENT / FAILED_PERMANENT / CANCELLED row keeps its metadata for audit
(kind, status, attempt_count, source, installation_id, timestamps, recipient
DOMAIN) but after N days (default 30) loses everything that can identify a
person or carry shop data: body_text -> '', body_html -> NULL, subject ->
REDACTED_SUBJECT, to_address -> '***@<domain>'. Rows still QUEUED/SENDING are
NEVER touched, however old -- they have not been delivered yet.
No schema change: the redacted subject is the "already cleared" marker, which
keeps the job idempotent and re-countable (dry-run count == apply count).

SAFETY FLOOR: the relay's abuse caps (app/mail/relay.py) read to_address and
created_at over windows of at most one day, so the e-mail window is clamped to
>= MIN_EMAIL_DAYS; a mis-set environment variable can never blind the caps.

CUSTOMER WEBSITE ACCOUNTS (Step 2 CA1): customer login attempts (hashed email / IP)
are deleted after 90 days; customer sessions and account tokens 30 days after they
EXPIRED (a live one is never touched); and the body of an invitation / reset /
email-change mail -- which IS a one-time link -- is cleared as soon as the message
is finished (the sender already clears it at SENT; this catches stragglers and
FAILED/CANCELLED rows).

LOGIN ATTEMPTS: rows older than N days (default 180) are deleted. The lockout
and reset throttles look back minutes to an hour, so this cannot loosen them.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, delete, func, select, update

from app.extensions import db_session
from app.mail.templates import LINK_BEARING_KINDS
from app.models.customer_accounts import CustomerAccountToken, CustomerLoginAttempt, CustomerSession
from app.models.email_outbox import EmailOutbox
from app.models.staff import LoginAttempt

DEFAULT_EMAIL_BODY_DAYS = 30
DEFAULT_LOGIN_ATTEMPT_DAYS = 180
MIN_EMAIL_DAYS = 2
REDACTED_SUBJECT = "[redacted]"
_FINISHED_STATES = ("SENT", "FAILED_PERMANENT", "CANCELLED")


def _days_from_env(name: str, default: int, floor: int = 1) -> int:
    raw = os.environ.get(name, "").strip()
    try:
        value = int(raw) if raw else default
    except ValueError:
        return default
    return max(value, floor)


def email_body_days() -> int:
    return _days_from_env("OWNER_EMAIL_BODY_RETENTION_DAYS", DEFAULT_EMAIL_BODY_DAYS, MIN_EMAIL_DAYS)


def login_attempt_days() -> int:
    return _days_from_env("OWNER_LOGIN_ATTEMPT_RETENTION_DAYS", DEFAULT_LOGIN_ATTEMPT_DAYS)


def _email_condition(now: datetime, days: int):
    cutoff = now - timedelta(days=days)
    return and_(
        EmailOutbox.status.in_(_FINISHED_STATES),
        func.coalesce(EmailOutbox.sent_at, EmailOutbox.created_at) < cutoff,
        EmailOutbox.subject != REDACTED_SUBJECT,
    )


def count_email_bodies_to_clear(*, now: datetime | None = None, days: int | None = None) -> int:
    now = now or datetime.now(timezone.utc)
    return db_session.execute(
        select(func.count(EmailOutbox.id)).where(_email_condition(now, days or email_body_days()))
    ).scalar_one()


def clear_old_email_bodies(*, now: datetime | None = None, days: int | None = None) -> int:
    now = now or datetime.now(timezone.utc)
    result = db_session.execute(
        update(EmailOutbox)
        .where(_email_condition(now, days or email_body_days()))
        .values(
            body_text="",
            body_html=None,
            subject=REDACTED_SUBJECT,
            to_address=func.concat("***@", func.substring(EmailOutbox.to_address, r"@([^@]*)$")),
        )
    )
    db_session.commit()
    return result.rowcount


def count_login_attempts_to_purge(*, now: datetime | None = None, days: int | None = None) -> int:
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=days or login_attempt_days())
    return db_session.execute(
        select(func.count(LoginAttempt.id)).where(LoginAttempt.created_at < cutoff)
    ).scalar_one()


def purge_old_login_attempts(*, now: datetime | None = None, days: int | None = None) -> int:
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=days or login_attempt_days())
    result = db_session.execute(delete(LoginAttempt).where(LoginAttempt.created_at < cutoff))
    db_session.commit()
    return result.rowcount


# --- Step 2 CA1: customer website accounts -------------------------------------
CUSTOMER_LOGIN_ATTEMPT_DAYS = 90
CUSTOMER_EXPIRED_GRACE_DAYS = 30


def _cutoff(now, days):
    return (now or datetime.now(timezone.utc)) - timedelta(days=days)


def count_customer_login_attempts_to_purge(*, now: datetime | None = None) -> int:
    return db_session.execute(select(func.count(CustomerLoginAttempt.id)).where(
        CustomerLoginAttempt.created_at < _cutoff(now, CUSTOMER_LOGIN_ATTEMPT_DAYS))).scalar_one()


def purge_customer_login_attempts(*, now: datetime | None = None) -> int:
    result = db_session.execute(delete(CustomerLoginAttempt).where(
        CustomerLoginAttempt.created_at < _cutoff(now, CUSTOMER_LOGIN_ATTEMPT_DAYS)))
    db_session.commit()
    return result.rowcount


def count_expired_customer_sessions_and_tokens(*, now: datetime | None = None) -> int:
    cutoff = _cutoff(now, CUSTOMER_EXPIRED_GRACE_DAYS)
    sessions = db_session.execute(select(func.count(CustomerSession.id)).where(
        CustomerSession.absolute_expires_at < cutoff)).scalar_one()
    tokens = db_session.execute(select(func.count(CustomerAccountToken.id)).where(
        CustomerAccountToken.expires_at < cutoff)).scalar_one()
    return sessions + tokens


def purge_expired_customer_sessions_and_tokens(*, now: datetime | None = None) -> int:
    cutoff = _cutoff(now, CUSTOMER_EXPIRED_GRACE_DAYS)
    sessions = db_session.execute(delete(CustomerSession).where(CustomerSession.absolute_expires_at < cutoff)).rowcount
    tokens = db_session.execute(delete(CustomerAccountToken).where(CustomerAccountToken.expires_at < cutoff)).rowcount
    db_session.commit()
    return sessions + tokens


def _link_mail_condition():
    return and_(
        EmailOutbox.kind.in_(sorted(LINK_BEARING_KINDS)),
        EmailOutbox.status.in_(_FINISHED_STATES),
        EmailOutbox.body_text != "",
    )


def count_customer_link_mail_bodies_to_clear() -> int:
    return db_session.execute(select(func.count(EmailOutbox.id)).where(_link_mail_condition())).scalar_one()


def clear_customer_link_mail_bodies() -> int:
    """Not time-based: a finished link mail has no reason to keep its token. Rows
    still QUEUED/SENDING are never touched (they have not been delivered)."""
    result = db_session.execute(update(EmailOutbox).where(_link_mail_condition()).values(body_text="", body_html=None))
    db_session.commit()
    return result.rowcount
