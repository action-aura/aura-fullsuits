"""
Aura FullSuits -- multi-tenant registry database (trimmed).

Owns the tables that back tenant identity, licensing, and audit for every
product in this suite: `users` (multi-tenant accounts), `company_modules`
(per-tenant product/module licensing), `user_permissions` (per-user subsystem
access), `audit_logs` (append-oriented security/audit trail), `secure_links`
(one-time employee-invite tokens), `company_settings` (locale/business
fields written by onboarding), `sync_outbox`/`sync_cursor` (v5, Phase 5 wave
B2 stage 1 -- registry.db's OWN outbox/cursor pair, mirroring retail.db's;
see registry_sync_schema.py -- schema only, no write-site emits through it
yet), `sync_apply_quarantine` (v6, Phase 5 wave B2 stage 2a -- registry.db's
OWN apply-side quarantine table, mirroring retail.db's; see
registry_quarantine_schema.py. `_apply_event`'s `user` branch
(commercial_runtime/sync/sync_service.py) was wired and proven at that stage;
stage 2b -- commercial_runtime/identity/user_accounts.py's
`_queue_user_sync_event` and its call sites -- has since made every
allowlisted write to `users` actually queue an event through it).

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
# v4: identity-side company_id rebind from md5(admin_email) to the
# Owner-issued license_public_id, across every company_id-bearing table in
# THIS database -- discovered at runtime, never hardcoded. Launch-readiness
# Phase 5 prerequisite #1, reserved in ROADMAP.md's 2026-08-21 ledger. See
# company_rebind.py and docs/launch-readiness/phase5-prerequisites.md §1.
# Retail's own v14 (products/retail/backend/database/schema.py) is
# deliberately inert until this lands -- it only ever converges onto a
# tenant key THIS migration has already adopted, never the other way round.
# v5: registry.db gets its own `sync_outbox`/`sync_cursor` -- Phase 5 wave
# B2 stage 1, reserved in ROADMAP.md's 2026-08-21 ledger. `users` lives here,
# not in retail.db, so its future sync stream needs its own outbox in the
# SAME file as the row it will queue an event for -- ATTACH-ing registry.db
# to the retail connection was considered and ruled out (both databases run
# WAL mode; SQLite gives no cross-database atomic commit once either side
# is in WAL). See registry_sync_schema.py and
# docs/launch-readiness/phase5-waveb2-user-sync.md §Decision 1. This
# migration builds ONLY the schema -- no `user` entity type, no user sync
# event, no write-site change; that is wave B2 stage 2.
# v6: registry.db gets its own `sync_apply_quarantine` -- Phase 5 wave B2
# stage 2a, reserved in ROADMAP.md's 2026-08-21 ledger. `_apply_event`'s new
# `user` branch (sync_service.py) has to catch `users`' two non-wire UNIQUE
# constraints (`email`, `UNIQUE(company_id, employee_id)`) BY NAME and park
# the losing event via `SyncService._quarantine_apply_event` -- exactly
# wave A's defect #1, reproduced by construction the moment a second unique
# constraint exists on a synced table's own wire-identity column -- and that
# helper writes unconditionally to `sync_apply_quarantine` on WHATEVER
# connection it is called with. See registry_quarantine_schema.py and
# docs/launch-readiness/phase5-waveb2-user-sync.md §Decision 4.
# v7: `users` gains ONE nullable column, `branch_scope_uid` -- ROADMAP.md's
# 2026-08-30 "registry schema v7 CLAIMED for branch-scoped users" entry and
# docs/launch-readiness/account-hierarchy-design.md §3.3/§6. NULL (every
# existing row, on both products) means "every branch"; a non-NULL value is
# a `branches.uid` a Retail account is scoped to. See
# branch_scope_schema.py's own module docstring for the full "why nullable
# with no default is what keeps Clinic byte-identical" reasoning.
REGISTRY_SCHEMA_VERSION = 7


def _migrate_registry_schema(conn):
    """The single `migrate_fn` handed to `ensure_schema_version` -- runs
    every migration this file owns, in version order, on any database
    behind REGISTRY_SCHEMA_VERSION, same "run every step, each
    independently idempotent" shape as products/retail/backend/database/
    schema.py::_migrate_retail_schema. A v0 (pre-user_version) database
    upgrading straight to v5 runs all five steps in one pass.

    New steps are appended LAST and never reordered: a database that is
    already at v2 still runs v1 and v2 on its way to v5 (there is one
    version gate for the whole function, not one per step), so each step has
    to be a no-op against a database that already has its changes -- which
    is exactly what each of them is.

    v4 (the company_id rebind) is appended after v3 deliberately, not merely
    by the "new steps go last" convention: v3 (account_schema.py) is what
    gives `users` its `uid`/`row_version`/`updated_at_utc` columns, and while
    the rebind itself does not read them, running it before v3 would still
    mean it acts on a database whose account model is mid-upgrade -- the
    same "finish the schema shape before touching tenant identity" ordering
    retail's v14 follows relative to its own v13.

    v5 (registry_sync_schema.py) has no ordering dependency on v1-v4 at all
    -- it only ever creates two brand-new tables (`sync_outbox`/
    `sync_cursor`) that nothing before it touches or reads -- but is still
    appended last, per the same convention, so the "new steps go last"
    invariant stays simple to reason about rather than needing a case-by-case
    justification for every future step's position.

    v6 (registry_quarantine_schema.py) has the identical "no ordering
    dependency, appended last anyway" shape as v5 -- one more brand-new
    table (`sync_apply_quarantine`) nothing before it touches or reads.

    v7 (branch_scope_schema.py) is a single `ALTER TABLE users ADD COLUMN`,
    same shape as v3's own column additions -- appended last per the same
    convention, and with no ordering dependency on v1-v6: it only ever adds
    a column nothing before it reads or writes."""
    from commercial_runtime.identity.device_registry import apply_identity_device_schema
    from commercial_runtime.identity.verification_schema import apply_email_verification_schema
    from commercial_runtime.identity.account_schema import apply_account_schema
    from commercial_runtime.identity.company_rebind import _migrate_rebind_registry_company_id_to_owner_issued
    from commercial_runtime.identity.registry_sync_schema import apply_registry_sync_schema
    from commercial_runtime.identity.registry_quarantine_schema import apply_registry_quarantine_schema
    from commercial_runtime.identity.branch_scope_schema import apply_branch_scope_schema
    apply_identity_device_schema(conn)
    apply_email_verification_schema(conn)
    apply_account_schema(conn)
    _migrate_rebind_registry_company_id_to_owner_issued(conn)
    apply_registry_sync_schema(conn)
    apply_registry_quarantine_schema(conn)
    apply_branch_scope_schema(conn)


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
