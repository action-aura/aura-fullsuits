"""Outbound email -- verification token generation (foundation only).

What this file builds: a cryptographically random token, a hashed form of
it safe to persist (mirrors how licensing_contracts never stores a
plaintext secret it can avoid storing), an expiry helper, and
`queue_verification_email()` -- a convenience function that generates a
token, records its hash in email_verification_tokens, and queues the actual
email (raw token embedded in the body, via the normal EmailOutboxRepository
path) in one call.

What this file deliberately does NOT build (see the task this module was
written for: "you don't need to wire a full verification UI flow if that's
not obviously scoped yet"):

  * No route that calls queue_verification_email() -- no product yet has a
    concrete "verify this email address" user flow (signup confirmation?
    a staff invite? a customer-facing portal that doesn't exist yet?), and
    guessing that shape now would mean building a table/route contract a
    real future flow would likely have to reshape anyway.
  * No `consume_token()` / verify-and-mark-consumed function -- same
    reason: what happens on successful verification (activate a user row?
    unlock a feature? just log an event?) is entirely determined by the
    flow that doesn't exist yet.

A future caller wiring a real flow needs exactly two more things this file
does not provide: a route that calls queue_verification_email() at the
right trigger point, and a small consume function that looks up
email_verification_tokens by (company_id, recipient, purpose,
consumed_at IS NULL), compares hash_token(supplied_token) against
token_hash, checks expires_at, and stamps consumed_at. Both are
straightforward once the calling flow's own shape is decided.
"""
from __future__ import annotations

import hashlib
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Optional

from .outbox import EmailOutboxRepository

TOKEN_TTL_MINUTES = 30
_TOKEN_BYTES = 32  # secrets.token_urlsafe(32) -> 43 url-safe chars, ~256 bits of entropy


def generate_token() -> str:
    return secrets.token_urlsafe(_TOKEN_BYTES)


def hash_token(token: str) -> str:
    """SHA-256 is sufficient here (not a password hash / no brute-force
    surface to worry about the way a login password has): the token itself
    already carries ~256 bits of entropy from secrets.token_urlsafe, so this
    hash exists only so a stolen database dump doesn't hand out live,
    usable tokens -- not to slow down an attacker guessing a low-entropy
    secret. Matches the threat model, not password-hashing best practice
    (which would be the wrong tool applied to the wrong problem here)."""
    return hashlib.sha256(token.encode('utf-8')).hexdigest()


def _expiry_timestamp(minutes: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(minutes=minutes)).isoformat()


def queue_verification_email(
    conn: sqlite3.Connection,
    *,
    company_id,
    recipient: str,
    purpose: str = 'verify_email',
    subject: Optional[str] = None,
    body_template: Optional[str] = None,
    ttl_minutes: int = TOKEN_TTL_MINUTES,
) -> dict:
    """Generates a token, records its hash, and queues the email carrying
    the RAW token (never the hash) via the normal outbox path -- so sending
    still goes through EmailOutboxWorker like every other email type, never
    inline/synchronous, matching this whole module's "always via the queue"
    contract.

    `body_template` receives the raw token via `{token}` substitution (a
    plain str.format() call, not a templating engine -- consistent with
    this module's "no templating engine dependency" scope). Returns a dict
    with `token` (raw -- give this to nobody but the recipient's own email;
    callers must not log it) and `email_outbox_id` for traceability.
    """
    token = generate_token()
    token_hash = hash_token(token)
    now = _now()
    expires_at = _expiry_timestamp(ttl_minutes)

    subject = subject or 'Your Aura verification code'
    body_template = body_template or (
        "Your verification code is: {token}\n\n"
        f"This code expires in {ttl_minutes} minutes. If you did not request this, you can ignore this email."
    )
    body_text = body_template.format(token=token)

    row_id = EmailOutboxRepository(conn).enqueue(
        company_id=company_id, email_type='verification', recipient=recipient,
        subject=subject, body_text=body_text,
    )

    conn.execute(
        "INSERT INTO email_verification_tokens "
        "(company_id, recipient, purpose, token_hash, email_outbox_id, expires_at, created_at) "
        "VALUES (?,?,?,?,?,?,?)",
        (company_id, recipient, purpose, token_hash, row_id, expires_at, now),
    )

    return {'token': token, 'email_outbox_id': row_id, 'expires_at': expires_at}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
