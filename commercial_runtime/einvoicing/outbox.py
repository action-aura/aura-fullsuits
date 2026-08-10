"""JoFotara e-invoicing -- the outbox: state machine + repository over
einvoice_outbox.

State machine
=============

    QUEUED --claim--> SUBMITTING --CLEARED----> CLEARED           (terminal)
       ^                   |
       |                   +--REJECTED----> FAILED_PERMANENT      (terminal)
       |                   +--RETRY-------> QUEUED   (backoff, attempt_count++)
       |                   +--PENDING-----> AWAITING_CLEARANCE
       |                   +--UNKNOWN-----> SUBMITTING_UNKNOWN
       |                                         |
       |                                         +--check_status: cleared --> CLEARED
       |                                         +--check_status: not received --> QUEUED
       +-------------------------------------------------+
    AWAITING_CLEARANCE --poll: cleared--> CLEARED
    AWAITING_CLEARANCE --poll: rejected--> FAILED_PERMANENT
    any non-terminal --kill switch / operator--> CANCELLED         (terminal,
                                                   sequence number retained)

Four independent layers make double-submission structurally impossible, not
just unlikely:

  1. UNIQUE index on invoice_ref -- enqueue() is INSERT OR IGNORE; a
     duplicate enqueue is a silent no-op.
  2. claim_due() is a conditional UPDATE ... WHERE status='QUEUED' -- only
     proceeds when exactly one row was actually updated (rowcount check).
     Two workers racing the same row: exactly one wins.
  3. submit_started_at is written and committed BEFORE the provider call
     happens (by the caller, worker.py) -- a crash mid-flight leaves an
     unambiguous marker of "a submission attempt was in progress here".
  4. An expired SUBMITTING lease is reclaimed to SUBMITTING_UNKNOWN, never
     straight back to QUEUED -- the worker must call
     provider.check_status() first and get an explicit "not received"
     answer before this row is ever eligible to be claimed again. A
     provider answer of UNKNOWN holds the row exactly where it is.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Optional

STATES = frozenset({
    'QUEUED', 'SUBMITTING', 'CLEARED', 'FAILED_PERMANENT',
    'AWAITING_CLEARANCE', 'SUBMITTING_UNKNOWN', 'CANCELLED',
})
TERMINAL_STATES = frozenset({'CLEARED', 'FAILED_PERMANENT', 'CANCELLED'})

_ALLOWED_TRANSITIONS = frozenset({
    ('QUEUED', 'SUBMITTING'),
    ('SUBMITTING', 'CLEARED'),
    ('SUBMITTING', 'FAILED_PERMANENT'),
    ('SUBMITTING', 'QUEUED'),
    ('SUBMITTING', 'AWAITING_CLEARANCE'),
    ('SUBMITTING', 'SUBMITTING_UNKNOWN'),
    ('AWAITING_CLEARANCE', 'CLEARED'),
    ('AWAITING_CLEARANCE', 'FAILED_PERMANENT'),
    ('SUBMITTING_UNKNOWN', 'CLEARED'),
    ('SUBMITTING_UNKNOWN', 'QUEUED'),
} | {(state, 'CANCELLED') for state in STATES - TERMINAL_STATES})


class IllegalTransitionError(ValueError):
    pass


class StaleTransitionError(ValueError):
    """Raised when a caller attempts a transition against a row that has
    already moved to a different state than the caller expected -- e.g. two
    workers both believing they hold a SUBMITTING row. Distinct from
    IllegalTransitionError (a transition that could never be legal, period)
    so callers can tell "the state machine forbids this" apart from "someone
    else already acted on this row"."""


def is_legal_transition(from_state: str, to_state: str) -> bool:
    return (from_state, to_state) in _ALLOWED_TRANSITIONS


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class OutboxRepository:
    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn

    def enqueue(
        self, *, company_id: int, invoice_ref: str, source_type: str, source_id: int,
        local_document_no: Optional[str], einvoice_no: str, invoice_family: str,
        payment_type: str, currency: str, provider: str,
    ) -> Optional[int]:
        """Returns the new row's id, or None if invoice_ref already existed
        (a silent, safe no-op -- layer 1 of the idempotency guarantee)."""
        now = _now()
        cur = self._conn.execute(
            "INSERT OR IGNORE INTO einvoice_outbox "
            "(company_id, invoice_ref, source_type, source_id, local_document_no, einvoice_no, "
            " invoice_family, payment_type, currency, status, attempt_count, provider, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?, 'QUEUED', 0, ?, ?, ?)",
            (company_id, invoice_ref, source_type, source_id, local_document_no, einvoice_no,
             invoice_family, payment_type, currency, provider, now, now),
        )
        if cur.rowcount == 0:
            return None
        return cur.lastrowid

    def get_by_ref(self, invoice_ref: str) -> Optional[sqlite3.Row]:
        self._conn.row_factory = sqlite3.Row
        return self._conn.execute(
            "SELECT * FROM einvoice_outbox WHERE invoice_ref=?", (invoice_ref,)
        ).fetchone()

    def claim_due(self, *, batch_size: int, lease_seconds: int) -> list:
        """Layer 2 of the idempotency guarantee: each row's UPDATE only
        succeeds (rowcount==1) if it is still QUEUED at the moment this
        specific claim runs -- two concurrent callers can never both claim
        the same row."""
        self._conn.row_factory = sqlite3.Row
        now = _now()
        candidates = self._conn.execute(
            "SELECT id FROM einvoice_outbox WHERE status='QUEUED' "
            "AND (next_attempt_at IS NULL OR next_attempt_at <= ?) "
            "ORDER BY id LIMIT ?",
            (now, batch_size),
        ).fetchall()

        claimed = []
        for row in candidates:
            row_id = row['id']
            lease_expires = self._lease_expiry(lease_seconds)
            cur = self._conn.execute(
                "UPDATE einvoice_outbox SET status='SUBMITTING', lease_expires_at=?, "
                "submit_started_at=?, updated_at=? WHERE id=? AND status='QUEUED'",
                (lease_expires, now, now, row_id),
            )
            if cur.rowcount == 1:
                claimed.append(self._conn.execute(
                    "SELECT * FROM einvoice_outbox WHERE id=?", (row_id,)
                ).fetchone())
        return claimed

    @staticmethod
    def _lease_expiry(lease_seconds: int) -> str:
        from datetime import timedelta
        return (datetime.now(timezone.utc) + timedelta(seconds=lease_seconds)).isoformat()

    def _transition(self, row_id: int, from_states: tuple, to_state: str, extra_sql: str = '', extra_params: tuple = ()) -> bool:
        """Returns True if the transition actually happened.

        Returns False (does NOT raise) when the row has already settled
        into a terminal state (CLEARED/FAILED_PERMANENT/CANCELLED) -- that
        is always a legitimate race (someone else, or an earlier call,
        already resolved this row), never a caller bug.

        Raises IllegalTransitionError when the row is in a normal, active
        (non-terminal) state that this method was never meant to fire from
        -- e.g. calling mark_cleared() on a row that was never claimed
        (still QUEUED). That is a genuine caller error, not a race.
        """
        current = self._conn.execute("SELECT status FROM einvoice_outbox WHERE id=?", (row_id,)).fetchone()
        if current is None:
            return False
        current_status = current[0]
        if current_status in TERMINAL_STATES:
            return False
        if current_status not in from_states:
            raise IllegalTransitionError(f"{current_status} -> {to_state} is not a legal e-invoice outbox transition.")
        assert is_legal_transition(current_status, to_state), (
            f"from_states for this method disagree with the state machine: "
            f"{current_status} -> {to_state} is not in _ALLOWED_TRANSITIONS"
        )

        sql = f"UPDATE einvoice_outbox SET status=?, updated_at=?{extra_sql} WHERE id=? AND status=?"
        params = (to_state, _now()) + extra_params + (row_id, current_status)
        cur = self._conn.execute(sql, params)
        return cur.rowcount == 1

    def mark_cleared(self, row_id: int, *, provider_uuid: Optional[str], qr_payload: Optional[str],
                      qr_image_base64: Optional[str]) -> bool:
        return self._transition(
            row_id, ('SUBMITTING', 'AWAITING_CLEARANCE', 'SUBMITTING_UNKNOWN'), 'CLEARED',
            extra_sql=", provider_uuid=?, qr_payload=?, qr_image_base64=?, cleared_at=?, last_reason_code='CLEARED'",
            extra_params=(provider_uuid, qr_payload, qr_image_base64, _now()),
        )

    def mark_rejected(self, row_id: int, *, reason_code: str, detail: Optional[str]) -> bool:
        return self._transition(
            row_id, ('SUBMITTING', 'AWAITING_CLEARANCE'), 'FAILED_PERMANENT',
            extra_sql=", last_reason_code=?, last_detail=?",
            extra_params=(reason_code, detail),
        )

    def mark_failed_permanent_after_max_attempts(self, row_id: int, *, reason_code: str, detail: Optional[str]) -> bool:
        return self.mark_rejected(row_id, reason_code=reason_code, detail=detail)

    def mark_retry(self, row_id: int, *, reason_code: str, detail: Optional[str], next_attempt_at: str) -> bool:
        return self._transition(
            row_id, ('SUBMITTING',), 'QUEUED',
            extra_sql=", attempt_count=attempt_count+1, next_attempt_at=?, last_reason_code=?, last_detail=?, lease_expires_at=NULL",
            extra_params=(next_attempt_at, reason_code, detail),
        )

    def mark_pending(self, row_id: int) -> bool:
        return self._transition(row_id, ('SUBMITTING',), 'AWAITING_CLEARANCE',
                                 extra_sql=", last_reason_code='PENDING'")

    def mark_unknown(self, row_id: int, *, reason_code: str, detail: Optional[str]) -> bool:
        return self._transition(
            row_id, ('SUBMITTING',), 'SUBMITTING_UNKNOWN',
            extra_sql=", last_reason_code=?, last_detail=?",
            extra_params=(reason_code, detail),
        )

    def resolve_unknown_cleared(self, row_id: int, *, provider_uuid: Optional[str], qr_payload: Optional[str],
                                 qr_image_base64: Optional[str]) -> bool:
        return self.mark_cleared(row_id, provider_uuid=provider_uuid, qr_payload=qr_payload, qr_image_base64=qr_image_base64)

    def resolve_unknown_not_received(self, row_id: int, *, next_attempt_at: str) -> bool:
        return self._transition(
            row_id, ('SUBMITTING_UNKNOWN',), 'QUEUED',
            extra_sql=", attempt_count=attempt_count+1, next_attempt_at=?, last_reason_code='UNKNOWN_RESOLVED_NOT_RECEIVED', lease_expires_at=NULL",
            extra_params=(next_attempt_at,),
        )

    def cancel(self, row_id: int) -> bool:
        current = self._conn.execute("SELECT status FROM einvoice_outbox WHERE id=?", (row_id,)).fetchone()
        if current is None:
            return False
        current_status = current[0]
        if current_status in TERMINAL_STATES:
            return False
        return self._transition(row_id, (current_status,), 'CANCELLED')

    def reclaim_expired_leases(self, *, batch_size: int = 100) -> list:
        """SUBMITTING rows whose lease has expired move to
        SUBMITTING_UNKNOWN -- NEVER straight back to QUEUED. The caller
        (worker.py) must call provider.check_status() on each returned row
        and only then decide CLEARED vs QUEUED. Returns the reclaimed rows."""
        self._conn.row_factory = sqlite3.Row
        now = _now()
        candidates = self._conn.execute(
            "SELECT id FROM einvoice_outbox WHERE status='SUBMITTING' AND lease_expires_at IS NOT NULL "
            "AND lease_expires_at <= ? ORDER BY id LIMIT ?",
            (now, batch_size),
        ).fetchall()
        reclaimed = []
        for row in candidates:
            if self._transition(row['id'], ('SUBMITTING',), 'SUBMITTING_UNKNOWN',
                                 extra_sql=", last_reason_code='LEASE_EXPIRED'"):
                reclaimed.append(self._conn.execute(
                    "SELECT * FROM einvoice_outbox WHERE id=?", (row['id'],)
                ).fetchone())
        return reclaimed

    def counts_by_state(self, company_id: int) -> dict:
        rows = self._conn.execute(
            "SELECT status, COUNT(*) FROM einvoice_outbox WHERE company_id=? GROUP BY status", (company_id,)
        ).fetchall()
        result = {state: 0 for state in STATES}
        for status, count in rows:
            result[status] = count
        return result
