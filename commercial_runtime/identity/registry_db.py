"""
Aura FullSuits -- multi-tenant registry database (trimmed).

Owns the tables that back tenant identity, licensing, and audit for every
product in this suite: `users` (multi-tenant accounts), `company_modules`
(per-tenant product/module licensing), `user_permissions` (per-user subsystem
access), `audit_logs` (append-oriented security/audit trail).

This is a deliberately trimmed extraction of Action Aura Enterprise's
database/registry_db.py (which owns ~20 tables for the full platform --
EIP module catalog, document flow, numbering sequences, custom fields, etc.
that Retail/Clinic never touch). Only the tables actually read/written by
commercial_runtime.identity.mt_auth and commercial_runtime.security.audit are
kept here. See docs/migration/dependency-map.md §1.
"""
import os
import sqlite3
import json
import uuid

_app_data = os.environ.get('AURA_APP_DATA') or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_db_dir = os.path.join(_app_data, 'database')
DB_PATH = os.path.join(_db_dir, 'registry.db')


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

    conn.commit()
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
