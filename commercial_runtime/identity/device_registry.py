"""
Aura FullSuits -- per-device login / "one Admin Device" registry.

Phase 1 (this week): schema + read-only-safe CRUD only. Nothing in this
module is wired into any login path yet -- `mt_auth.py`, `auth_routes.py`,
`onboarding_routes.py`, and both products' `config.py` are explicitly
untouched this week, and enforcement stays default-OFF. See
docs/superpowers/specs/2026-08-10-per-device-login.md for the full Phase 0
decision record (requirement disambiguation, company_id authority decision,
and the per-registry.db limitation this table's invariant is scoped to).

Owns two tables:
  - `devices` -- one row per physical device known to this install's
    `registry.db`, keyed by a local UUID stable for the life of the
    install. `is_admin_device` is enforced to have at most one holder per
    `company_id` by a partial unique index (`idx_devices_one_admin`) -- the
    database enforces the invariant, not application code.
  - `user_devices` -- an explicit grant list: an account existing does not
    by itself authorize login from every device, a (user_id, device_id)
    pair must be explicitly granted.

Every function below takes an explicit `conn` (sqlite3.Connection,
`row_factory=sqlite3.Row` expected, matching registry_db.get_conn()) as its
first parameter. Unlike registry_db.py, this module has NO module-level
DB_PATH constant and does no import-time filesystem access -- callers own
connection lifecycle (typically via `registry_db.get_conn()`), this module
only ever operates on the connection it's handed.
"""
import sqlite3
from datetime import datetime, timezone


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def apply_identity_device_schema(conn: sqlite3.Connection) -> None:
    """Additive-only schema for per-device login. Every statement is
    CREATE TABLE/INDEX IF NOT EXISTS -- no ALTER, no DROP, no data rewrite --
    so calling this twice (or a hundred times) against the same connection
    is always a clean no-op. Called from registry_db.init_registry_db() via
    commercial_runtime.security.migration_safety.ensure_schema_version(),
    which takes a live backup and runs PRAGMA integrity_check before and
    after, only advancing PRAGMA user_version on full success -- same
    paranoid pattern already used for retail.db/clinic.db, now applied to
    registry.db for the first time (REGISTRY_SCHEMA_VERSION in
    registry_db.py).
    """
    conn.executescript('''
        CREATE TABLE IF NOT EXISTS devices (
            id                    TEXT PRIMARY KEY,       -- local UUID, stable for the life of the install
            company_id            TEXT NOT NULL,
            device_fingerprint    TEXT,                   -- licensing pubkey fingerprint, NULL until activated
            owner_installation_id TEXT,
            device_label          TEXT,
            platform              TEXT,                   -- 'WINDOWS' | 'ANDROID'
            is_admin_device       INTEGER NOT NULL DEFAULT 0,
            status                TEXT NOT NULL DEFAULT 'active',   -- 'active' | 'revoked'
            first_seen_at         TEXT NOT NULL,
            last_seen_at          TEXT
        );
        CREATE UNIQUE INDEX IF NOT EXISTS idx_devices_fingerprint
            ON devices(device_fingerprint) WHERE device_fingerprint IS NOT NULL;
        -- THE "one Admin Device" invariant, enforced by the database, not application code:
        CREATE UNIQUE INDEX IF NOT EXISTS idx_devices_one_admin
            ON devices(company_id) WHERE is_admin_device = 1;

        -- Deliberately NO foreign keys here (to devices.id / users.id), for
        -- the same reason registry_db.py's own `user_permissions` table
        -- (also a "grant" table referencing users.id) has none either:
        -- get_conn() runs with PRAGMA foreign_keys=ON, and create_admin's
        -- `DELETE FROM users WHERE role='admin'` (onboarding_routes.py)
        -- replaces the admin row outright when re-onboarding. An FK on
        -- user_id here (default SQLite action, no ON DELETE CASCADE) would
        -- make that DELETE fail outright the moment a grant row existed for
        -- that admin; CASCADE would silently destroy the audit trail this
        -- table exists to preserve (see revoke_device below). Omitting the
        -- FK, same as user_permissions, keeps that DELETE working exactly
        -- as it does today and keeps grant rows as an intentional,
        -- app-managed audit trail rather than a DB-enforced relationship.
        CREATE TABLE IF NOT EXISTS user_devices (
            user_id    TEXT NOT NULL,
            device_id  TEXT NOT NULL,
            granted_at TEXT NOT NULL,
            granted_by TEXT,
            PRIMARY KEY (user_id, device_id)
        );
        CREATE INDEX IF NOT EXISTS idx_user_devices_device ON user_devices(device_id);
    ''')


# ── Device CRUD ──────────────────────────────────────────────────────────────

def upsert_local_device(conn: sqlite3.Connection, device_id: str, company_id: str,
                         device_label: str = None, platform: str = None,
                         device_fingerprint: str = None,
                         owner_installation_id: str = None) -> dict:
    """Insert a new device row, or refresh the mutable fields of an existing
    one identified by `device_id` (the local, install-stable UUID -- NOT the
    autoincrement-style id pattern used elsewhere in this suite).
    `first_seen_at` is stamped once, on first insert, and never overwritten;
    `last_seen_at` is refreshed on every call -- this is the "device checked
    in" write. A field left as None on an update leaves the existing stored
    value untouched (COALESCE), so a bare heartbeat call doesn't need to
    resend every field.
    """
    now = _now_iso()
    existing = conn.execute("SELECT id FROM devices WHERE id=?", (device_id,)).fetchone()
    if existing:
        conn.execute('''
            UPDATE devices
            SET company_id=?,
                device_fingerprint=COALESCE(?, device_fingerprint),
                owner_installation_id=COALESCE(?, owner_installation_id),
                device_label=COALESCE(?, device_label),
                platform=COALESCE(?, platform),
                last_seen_at=?
            WHERE id=?
        ''', (company_id, device_fingerprint, owner_installation_id,
              device_label, platform, now, device_id))
    else:
        conn.execute('''
            INSERT INTO devices
              (id, company_id, device_fingerprint, owner_installation_id,
               device_label, platform, is_admin_device, status,
               first_seen_at, last_seen_at)
            VALUES (?, ?, ?, ?, ?, ?, 0, 'active', ?, ?)
        ''', (device_id, company_id, device_fingerprint, owner_installation_id,
              device_label, platform, now, now))
    conn.commit()
    return get_device(conn, device_id)


def get_device(conn: sqlite3.Connection, device_id: str) -> dict:
    row = conn.execute("SELECT * FROM devices WHERE id=?", (device_id,)).fetchone()
    return dict(row) if row else None


def list_devices(conn: sqlite3.Connection, company_id: str) -> list:
    rows = conn.execute(
        "SELECT * FROM devices WHERE company_id=? ORDER BY first_seen_at",
        (company_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def set_admin_device(conn: sqlite3.Connection, company_id: str, device_id: str) -> dict:
    """Atomically move the `is_admin_device` flag to `device_id`, clearing
    any previous holder for `company_id` first -- both statements run
    inside the SAME transaction, before a single commit.

    This ordering is not cosmetic: `idx_devices_one_admin` is a *partial*
    unique index (`WHERE is_admin_device = 1`). If the new admin row were
    set to 1 before the old one was cleared to 0, two rows would
    transiently satisfy the index's predicate at once and the second UPDATE
    would fail the unique constraint. Clearing every row for this
    `company_id` first, unconditionally, makes the ordering safe regardless
    of how many rows currently hold the flag (should only ever be 0 or 1).
    """
    conn.execute(
        "UPDATE devices SET is_admin_device=0 WHERE company_id=?",
        (company_id,),
    )
    cur = conn.execute(
        "UPDATE devices SET is_admin_device=1 WHERE id=? AND company_id=?",
        (device_id, company_id),
    )
    if cur.rowcount == 0:
        conn.rollback()
        raise ValueError(f"device {device_id!r} not found for company {company_id!r}")
    conn.commit()
    return get_device(conn, device_id)


def has_admin_device(conn: sqlite3.Connection, company_id: str) -> bool:
    """True if some device already holds `is_admin_device` for this company.
    Used by device_context.resolve_local_device() to auto-bootstrap the
    first-ever device as the admin device -- see that call site's own
    comment for why nothing else in this codebase ever did that."""
    row = conn.execute(
        "SELECT 1 FROM devices WHERE company_id=? AND is_admin_device=1 LIMIT 1",
        (company_id,),
    ).fetchone()
    return row is not None


def revoke_device(conn: sqlite3.Connection, device_id: str) -> dict:
    """Mark a device 'revoked' and clear its admin flag if it held one.

    Deliberately does NOT delete the device row, and does NOT touch
    `user_devices` -- any grants naming this device are left exactly as
    they were, an intentional audit trail of who had access to what, even
    though the device itself is no longer active. `allowed_on_device` is
    grant-existence-only (see below) and does not consult device status --
    combining "device revoked" with "login allowed" is a login-enforcement
    decision, out of scope this week (device_context.py, Tuesday).
    """
    conn.execute(
        "UPDATE devices SET status='revoked', is_admin_device=0 WHERE id=?",
        (device_id,),
    )
    conn.commit()
    return get_device(conn, device_id)


# ── User <-> device grants ───────────────────────────────────────────────────

def grant_user_device(conn: sqlite3.Connection, user_id: str, device_id: str,
                       granted_by: str = None) -> None:
    """Grant `user_id` authorization to log in from `device_id`. Re-granting
    an existing pair refreshes `granted_at`/`granted_by` rather than
    erroring (INSERT OR REPLACE on the (user_id, device_id) primary key)."""
    conn.execute('''
        INSERT OR REPLACE INTO user_devices (user_id, device_id, granted_at, granted_by)
        VALUES (?, ?, ?, ?)
    ''', (user_id, device_id, _now_iso(), granted_by))
    conn.commit()


def revoke_user_device(conn: sqlite3.Connection, user_id: str, device_id: str) -> None:
    """Remove a specific (user_id, device_id) grant. Unlike `revoke_device`,
    this genuinely deletes the row -- it's the grant itself being revoked,
    not the device being retired, so there is no separate audit-trail row
    to preserve here."""
    conn.execute(
        "DELETE FROM user_devices WHERE user_id=? AND device_id=?",
        (user_id, device_id),
    )
    conn.commit()


def allowed_on_device(conn: sqlite3.Connection, user_id: str, device_id: str) -> bool:
    """True iff `user_id` currently has an active grant for `device_id`.
    Grant-existence only -- does not consult `devices.status`. Combining
    this with device-status/company checks at the point an actual login
    decision is made is device_context.py's job (Tuesday, out of scope
    today)."""
    row = conn.execute(
        "SELECT 1 FROM user_devices WHERE user_id=? AND device_id=?",
        (user_id, device_id),
    ).fetchone()
    return row is not None
