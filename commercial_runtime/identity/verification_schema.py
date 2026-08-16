"""registry.db v2 -- email verification + password reset.

Adds:
  - `users.email_verified_at` (TEXT, nullable) -- NULL means unverified.
    Set once, on successful `/api/auth/verify-email`, never cleared back to
    NULL by anything in this codebase (a changed email would need its own
    re-verification flow -- not built, since nothing here lets a user change
    their email today).
  - `secure_links.purpose` (TEXT NOT NULL DEFAULT 'employee_setup') --
    secure_links predates this column and was single-purpose (employee
    invite links only); the default backfills every pre-existing row with
    that same purpose so `onboarding_routes.py::employee_setup`'s existing
    lookups (which never filtered on purpose) keep matching exactly the rows
    they always matched. New link types ('email_verification',
    'password_reset') are created with their own explicit purpose from here
    on -- see verification.py.

Wired into registry_db.py's `_migrate_registry_schema` at
REGISTRY_SCHEMA_VERSION=2, applied via commercial_runtime.security.
migration_safety.ensure_schema_version -- same paranoid backup/integrity-
check-before-and-after discipline as every other product's schema.py.
"""
from __future__ import annotations

import sqlite3


def apply_email_verification_schema(conn: sqlite3.Connection) -> None:
    """Idempotent -- inspects the live schema before altering, safe to run
    against a database that already has these columns (e.g. re-run after a
    partial failure, or a database that started life at v2 or later)."""
    user_cols = {row[1] for row in conn.execute("PRAGMA table_info(users)").fetchall()}
    if "email_verified_at" not in user_cols:
        conn.execute("ALTER TABLE users ADD COLUMN email_verified_at TEXT")

    link_cols = {row[1] for row in conn.execute("PRAGMA table_info(secure_links)").fetchall()}
    if "purpose" not in link_cols:
        conn.execute("ALTER TABLE secure_links ADD COLUMN purpose TEXT NOT NULL DEFAULT 'employee_setup'")

    conn.execute("CREATE INDEX IF NOT EXISTS idx_secure_links_token_hash ON secure_links(token_hash)")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_secure_links_email_purpose ON secure_links(email_target, purpose)"
    )
    conn.commit()
