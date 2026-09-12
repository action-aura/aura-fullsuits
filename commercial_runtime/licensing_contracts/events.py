"""LicensingEventRecorder (Part W) -- privacy-safe local licensing event
log. Local only: never transmitted to Owner by any code in this package
(Part W is explicit about this). Shares the licensing.db file used by
LicenseStateRepository (one licensing schema, per Part J's spirit) but owns
its own table.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Mapping, Optional

EVENT_TYPES = frozenset(
    {
        "ACTIVATION_STARTED",
        "ACTIVATION_SUCCEEDED",
        "ACTIVATION_FAILED",
        "ACTIVATION_PENDING",
        "ASSERTION_ACCEPTED",
        "ASSERTION_REJECTED",
        "ASSERTION_STALE_REJECTED",
        "CHECK_IN_SUCCEEDED",
        "CHECK_IN_FAILED",
        "OFFLINE_MODE_ENTERED",
        "WARNING_ENTERED",
        "GRACE_ENTERED",
        "RESTRICTED_MODE_ENTERED",
        "LICENSE_SUSPENDED",
        "LICENSE_REVOKED",
        "LICENSE_EXPIRED",
        "CLOCK_REVIEW_REQUIRED",
        "DEVICE_DEACTIVATED",
        "LOCAL_STATE_CORRUPT",
        "CAPABILITY_DENIED",
        # Guarded re-anchor (2026-09-04): the bundled anchor introduced a
        # signing key this store had never seen, because Owner's key rotated
        # with no continuity bridge. Rare and security-relevant by nature, so
        # it is recorded rather than done silently -- an unexplained one is
        # worth investigating.
        "TRUST_ANCHOR_READMITTED",
    }
)

# Same defense-in-depth allowlist pattern used by Owner's api/serializers.py
# and this package's own assertion_verifier.py -- a caller passing
# business/medical/secret data into an event's details dict is rejected
# outright rather than silently persisted to a local log file.
FORBIDDEN_DETAIL_MARKERS = frozenset(
    {
        "license_key",
        "key_secret",
        "pepper",
        "password",
        "private_key",
        "card_number",
        "bank_account",
        "patient",
        "medical_note",
        "clinical_note",
        "diagnosis",
        "prescription",
        "invoice_total",
        "sale_total",
        "stock_quantity",
    }
)

DEFAULT_RETENTION_DAYS = 180  # bounded, documented -- not unbounded growth

_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS licensing_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    details_json TEXT
)
"""


class LicensingEventError(ValueError):
    pass


@dataclass(frozen=True)
class LicensingEvent:
    event_type: str
    occurred_at: datetime
    details: Mapping[str, object]


def _guard_details(details: Mapping[str, object], trusted_keys: frozenset = frozenset()) -> None:
    """Scans every value NOT in trusted_keys for a forbidden marker.

    trusted_keys exists for values like a capability_code
    ("clinic.patient.create", "clinic.prescription.create", ...) -- a
    hardcoded source-code literal from our own fixed, documented vocabulary
    (see clinic-restriction-capability-matrix.md / retail-restriction-
    capability-matrix.md), never derived from a request body, a database
    row, or any other user/patient-controlled input. Without this
    exemption, CAPABILITY_DENIED could never be recorded for most real
    capability codes -- Part Q/R's own suggested names ("...patient...",
    "...prescription...") legitimately contain the same substrings this
    guard exists to keep OUT of an event when they appear in actual data.
    The exemption is per-key and explicit at each call site, not a way to
    quietly widen what's allowed everywhere.
    """
    checked = {k: v for k, v in details.items() if k not in trusted_keys}
    flat = json.dumps(checked, default=str).lower()
    for marker in FORBIDDEN_DETAIL_MARKERS:
        if marker in flat:
            raise LicensingEventError(f"Event details contain a forbidden marker: {marker!r}.")


class LicensingEventRecorder:
    def __init__(self, db_path: Path):
        self._db_path = db_path
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as conn:
            conn.execute(_CREATE_TABLE_SQL)

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db_path), timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=30000")
        return conn

    def record(
        self,
        event_type: str,
        details: Optional[Mapping[str, object]] = None,
        *,
        trusted_keys: frozenset = frozenset(),
    ) -> None:
        if event_type not in EVENT_TYPES:
            raise LicensingEventError(f"Unknown licensing event type: {event_type!r}.")
        details = details or {}
        _guard_details(details, trusted_keys)
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO licensing_events (event_type, occurred_at, details_json) VALUES (?, ?, ?)",
                (event_type, datetime.now(timezone.utc).isoformat(), json.dumps(details)),
            )

    def recent(self, limit: int = 100) -> list[LicensingEvent]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT event_type, occurred_at, details_json FROM licensing_events ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [
            LicensingEvent(
                event_type=row["event_type"],
                occurred_at=datetime.fromisoformat(row["occurred_at"]),
                details=json.loads(row["details_json"] or "{}"),
            )
            for row in rows
        ]

    def prune_older_than(self, days: int = DEFAULT_RETENTION_DAYS) -> int:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        with self._conn() as conn:
            cursor = conn.execute("DELETE FROM licensing_events WHERE occurred_at < ?", (cutoff,))
            return cursor.rowcount
