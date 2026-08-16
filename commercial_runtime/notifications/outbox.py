"""Outbound email -- the outbox: state machine + repository over
email_outbox.

State machine
=============

    QUEUED --claim--> SENDING --sent----> SENT              (terminal)
       ^                  |
       |                  +--failure, attempts remain--> QUEUED (backoff, attempt_count++)
       |                  +--failure, attempts exhausted--> FAILED_PERMANENT (terminal)
       +------------------------------------------------------+
    any non-terminal --operator cancel--> CANCELLED           (terminal)

Deliberately simpler than commercial_runtime/einvoicing/outbox.py's state
machine -- and that gap is a considered choice, not an oversight, worth
spelling out since this file's shape is otherwise a near-literal copy of
that one:

  * einvoicing needs PENDING/AWAITING_CLEARANCE and UNKNOWN/
    SUBMITTING_UNKNOWN states because a tax authority can legitimately
    receive a submission and take time to clear it, and a double
    submission is a compliance problem this codebase treats as
    unacceptable -- see that file's own docstring's "four independent
    layers" writeup. SMTP has no equivalent "accepted but not yet cleared"
    state: `client.sendmail()` either succeeds (the relay accepted it) or
    raises, synchronously, within one call.
  * Layer 4 of einvoicing's idempotency guarantee (an expired SUBMITTING
    lease reclaims to SUBMITTING_UNKNOWN, never straight back to QUEUED,
    specifically to avoid ever double-submitting to ISTD) is INTENTIONALLY
    NOT mirrored here -- reclaim_expired_leases() below reclaims a crashed
    SENDING row straight back to QUEUED. Worst case on a real crash exactly
    between smtplib returning success and this process committing SENT is
    one duplicate email to one recipient; that is an acceptable trade-off
    for a low-stock alert or an on-demand report, and building the
    equivalent of provider.check_status() would mean inventing a
    delivery-receipt protocol SMTP does not give us for free. This
    trade-off is exactly why the task this module was built for explicitly
    said "don't chase... keep it as simple as the einvoicing pattern it's
    modeled on" rather than "copy it exactly".

Layers 1-2 of einvoicing's guarantee DO still apply here and matter just as
much: claim_due()'s conditional UPDATE ... WHERE status='QUEUED' with a
rowcount check is what makes two racing worker ticks (or, in principle, two
company workers on a shared table) unable to both claim the same row.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Optional

STATES = frozenset({'QUEUED', 'SENDING', 'SENT', 'FAILED_PERMANENT', 'CANCELLED'})
TERMINAL_STATES = frozenset({'SENT', 'FAILED_PERMANENT', 'CANCELLED'})


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class EmailOutboxRepository:
    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn

    def enqueue(
        self, *, company_id, email_type: str, recipient: str, subject: str,
        body_text: str, body_html: Optional[str] = None, template_id: Optional[str] = None,
    ) -> int:
        """Unlike einvoicing's enqueue() (INSERT OR IGNORE against a UNIQUE
        invoice_ref, so a duplicate enqueue is a silent no-op), this always
        inserts -- there is no natural single idempotency key for an
        arbitrary email the way `AURA_RETAIL:sale:<id>` is for one specific
        sale's invoice. Callers that need "at most one open alert per
        product" (the low-stock trigger) get that property for free from
        reorder_requests.idx_reorder_requests_open -- they only call
        enqueue() when a NEW reorder_requests row was just created, so this
        method never needs its own duplicate-suppression logic."""
        now = _now()
        cur = self._conn.execute(
            "INSERT INTO email_outbox "
            "(company_id, email_type, recipient, subject, body_text, body_html, template_id, "
            " status, attempt_count, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?, 'QUEUED', 0, ?, ?)",
            (company_id, email_type, recipient, subject, body_text, body_html, template_id, now, now),
        )
        return cur.lastrowid

    def get(self, row_id: int) -> Optional[sqlite3.Row]:
        self._conn.row_factory = sqlite3.Row
        return self._conn.execute("SELECT * FROM email_outbox WHERE id=?", (row_id,)).fetchone()

    def claim_due(self, *, company_id, batch_size: int, lease_seconds: int) -> list:
        """Same conditional-UPDATE-with-rowcount-check technique as
        einvoicing/outbox.py::claim_due -- see that method's docstring.

        AUDIT-fix: scoped by company_id -- each EmailOutboxWorker is
        one-per-company (see worker.py), but this query used to have no
        company_id filter at all, so any company's worker could claim (and
        send, with that company's own max_attempts) another company's
        queued rows. Same bug, same fix, as whatsapp_outbox.py's claim_due()
        -- this file is the one it was structurally copied from."""
        self._conn.row_factory = sqlite3.Row
        now = _now()
        candidates = self._conn.execute(
            "SELECT id FROM email_outbox WHERE company_id=? AND status='QUEUED' "
            "AND (next_attempt_at IS NULL OR next_attempt_at <= ?) "
            "ORDER BY id LIMIT ?",
            (company_id, now, batch_size),
        ).fetchall()

        claimed = []
        for row in candidates:
            row_id = row['id']
            lease_expires = self._lease_expiry(lease_seconds)
            cur = self._conn.execute(
                "UPDATE email_outbox SET status='SENDING', lease_expires_at=?, updated_at=? "
                "WHERE id=? AND company_id=? AND status='QUEUED'",
                (lease_expires, now, row_id, company_id),
            )
            if cur.rowcount == 1:
                claimed.append(self._conn.execute(
                    "SELECT * FROM email_outbox WHERE id=?", (row_id,)
                ).fetchone())
        return claimed

    @staticmethod
    def _lease_expiry(lease_seconds: int) -> str:
        return (datetime.now(timezone.utc) + timedelta(seconds=lease_seconds)).isoformat()

    def _transition(self, row_id: int, from_states: tuple, to_state: str,
                     extra_sql: str = '', extra_params: tuple = ()) -> bool:
        current = self._conn.execute("SELECT status FROM email_outbox WHERE id=?", (row_id,)).fetchone()
        if current is None:
            return False
        current_status = current[0]
        if current_status in TERMINAL_STATES:
            return False
        if current_status not in from_states:
            raise ValueError(f"{current_status} -> {to_state} is not a legal email outbox transition.")

        sql = f"UPDATE email_outbox SET status=?, updated_at=?{extra_sql} WHERE id=? AND status=?"
        params = (to_state, _now()) + extra_params + (row_id, current_status)
        cur = self._conn.execute(sql, params)
        return cur.rowcount == 1

    def mark_sent(self, row_id: int) -> bool:
        return self._transition(row_id, ('SENDING',), 'SENT', extra_sql=", sent_at=?", extra_params=(_now(),))

    def mark_retry(self, row_id: int, *, error: str, next_attempt_at: str) -> bool:
        return self._transition(
            row_id, ('SENDING',), 'QUEUED',
            extra_sql=", attempt_count=attempt_count+1, next_attempt_at=?, last_error=?, lease_expires_at=NULL",
            extra_params=(next_attempt_at, error),
        )

    def mark_failed_permanent(self, row_id: int, *, error: str) -> bool:
        return self._transition(
            row_id, ('SENDING',), 'FAILED_PERMANENT',
            extra_sql=", last_error=?",
            extra_params=(error,),
        )

    def cancel(self, row_id: int) -> bool:
        current = self._conn.execute("SELECT status FROM email_outbox WHERE id=?", (row_id,)).fetchone()
        if current is None:
            return False
        current_status = current[0]
        if current_status in TERMINAL_STATES:
            return False
        return self._transition(row_id, (current_status,), 'CANCELLED')

    def reclaim_expired_leases(self, *, batch_size: int = 100) -> int:
        """SENDING rows whose lease has expired go straight back to QUEUED
        -- see this module's own docstring for why that is a deliberately
        simpler (and lower-stakes) choice than einvoicing's
        SUBMITTING_UNKNOWN detour. Returns the count reclaimed."""
        now = _now()
        candidates = self._conn.execute(
            "SELECT id FROM email_outbox WHERE status='SENDING' AND lease_expires_at IS NOT NULL "
            "AND lease_expires_at <= ? ORDER BY id LIMIT ?",
            (now, batch_size),
        ).fetchall()
        reclaimed = 0
        for row in candidates:
            if self._transition(row[0], ('SENDING',), 'QUEUED',
                                 extra_sql=", lease_expires_at=NULL, last_error='LEASE_EXPIRED'"):
                reclaimed += 1
        return reclaimed

    def counts_by_state(self, company_id) -> dict:
        rows = self._conn.execute(
            "SELECT status, COUNT(*) FROM email_outbox WHERE company_id=? GROUP BY status", (company_id,)
        ).fetchall()
        result = {state: 0 for state in STATES}
        for status, count in rows:
            result[status] = count
        return result
