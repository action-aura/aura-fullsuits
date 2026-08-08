"""JoFotara e-invoicing -- submission audit trail.

Mirrors commercial_runtime/licensing_contracts/events.py's design: an
allowlisted event vocabulary, a FORBIDDEN_DETAIL_MARKERS guard that makes it
structurally impossible to persist a secret or business-sensitive value into
this log (raises rather than silently redacting -- a caller must fix the
call site, not rely on this guard to clean up after it), and a
JSONL-mirrored form on disk for operator/support visibility, matching
scripts/sync/lib/Audit.ps1's Write-SyncLog shape.

Two sinks, written together by record():

  1. einvoice_audit table (lives in the product's own DB -- see schema.py --
     so it survives backup/restore, unlike a separate log-only file).
  2. <app_data_dir>/einvoicing/logs/audit.jsonl -- one compact JSON object
     per line, for a human/support engineer to tail without a DB client.

Monetary amounts and buyer PII never belong here (same rule events.py
enforces for licensing) -- the submitted document itself, with its
document_sha256, already lives in einvoice_outbox as the compliance
evidence; duplicating business data into a log is not required to prove
what was submitted.
"""
from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Mapping, Optional

EVENT_TYPES = frozenset({
    'FEATURE_ENABLED', 'FEATURE_DISABLED', 'KILLSWITCH_SET', 'KILLSWITCH_CLEARED',
    'CREDENTIALS_STORED', 'CREDENTIALS_WIPED', 'CREDENTIALS_UNREADABLE',
    'INVOICE_ENQUEUED', 'SEQUENCE_ALLOCATED', 'SUBMIT_STARTED', 'SUBMIT_CLEARED',
    'SUBMIT_REJECTED', 'SUBMIT_PENDING', 'SUBMIT_RETRY_SCHEDULED', 'SUBMIT_UNKNOWN',
    'LEASE_RECLAIMED', 'STATUS_POLLED', 'MANUAL_RETRY', 'ENTRY_CANCELLED',
    'RECONCILIATION_ENQUEUED', 'PROVIDER_ERROR',
})

# Same defense-in-depth pattern as licensing_contracts/events.py's
# FORBIDDEN_DETAIL_MARKERS, extended with the credential/secret-shaped
# fields specific to this feature.
FORBIDDEN_DETAIL_MARKERS = frozenset({
    'license_key', 'key_secret', 'pepper', 'password', 'private_key',
    'card_number', 'bank_account', 'patient', 'medical_note', 'clinical_note',
    'diagnosis', 'prescription', 'invoice_total', 'sale_total', 'stock_quantity',
    'client_id', 'client_secret', 'access_token', 'refresh_token', 'bearer',
    'authorization', 'tin', 'national_id',
})

DEFAULT_RETENTION_DAYS = 400  # one tax year + margin


class EInvoiceAuditError(ValueError):
    pass


def _guard_details(details: Mapping[str, object]) -> None:
    flat = json.dumps(details, default=str).lower()
    for marker in FORBIDDEN_DETAIL_MARKERS:
        if marker in flat:
            raise EInvoiceAuditError(f"Audit details contain a forbidden marker: {marker!r}.")


def _logs_dir(app_data_dir: str) -> str:
    d = os.path.join(app_data_dir, 'einvoicing', 'logs')
    os.makedirs(d, exist_ok=True)
    return d


def record(
    conn: sqlite3.Connection,
    app_data_dir: str,
    *,
    company_id: int,
    event: str,
    invoice_ref: Optional[str] = None,
    attempt_no: Optional[int] = None,
    outcome: Optional[str] = None,
    reason_code: Optional[str] = None,
    provider: Optional[str] = None,
    http_status: Optional[int] = None,
    duration_ms: Optional[int] = None,
    details: Optional[Mapping[str, object]] = None,
) -> None:
    if event not in EVENT_TYPES:
        raise EInvoiceAuditError(f"Unknown e-invoicing audit event: {event!r}.")
    details = details or {}
    _guard_details(details)

    occurred_at = datetime.now(timezone.utc).isoformat()
    details_json = json.dumps(details)

    conn.execute(
        "INSERT INTO einvoice_audit "
        "(company_id, invoice_ref, attempt_no, event, outcome, reason_code, provider, "
        " http_status, duration_ms, occurred_at, details_json) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (company_id, invoice_ref, attempt_no, event, outcome, reason_code, provider,
         http_status, duration_ms, occurred_at, details_json),
    )

    line = json.dumps({
        'timestamp': occurred_at,
        'level': 'ERROR' if event in ('PROVIDER_ERROR', 'SUBMIT_REJECTED', 'CREDENTIALS_UNREADABLE') else 'INFO',
        'event': event,
        'company_id': company_id,
        'invoice_ref': invoice_ref,
        'outcome': outcome,
        'reason_code': reason_code,
        'provider': provider,
        'data': details,
    })
    log_path = os.path.join(_logs_dir(app_data_dir), 'audit.jsonl')
    with open(log_path, 'a', encoding='utf-8') as f:
        f.write(line + '\n')


def recent(conn: sqlite3.Connection, company_id: int, *, invoice_ref: Optional[str] = None, limit: int = 100) -> list:
    conn.row_factory = sqlite3.Row
    if invoice_ref:
        rows = conn.execute(
            "SELECT * FROM einvoice_audit WHERE company_id=? AND invoice_ref=? ORDER BY id DESC LIMIT ?",
            (company_id, invoice_ref, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM einvoice_audit WHERE company_id=? ORDER BY id DESC LIMIT ?",
            (company_id, limit),
        ).fetchall()
    return rows


def prune_older_than(conn: sqlite3.Connection, days: int = DEFAULT_RETENTION_DAYS) -> int:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    cur = conn.execute("DELETE FROM einvoice_audit WHERE occurred_at < ?", (cutoff,))
    return cur.rowcount
