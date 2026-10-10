"""owner_email_outbox -- the queue: state machine + repository.

    QUEUED --claim--> SENDING --sent----> SENT              (terminal)
       ^                  |
       |                  +--failure, attempts remain--> QUEUED (backoff, attempt_count++)
       |                  +--failure, attempts exhausted--> FAILED_PERMANENT (terminal)
       +------------------------------------------------------+
    any non-terminal --operator cancel--> CANCELLED           (terminal)

Same semantics as commercial_runtime/notifications/outbox.py (the product-side
queue this one is modelled on), including its deliberate simplification: an
expired SENDING lease goes straight back to QUEUED, so a crash exactly between
the SMTP server accepting a message and this process committing SENT sends one
duplicate email. Acceptable for an invitation or an alert; SMTP gives no
delivery receipt to build anything stricter on.

claim_due() is a conditional UPDATE ... WHERE status='QUEUED' with a rowcount
check, so two racing workers (two gunicorn processes both running a worker
thread) can never both claim -- and so both send -- the same row.

Callers commit; nothing here does, except claim_due and reclaim_expired_leases
(a claim must be durable before the slow SMTP call starts).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import select, update

from app.extensions import db_session
from app.models.email_outbox import EmailOutbox

TERMINAL_STATES = frozenset({"SENT", "FAILED_PERMANENT", "CANCELLED"})
# error text is stored as a class name only; this caps even that.
MAX_ERROR_CHARS = 120


def _now() -> datetime:
    return datetime.now(timezone.utc)


def enqueue(*, kind: str, to_address: str, subject: str, body_text: str, body_html: Optional[str] = None,
            source: str = "owner", installation_id: Optional[uuid.UUID] = None) -> EmailOutbox:
    """Header-injection guard lives HERE, not only in the relay: whatever
    produced the recipient or the subject, a CR/LF in either would let the
    sender add headers (Bcc:) to the message."""
    for label, value in (("to_address", to_address), ("subject", subject)):
        if not value or any(ch in value for ch in "\r\n\x00"):
            raise ValueError(f"{label} is empty or contains a line break")
    row = EmailOutbox(
        kind=kind, to_address=to_address, subject=subject, body_text=body_text, body_html=body_html,
        status="QUEUED", attempt_count=0, source=source, installation_id=installation_id, created_at=_now(),
    )
    db_session.add(row)
    db_session.flush()
    return row


def claim_due(*, batch_size: int, lease_seconds: int) -> list:
    now = _now()
    candidates = db_session.execute(
        select(EmailOutbox.id)
        .where(EmailOutbox.status == "QUEUED", (EmailOutbox.next_attempt_at.is_(None)) | (EmailOutbox.next_attempt_at <= now))
        .order_by(EmailOutbox.created_at, EmailOutbox.id)
        .limit(batch_size)
    ).scalars().all()
    claimed = []
    for row_id in candidates:
        result = db_session.execute(
            update(EmailOutbox)
            .where(EmailOutbox.id == row_id, EmailOutbox.status == "QUEUED")
            .values(status="SENDING", lease_expires_at=now + timedelta(seconds=lease_seconds))
        )
        if result.rowcount == 1:
            claimed.append(row_id)
    db_session.commit()
    return claimed


def _transition(row_id, from_state: str, **values) -> bool:
    result = db_session.execute(
        update(EmailOutbox).where(EmailOutbox.id == row_id, EmailOutbox.status == from_state).values(**values)
    )
    return result.rowcount == 1


def mark_sent(row_id) -> bool:
    return _transition(row_id, "SENDING", status="SENT", sent_at=_now(), lease_expires_at=None)


def mark_retry(row_id, *, error: str, next_attempt_at: datetime) -> bool:
    row = db_session.get(EmailOutbox, row_id)
    attempts = (row.attempt_count if row is not None else 0) + 1
    return _transition(row_id, "SENDING", status="QUEUED", attempt_count=attempts,
                       next_attempt_at=next_attempt_at, last_error=error[:MAX_ERROR_CHARS], lease_expires_at=None)


def mark_failed_permanent(row_id, *, error: str) -> bool:
    return _transition(row_id, "SENDING", status="FAILED_PERMANENT", last_error=error[:MAX_ERROR_CHARS], lease_expires_at=None)


def cancel(row_id) -> bool:
    row = db_session.get(EmailOutbox, row_id)
    if row is None or row.status in TERMINAL_STATES:
        return False
    return _transition(row_id, row.status, status="CANCELLED", lease_expires_at=None)


def reclaim_expired_leases(*, batch_size: int = 100) -> int:
    now = _now()
    ids = db_session.execute(
        select(EmailOutbox.id)
        .where(EmailOutbox.status == "SENDING", EmailOutbox.lease_expires_at.is_not(None), EmailOutbox.lease_expires_at <= now)
        .limit(batch_size)
    ).scalars().all()
    reclaimed = 0
    for row_id in ids:
        if _transition(row_id, "SENDING", status="QUEUED", lease_expires_at=None, last_error="LEASE_EXPIRED"):
            reclaimed += 1
    db_session.commit()
    return reclaimed
