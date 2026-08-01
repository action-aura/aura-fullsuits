"""Server-side session management (Part E).

The browser only ever holds an opaque random bearer token in the
`owner_session` cookie (HttpOnly, SameSite=Lax, Secure in production). The
database (owner_staff_sessions) is the sole source of truth for validity,
expiry, idle timeout, and revocation -- this is what "server-side session
storage" means concretely, distinct from Flask's own signed client-side
`session` (which this app uses only for CSRF token storage, a separate concern).
"""
from __future__ import annotations

import uuid
from datetime import timedelta

from flask import current_app, g, request
from sqlalchemy import select

from app.extensions import db_session
from app.models.base import utcnow
from app.models.staff import StaffSession, StaffUser
from app.security.tokens import generate_token, hash_token

COOKIE_NAME = "owner_session"


def create_session(staff_user: StaffUser) -> str:
    raw_token = generate_token()
    now = utcnow()
    absolute_seconds = current_app.config["PERMANENT_SESSION_LIFETIME_SECONDS"]
    record = StaffSession(
        staff_user_id=staff_user.id,
        token_hash=hash_token(raw_token),
        session_version_at_login=staff_user.session_version,
        ip_address=request.remote_addr,
        user_agent=(request.user_agent.string or "")[:512] if request.user_agent else None,
        last_seen_at=now,
        expires_at=now + timedelta(seconds=absolute_seconds),
    )
    db_session.add(record)
    db_session.commit()
    # The caller's response cookie will carry THIS session's token from here
    # on, but the request already in flight was received with the PRIOR
    # cookie (if any) -- request.cookies never reflects a cookie this same
    # response is only now about to set. Without this, a same-request
    # follow-up call like mark_mfa_verified() (auth/routes.py's
    # mfa_verify_submit, which deliberately rotates the session on
    # privilege escalation from pre-MFA to post-MFA as a session-fixation
    # defense) would fall through to load_current_staff()'s cookie lookup,
    # find the OLD pre-MFA session instead, and mark that one -- leaving
    # the NEW session (the one the client actually ends up holding) never
    # marked as recently-authenticated. Caching the just-created session
    # onto `g` here makes "the session I just created is the current
    # session for the rest of this request" hold unconditionally, for
    # every caller, not just this one call site.
    g.staff_session = record
    g.staff_user = staff_user
    _activate_pending_employee_profile(staff_user)
    return raw_token


def _activate_pending_employee_profile(staff_user: StaffUser) -> None:
    """Phase 9.5B: create_session() is the one real choke point every login
    path already calls at the moment a full session is established (plain
    login, MFA verify, first-time forced MFA enrollment, post-password-change
    re-login) -- so it's the correct single place to activate a still-PENDING
    EmployeeProfile on first successful login, rather than duplicating this
    check in every route that calls create_session(). Local import avoids a
    package-load cycle (employees.services imports auth.session)."""
    from app.models.employees import EmployeeProfile

    profile = db_session.execute(
        select(EmployeeProfile).where(EmployeeProfile.staff_user_id == staff_user.id)
    ).scalars().first()
    if profile is not None and profile.employment_status == "PENDING":
        from app.employees.services import activate_employee

        activate_employee(profile, actor_staff_user_id=staff_user.id)


def _get_valid_session(raw_token: str) -> StaffSession | None:
    if not raw_token:
        return None
    token_hash = hash_token(raw_token)
    stmt = select(StaffSession).where(StaffSession.token_hash == token_hash)
    record = db_session.execute(stmt).scalars().first()
    if record is None or record.revoked_at is not None:
        return None
    now = utcnow()
    if record.expires_at <= now:
        return None
    idle_seconds = current_app.config["SESSION_IDLE_TIMEOUT_SECONDS"]
    if (now - record.last_seen_at).total_seconds() > idle_seconds:
        return None
    staff = db_session.get(StaffUser, record.staff_user_id)
    if staff is None or not staff.is_active or staff.disabled_at is not None:
        return None
    if staff.session_version != record.session_version_at_login:
        return None  # a role/disable change invalidated every prior session (fixes the documented staleness gap)
    return record


def load_current_staff() -> StaffUser | None:
    if "staff_user" in g:
        return g.staff_user
    raw_token = request.cookies.get(COOKIE_NAME)
    record = _get_valid_session(raw_token) if raw_token else None
    if record is None:
        g.staff_user = None
        g.staff_session = None
        return None
    record.last_seen_at = utcnow()
    db_session.commit()
    g.staff_session = record
    g.staff_user = db_session.get(StaffUser, record.staff_user_id)
    return g.staff_user


def current_session() -> StaffSession | None:
    load_current_staff()
    return g.get("staff_session")


def mark_mfa_verified() -> None:
    record = current_session()
    if record is not None:
        record.mfa_verified_at = utcnow()
        db_session.commit()


def has_recent_auth() -> bool:
    record = current_session()
    if record is None or record.mfa_verified_at is None:
        return False
    window = current_app.config["RECENT_AUTH_WINDOW_SECONDS"]
    return (utcnow() - record.mfa_verified_at).total_seconds() <= window


def revoke_session(raw_token: str, reason: str = "logout") -> None:
    token_hash = hash_token(raw_token)
    stmt = select(StaffSession).where(StaffSession.token_hash == token_hash)
    record = db_session.execute(stmt).scalars().first()
    if record is not None and record.revoked_at is None:
        record.revoked_at = utcnow()
        record.revoked_reason = reason
        db_session.commit()


def revoke_all_sessions_for_staff(staff_user_id: uuid.UUID, reason: str = "admin_revoked") -> None:
    stmt = select(StaffSession).where(StaffSession.staff_user_id == staff_user_id).where(
        StaffSession.revoked_at.is_(None)
    )
    for record in db_session.execute(stmt).scalars().all():
        record.revoked_at = utcnow()
        record.revoked_reason = reason
    db_session.commit()
