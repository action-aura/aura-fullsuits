"""Email verification + forgot-password: token lifecycle and email content.

Reuses the existing `secure_links` table (registry.db) rather than inventing
a parallel token store -- see verification_schema.py's docstring for why the
table needed a `purpose` column first. Every function here takes an explicit
`conn` (matching device_registry.py's convention) so routes control the
transaction boundary.

Email delivery is synchronous (`commercial_runtime.notifications.smtp_client
.send_email` called directly), not routed through the async email_outbox
worker: outbox+poll-interval is the right shape for bulk/report email where
nobody is watching a screen for it; a verification/reset link is the
opposite -- low-volume, and the user is actively waiting. A failed or
unconfigured send never raises past this module's own functions -- callers
get a bool, not an exception, because "SMTP isn't configured on this
install" must never surface as a 500 (same invisible-until-configured
contract as every other optional channel in this codebase) and a
password-reset caller specifically must never let delivery success/failure
leak whether the email address exists (see request_password_reset).
"""
from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from commercial_runtime.notifications.smtp_client import send_email, SmtpNotConfiguredError

EMAIL_VERIFICATION_TTL_HOURS = 24
PASSWORD_RESET_TTL_HOURS = 1
# A fresh password-reset link is withheld while an unused one for the same
# email is still within this window -- cheap inbox-spam guard using the
# existing table, no new rate-limit infrastructure.
PASSWORD_RESET_RESEND_COOLDOWN_MINUTES = 5


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _new_raw_token() -> str:
    return uuid.uuid4().hex


def _hash_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode()).hexdigest()


def _create_link(conn, *, company_id: str, email: str, purpose: str, ttl_hours: float) -> str:
    """Writes created_at explicitly rather than relying on secure_links'
    column default (SQLite CURRENT_TIMESTAMP, which is space-separated with
    no timezone suffix) -- request_password_reset's cooldown check compares
    created_at against a Python isoformat() cutoff ('T' separator,
    '+00:00' suffix); mixing the two formats makes the string comparison
    silently never match (confirmed: the cooldown never fired until this
    was fixed), same class of bug as the Owner API timestamp fix earlier
    tonight."""
    raw_token = _new_raw_token()
    now = _now().isoformat()
    conn.execute(
        "INSERT INTO secure_links (id, company_id, token_hash, email_target, expires_at, purpose, created_at) "
        "VALUES (?,?,?,?,?,?,?)",
        (
            str(uuid.uuid4()), company_id, _hash_token(raw_token), email,
            (_now() + timedelta(hours=ttl_hours)).isoformat(), purpose, now,
        ),
    )
    return raw_token


def consume_link(conn, raw_token: str, *, purpose: str) -> Optional[dict]:
    """Validates a token for the given purpose (unused, unexpired) and marks
    it used, atomically with the caller's own action -- call this only once
    the caller is about to commit the actual effect (verifying the email /
    setting the new password), not as a standalone pre-check, so a crash
    between "marked used" and "effect applied" can never happen. Returns the
    link row as a dict on success, None on any failure (expired, wrong
    purpose, already used, or simply not found) -- deliberately one
    undifferentiated failure mode, so a caller can't be used to distinguish
    "token doesn't exist" from "token belongs to a different purpose"."""
    row = conn.execute(
        "SELECT id, company_id, email_target, expires_at, is_used, purpose FROM secure_links WHERE token_hash=?",
        (_hash_token(raw_token),),
    ).fetchone()
    if row is None or row["purpose"] != purpose or row["is_used"]:
        return None
    if _now().isoformat() > row["expires_at"]:
        return None
    conn.execute("UPDATE secure_links SET is_used=1 WHERE id=?", (row["id"],))
    return dict(row)


def _send(*, recipient: str, subject: str, body_text: str, body_html: str) -> bool:
    try:
        send_email(recipient=recipient, subject=subject, body_text=body_text, body_html=body_html)
        return True
    except SmtpNotConfiguredError:
        return False
    except Exception:
        # Never let a transient SMTP failure (relay timeout, auth error, DNS)
        # turn into a 500 on a signup/reset request -- the link itself was
        # already created and remains valid; the user's real recourse (ask
        # for a new one, or an admin resends) exists regardless of whether
        # this one delivery attempt succeeded.
        return False


def send_verification_email(conn, *, company_id: str, user_email: str, base_url: str) -> bool:
    """Creates a fresh email_verification link and attempts to send it.
    Returns whether the send actually succeeded (SMTP configured and no
    transport error) -- callers should treat False as "queued for the user
    to request again," never as a request-level failure."""
    raw_token = _create_link(
        conn, company_id=company_id, email=user_email,
        purpose="email_verification", ttl_hours=EMAIL_VERIFICATION_TTL_HOURS,
    )
    link = f"{base_url.rstrip('/')}/#verify-email/{raw_token}"
    return _send(
        recipient=user_email,
        subject="Verify your Aura Retail email address",
        body_text=(
            f"Confirm this is your email address to finish setting up Aura Retail:\n\n{link}\n\n"
            f"This link expires in {EMAIL_VERIFICATION_TTL_HOURS} hours. "
            "If you didn't create this account, you can ignore this email."
        ),
        body_html=(
            f'<p>Confirm this is your email address to finish setting up Aura Retail:</p>'
            f'<p><a href="{link}">{link}</a></p>'
            f'<p>This link expires in {EMAIL_VERIFICATION_TTL_HOURS} hours. '
            "If you didn't create this account, you can ignore this email.</p>"
        ),
    )


def verify_email(conn, raw_token: str) -> Optional[str]:
    """Returns the verified user's id on success, None on any failure.
    Matches the email on the link against a real, still-existing user before
    writing anything -- a user deleted after the link was issued must not
    resurrect a row."""
    link = consume_link(conn, raw_token, purpose="email_verification")
    if link is None:
        return None
    user = conn.execute("SELECT id FROM users WHERE email=?", (link["email_target"],)).fetchone()
    if user is None:
        return None
    conn.execute(
        "UPDATE users SET email_verified_at=? WHERE id=?", (_now().isoformat(), user["id"])
    )
    return user["id"]


def request_password_reset(conn, *, email: str, base_url: str) -> None:
    """Always returns None regardless of whether `email` belongs to a real
    account -- the caller's route must return an identical response either
    way. This is the actual enumeration guard; it lives here, not just in
    the route, so no future caller can accidentally skip it."""
    email = (email or "").strip().lower()
    if not email:
        return
    user = conn.execute(
        "SELECT id, company_id, status FROM users WHERE email=?", (email,)
    ).fetchone()
    if user is None or user["status"] != "active":
        return

    cooldown_cutoff = (_now() - timedelta(minutes=PASSWORD_RESET_RESEND_COOLDOWN_MINUTES)).isoformat()
    recent = conn.execute(
        "SELECT id FROM secure_links WHERE email_target=? AND purpose='password_reset' "
        "AND is_used=0 AND created_at >= ? LIMIT 1",
        (email, cooldown_cutoff),
    ).fetchone()
    if recent is not None:
        return

    raw_token = _create_link(
        conn, company_id=user["company_id"], email=email,
        purpose="password_reset", ttl_hours=PASSWORD_RESET_TTL_HOURS,
    )
    link = f"{base_url.rstrip('/')}/#reset-password/{raw_token}"
    _send(
        recipient=email,
        subject="Reset your Aura Retail password",
        body_text=(
            f"Reset your Aura Retail password:\n\n{link}\n\n"
            f"This link expires in {PASSWORD_RESET_TTL_HOURS} hour and can only be used once. "
            "If you didn't request this, you can ignore this email -- your password will not change."
        ),
        body_html=(
            f'<p>Reset your Aura Retail password:</p>'
            f'<p><a href="{link}">{link}</a></p>'
            f'<p>This link expires in {PASSWORD_RESET_TTL_HOURS} hour and can only be used once. '
            "If you didn't request this, you can ignore this email -- your password will not change.</p>"
        ),
    )
