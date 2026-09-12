"""LicenseStateRepository (Part J) -- persists ONLY the safe local licensing
state fields Part J allowlists. Plain sqlite3, matching the exact connection
pattern already used by products/*/backend/database/schema.py (WAL mode,
busy_timeout, foreign_keys=ON) so this fits the established product
convention rather than introducing a second persistence style.

Deliberately does NOT store: full license key, license HMAC, Owner pepper,
Owner private keys, the device private key (that lives in DPAPI/Keystore
storage, never here), or any patient/sales/inventory/customer data. See
docs/licensing/phase7/product-to-owner-data-boundary.md.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Optional

LICENSING_SCHEMA_VERSION = 2  # bumped: launch-readiness (2026-09-03) added
# sync_relay_base_url (see LicenseStateRecord + _MIGRATIONS below). This
# constant is stored PER-ROW as data (licensing_schema_version column), not
# read anywhere to gate behavior -- the real migration mechanism is
# _migrate_add_missing_columns() below, which is additive-only and driven
# by actually inspecting the live table shape (PRAGMA table_info), the same
# "trust live shape, not a version number" principle
# commercial_runtime/security/migration_safety.py's docstring argues for at
# the product-schema level. Bumping this is just keeping the constant
# honest for any NEW row saved from here on.

_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS licensing_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),  -- single-row table: one installation per app-data dir
    licensing_schema_version INTEGER NOT NULL,
    product_code TEXT NOT NULL,
    platform TEXT NOT NULL,
    owner_environment_id TEXT,
    owner_installation_id TEXT,
    device_public_key_fingerprint TEXT,
    current_state TEXT NOT NULL,
    assertion_envelope_json TEXT,
    assertion_id TEXT,
    assertion_issued_at TEXT,
    assertion_not_before TEXT,
    assertion_expires_at TEXT,
    trusted_time_anchor_server_time TEXT,
    last_successful_checkin_at TEXT,
    last_sync_result TEXT,
    last_public_reason_code TEXT,
    offline_policy_json TEXT,
    entitlements_json TEXT,
    license_status TEXT,
    installation_status TEXT,
    subscription_status TEXT,
    trusted_signing_key_ids_json TEXT,
    restriction_state_metadata_json TEXT,
    sync_relay_base_url TEXT,
    updated_at TEXT NOT NULL
)
"""

# Columns added AFTER the original CREATE TABLE shipped. CREATE TABLE IF NOT
# EXISTS above is a no-op once the table already exists, so an existing
# licensing.db from before this column existed would otherwise never get
# it -- save()'s INSERT (which lists every dataclass field by name) would
# then fail with "no such column" the first time a build that knows about
# sync_relay_base_url tries to write to a database created by one that
# doesn't. Each entry is (column_name, column_ddl_type); ADD COLUMN is
# always nullable here (no server-side default), matching every field on
# LicenseStateRecord being Optional.
_ADDED_COLUMNS = (
    ("sync_relay_base_url", "TEXT"),
)


class LicenseStateRepositoryError(Exception):
    pass


@dataclass
class LicenseStateRecord:
    licensing_schema_version: int
    product_code: str
    platform: str
    current_state: str
    owner_environment_id: Optional[str] = None
    owner_installation_id: Optional[str] = None
    device_public_key_fingerprint: Optional[str] = None
    assertion_envelope_json: Optional[str] = None
    assertion_id: Optional[str] = None
    assertion_issued_at: Optional[str] = None
    assertion_not_before: Optional[str] = None
    assertion_expires_at: Optional[str] = None
    trusted_time_anchor_server_time: Optional[str] = None
    last_successful_checkin_at: Optional[str] = None
    last_sync_result: Optional[str] = None
    last_public_reason_code: Optional[str] = None
    offline_policy_json: Optional[str] = None
    entitlements_json: Optional[str] = None
    license_status: Optional[str] = None
    installation_status: Optional[str] = None
    subscription_status: Optional[str] = None
    trusted_signing_key_ids_json: Optional[str] = None
    restriction_state_metadata_json: Optional[str] = None
    # Launch-readiness (2026-09-03): the relay base URL Owner told this
    # device at activation (activation.py's ingest_activation_response()
    # stores it the same way owner_installation_id already is), used by
    # products/*/backend/config.py as a fallback when no operator has
    # explicitly set AURA_SYNC_RELAY_URL. None on any record saved before
    # this field existed, and on any activation response that never
    # included the key (an Owner deploy that never configured
    # OWNER_SYNC_RELAY_PUBLIC_URL) -- both are legitimate "not discovered"
    # states, not errors.
    sync_relay_base_url: Optional[str] = None


class LicenseStateRepository:
    def __init__(self, db_path: Path):
        self._db_path = db_path
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db_path), timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=30000")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _ensure_schema(self) -> None:
        with self._conn() as conn:
            conn.execute(_CREATE_TABLE_SQL)
            self._migrate_add_missing_columns(conn)

    def _migrate_add_missing_columns(self, conn: sqlite3.Connection) -> None:
        """Additive-only migration for a licensing.db that predates one of
        _ADDED_COLUMNS -- must never raise on a brand-new database (the
        columns are already present from _CREATE_TABLE_SQL, so this is a
        no-op) and must never raise on an old one either (ALTER TABLE ADD
        COLUMN is safe on SQLite regardless of how many rows already
        exist; every added column is nullable, so existing rows simply
        read back as NULL / None)."""
        existing = {row["name"] for row in conn.execute("PRAGMA table_info(licensing_state)")}
        for column_name, column_ddl_type in _ADDED_COLUMNS:
            if column_name not in existing:
                conn.execute(f"ALTER TABLE licensing_state ADD COLUMN {column_name} {column_ddl_type}")

    def load(self) -> Optional[LicenseStateRecord]:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM licensing_state WHERE id = 1").fetchone()
        if row is None:
            return None
        data = dict(row)
        data.pop("id", None)
        data.pop("updated_at", None)
        return LicenseStateRecord(**data)

    def save(self, record: LicenseStateRecord) -> None:
        data = asdict(record)
        columns = list(data.keys())
        placeholders = ", ".join(f":{c}" for c in columns)
        assignments = ", ".join(f"{c}=excluded.{c}" for c in columns)
        sql = f"""
            INSERT INTO licensing_state (id, {", ".join(columns)}, updated_at)
            VALUES (1, {placeholders}, :updated_at)
            ON CONFLICT(id) DO UPDATE SET {assignments}, updated_at=excluded.updated_at
        """
        params = {**data, "updated_at": datetime.utcnow().isoformat()}
        with self._conn() as conn:
            conn.execute(sql, params)

    def reset(self) -> None:
        """Used only by the controlled LOCAL_STATE_CORRUPT reset flow (Part
        X/E) -- never called as a silent recovery path. Does not touch any
        other table in the product's database (this repository owns exactly
        one table)."""
        with self._conn() as conn:
            conn.execute("DELETE FROM licensing_state")

    def update_state(self, new_state: str) -> None:
        record = self.load()
        if record is None:
            raise LicenseStateRepositoryError("Cannot update_state before an initial record exists.")
        record.current_state = new_state
        self.save(record)
