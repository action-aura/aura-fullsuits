"""
Aura FullSuits -- multi-tenant registry database (trimmed).

Owns the tables that back tenant identity, licensing, and audit for every
product in this suite: `users` (multi-tenant accounts), `company_modules`
(per-tenant product/module licensing), `user_permissions` (per-user subsystem
access), `audit_logs` (append-oriented security/audit trail), `secure_links`
(one-time employee-invite tokens), `company_settings` (locale/business
fields written by onboarding).

This is a deliberately trimmed extraction of Action Aura Enterprise's
database/registry_db.py (which owns ~20 tables for the full platform --
EIP module catalog, document flow, numbering sequences, custom fields, etc.
that Retail/Clinic never touch). Only the tables actually read/written by
commercial_runtime.identity.{mt_auth,auth_routes} and
commercial_runtime.security.audit are kept here. See
docs/migration/dependency-map.md §1 and docs/migration/clinic-source-inventory.md.

`secure_links` and `company_settings` were added in Phase 3 (Clinic) --
additive only, does not change any existing table used by Retail. Retail's
116-test suite was rerun after this change and passes unmodified (see
docs/migration/retail-extraction-report.md).
"""
import os
import sqlite3
import json
import uuid

_app_data = os.environ.get('AURA_APP_DATA') or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_db_dir = os.path.join(_app_data, 'database')
DB_PATH = os.path.join(_db_dir, 'registry.db')

# v1: devices + user_devices (per-device login / Admin Device flag). See
# docs/superpowers/specs/2026-08-10-per-device-login.md. registry.db had no
# PRAGMA user_version at all before this -- every existing on-disk
# registry.db implicitly reads version 0, so this migration is the first to
# run against it via commercial_runtime.security.migration_safety.
# v2: email verification + password reset (users.email_verified_at,
# secure_links.purpose) -- see verification_schema.py.
# v3: multi-device account model (users.uid/pin_hash/row_version/
# updated_at_utc/deleted_at_utc, widened role domain, seeded capability
# rows) -- see account_schema.py and
# docs/launch-readiness/multi-device-design.md §6.
REGISTRY_SCHEMA_VERSION = 3


def _migrate_registry_schema(conn):
    """The single `migrate_fn` handed to `ensure_schema_version` -- runs
    every migration this file owns, in version order, on any database
    behind REGISTRY_SCHEMA_VERSION, same "run every step, each
    independently idempotent" shape as products/retail/backend/database/
    schema.py::_migrate_retail_schema. A v0 (pre-user_version) database
    upgrading straight to v3 runs all three steps in one pass.

    New steps are appended LAST and never reordered: a database that is
    already at v2 still runs v1 and v2 on its way to v3 (there is one
    version gate for the whole function, not one per step), so each step has
    to be a no-op against a database that already has its changes -- which
    is exactly what each of them is."""
    from commercial_runtime.identity.device_registry import apply_identity_device_schema
    from commercial_runtime.identity.verification_schema import apply_email_verification_schema
    from commercial_runtime.identity.account_schema import apply_account_schema
    apply_identity_device_schema(conn)
    apply_email_verification_schema(conn)
    apply_account_schema(conn)


def get_conn():
    os.makedirs(_db_dir, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_registry_db():
    conn = get_conn()
    c = conn.cursor()

    c.execute('''
        CREATE TABLE IF NOT EXISTS company_modules (
            id          TEXT PRIMARY KEY,
            company_id  TEXT NOT NULL,
            module_code TEXT NOT NULL,
            status      TEXT DEFAULT 'enabled',
            enabled_at  TEXT,
            disabled_at TEXT,
            settings_json TEXT DEFAULT '{}',
            module_name TEXT,
            enabled     INTEGER DEFAULT 1,
            UNIQUE(company_id, module_code)
        )
    ''')

    c.execute('''
        CREATE TABLE IF NOT EXISTS audit_logs (
            id              TEXT PRIMARY KEY,
            company_id      TEXT NOT NULL,
            user_id         TEXT,
            module_code     TEXT,
            action          TEXT NOT NULL,
            entity_type     TEXT NOT NULL,
            entity_id       TEXT NOT NULL,
            old_value_json  TEXT,
            new_value_json  TEXT,
            ip_address      TEXT,
            created_at      TEXT DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    c.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id              TEXT PRIMARY KEY,
            company_id      TEXT NOT NULL,
            employee_id     TEXT NOT NULL,
            email           TEXT UNIQUE NOT NULL,
            password_hash   TEXT NOT NULL,
            role            TEXT DEFAULT 'employee',
            status          TEXT DEFAULT 'active',
            require_password_change INTEGER DEFAULT 1,
            session_version INTEGER DEFAULT 1,
            language        TEXT DEFAULT 'en',
            clinic_role     TEXT DEFAULT '',
            failed_login_count INTEGER DEFAULT 0,
            locked_until    TEXT,
            created_at      TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(company_id, employee_id)
        )
    ''')

    c.execute('''
        CREATE TABLE IF NOT EXISTS user_permissions (
            id              TEXT PRIMARY KEY,
            user_id         TEXT NOT NULL,
            subsystem       TEXT NOT NULL,
            access_level    TEXT DEFAULT 'none',
            UNIQUE(user_id, subsystem)
        )
    ''')

    c.execute('''
        CREATE TABLE IF NOT EXISTS secure_links (
            id              TEXT PRIMARY KEY,
            company_id      TEXT NOT NULL,
            token_hash      TEXT NOT NULL,
            email_target    TEXT NOT NULL,
            expires_at      TEXT NOT NULL,
            is_used         INTEGER DEFAULT 0,
            created_at      TEXT DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    c.execute('''
        CREATE TABLE IF NOT EXISTS company_settings (
            id              TEXT PRIMARY KEY,
            company_id      TEXT NOT NULL UNIQUE,
            country         TEXT,
            timezone        TEXT,
            currency        TEXT,
            currency_symbol TEXT,
            business_type   TEXT,
            language        TEXT DEFAULT 'en',
            date_format     TEXT,
            fiscal_year_start TEXT
        )
    ''')

    conn.commit()

    # Versioned migration for everything added after the tables above (which
    # predate PRAGMA user_version in this file and stay as unconditional
    # CREATE TABLE IF NOT EXISTS). ensure_schema_version() takes a live
    # backup and runs PRAGMA integrity_check before AND after, only
    # advancing user_version on full success -- see
    # commercial_runtime/security/migration_safety.py.
    from commercial_runtime.security.migration_safety import ensure_schema_version
    ensure_schema_version(
        conn, DB_PATH, REGISTRY_SCHEMA_VERSION, _migrate_registry_schema,
        backup_dir=os.path.join(_db_dir, 'migration_backups'),
    )

    conn.close()


def log_audit(company_id: str, user_id, module_code: str, action: str,
              entity_type: str, entity_id: str,
              old_value: dict = None, new_value: dict = None, ip: str = None):
    conn = get_conn()
    c = conn.cursor()
    c.execute('''
        INSERT INTO audit_logs
          (id, company_id, user_id, module_code, action,
           entity_type, entity_id, old_value_json, new_value_json, ip_address)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (str(uuid.uuid4()), company_id, user_id, module_code, action,
          entity_type, entity_id,
          json.dumps(old_value) if old_value else None,
          json.dumps(new_value) if new_value else None, ip))
    conn.commit()
    conn.close()


def get_audit_log(company_id: str, entity_type: str = None,
                   entity_id: str = None, limit: int = 100) -> list:
    conn = get_conn()
    c = conn.cursor()
    if entity_type and entity_id:
        c.execute('''SELECT * FROM audit_logs
                     WHERE company_id=? AND entity_type=? AND entity_id=?
                     ORDER BY created_at DESC LIMIT ?''',
                  (company_id, entity_type, entity_id, limit))
    else:
        c.execute('SELECT * FROM audit_logs WHERE company_id=? ORDER BY created_at DESC LIMIT ?',
                  (company_id, limit))
    logs = [dict(row) for row in c.fetchall()]
    conn.close()
    return logs
