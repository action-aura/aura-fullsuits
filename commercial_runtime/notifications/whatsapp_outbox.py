"""Outbound WhatsApp -- the outbox: state machine + repository over
whatsapp_outbox.

Near-literal structural copy of commercial_runtime/notifications/outbox.py's
EmailOutboxRepository -- same state machine, same reasoning for why it is
deliberately simpler than einvoicing's outbox (see that module's own
docstring; the same trade-off applies here: a duplicate WhatsApp send on a
crash-during-commit is an acceptable outcome for an alert/report, not a
compliance problem the way a duplicate tax submission would be).

Differences from EmailOutboxRepository, driven entirely by whatsapp_outbox's
own column shape (schema.py): enqueue() takes template_name/language_code/
component_params_json instead of subject/body_text/body_html (WhatsApp sends
a pre-approved template, never free text -- see whatsapp_client.py's
docstring), and mark_sent() additionally records the returned wamid.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Optional

STATES = frozenset({'QUEUED', 'SENDING', 'SENT', 'FAILED_PERMANENT', 'CANCELLED'})
TERMINAL_STATES = frozenset({'SENT', 'FAILED_PERMANENT', 'CANCELLED'})


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class WhatsAppOutboxRepository:
    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn

    def enqueue(
        self, *, company_id, message_type: str, recipient_phone_e164: str,
        template_name: str, language_code: str = 'en_US',
        component_params_json: Optional[str] = None,
    ) -> int:
        """Always inserts -- same reasoning as EmailOutboxRepository.enqueue:
        there is no natural single idempotency key for an arbitrary queued
        report the way a UBL submission ref is for einvoicing. Callers that
        need "at most one open alert" get that property from whatever
        upstream table gates the trigger (e.g. reorder_requests' own
        open-request uniqueness for the low-stock path), never from this
        method."""
        now = _now()
        cur = self._conn.execute(
            "INSERT INTO whatsapp_outbox "
            "(company_id, message_type, recipient_phone_e164, template_name, language_code, "
            " component_params_json, status, attempt_count, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?, 'QUEUED', 0, ?, ?)",
            (company_id, message_type, recipient_phone_e164, template_name, language_code,
             component_params_json, now, now),
        )
        return cur.lastrowid

    def get(self, row_id: int) -> Optional[sqlite3.Row]:
        self._conn.row_factory = sqlite3.Row
        return self._conn.execute("SELECT * FROM whatsapp_outbox WHERE id=?", (row_id,)).fetchone()

    def claim_due(self, *, company_id, batch_size: int, lease_seconds: int) -> list:
        """Same conditional-UPDATE-with-rowcount-check technique as
        EmailOutboxRepository.claim_due -- see that method's docstring.

        AUDIT-fix: scoped by company_id -- each WhatsAppOutboxWorker is
        one-per-company (see whatsapp_worker.py), but this query used to
        have no company_id filter at all, so any company's worker could
        claim (and send, with that company's own max_attempts) another
        company's queued rows, including rows for a company that has since
        disabled WhatsApp. Every other table in this codebase is
        company_id-scoped for exactly this reason."""
        self._conn.row_factory = sqlite3.Row
        now = _now()
        candidates = self._conn.execute(
            "SELECT id FROM whatsapp_outbox WHERE company_id=? AND status='QUEUED' "
            "AND (next_attempt_at IS NULL OR next_attempt_at <= ?) "
            "ORDER BY id LIMIT ?",
            (company_id, now, batch_size),
        ).fetchall()

        claimed = []
        for row in candidates:
            row_id = row['id']
            lease_expires = self._lease_expiry(lease_seconds)
            cur = self._conn.execute(
                "UPDATE whatsapp_outbox SET status='SENDING', lease_expires_at=?, updated_at=? "
                "WHERE id=? AND company_id=? AND status='QUEUED'",
                (lease_expires, now, row_id, company_id),
            )
            if cur.rowcount == 1:
                claimed.append(self._conn.execute(
                    "SELECT * FROM whatsapp_outbox WHERE id=?", (row_id,)
                ).fetchone())
        return claimed

    @staticmethod
    def _lease_expiry(lease_seconds: int) -> str:
        return (datetime.now(timezone.utc) + timedelta(seconds=lease_seconds)).isoformat()

    def _transition(self, row_id: int, from_states: tuple, to_state: str,
                     extra_sql: str = '', extra_params: tuple = ()) -> bool:
        current = self._conn.execute("SELECT status FROM whatsapp_outbox WHERE id=?", (row_id,)).fetchone()
        if current is None:
            return False
        current_status = current[0]
        if current_status in TERMINAL_STATES:
            return False
        if current_status not in from_states:
            raise ValueError(f"{current_status} -> {to_state} is not a legal whatsapp outbox transition.")

        sql = f"UPDATE whatsapp_outbox SET status=?, updated_at=?{extra_sql} WHERE id=? AND status=?"
        params = (to_state, _now()) + extra_params + (row_id, current_status)
        cur = self._conn.execute(sql, params)
        return cur.rowcount == 1

    def mark_sent(self, row_id: int, *, wamid: str) -> bool:
        return self._transition(
            row_id, ('SENDING',), 'SENT',
            extra_sql=", sent_at=?, wamid=?", extra_params=(_now(), wamid),
        )

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
        current = self._conn.execute("SELECT status FROM whatsapp_outbox WHERE id=?", (row_id,)).fetchone()
        if current is None:
            return False
        current_status = current[0]
        if current_status in TERMINAL_STATES:
            return False
        return self._transition(row_id, (current_status,), 'CANCELLED')

    def reclaim_expired_leases(self, *, batch_size: int = 100) -> int:
        """SENDING rows whose lease has expired go straight back to QUEUED
        -- see this module's own docstring for why that is an acceptable,
        deliberately simple choice here. Returns the count reclaimed."""
        now = _now()
        candidates = self._conn.execute(
            "SELECT id FROM whatsapp_outbox WHERE status='SENDING' AND lease_expires_at IS NOT NULL "
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
            "SELECT status, COUNT(*) FROM whatsapp_outbox WHERE company_id=? GROUP BY status", (company_id,)
        ).fetchall()
        result = {state: 0 for state in STATES}
        for status, count in rows:
            result[status] = count
        return result
