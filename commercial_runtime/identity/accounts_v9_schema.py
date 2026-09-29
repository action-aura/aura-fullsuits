"""registry.db v9 (part 1 of 2) -- two nullable columns on `users`:
`display_name` and `role_preset`.

ROADMAP.md's "registry v9 CLAIMED" entry (Track S). Two additions, both for
the Team screen and the Accountant role:

    ALTER TABLE users ADD COLUMN display_name TEXT   -- the person's name, 1-60 chars
    ALTER TABLE users ADD COLUMN role_preset  TEXT   -- 'cashier'|'manager'|'branch_manager'|'accountant'

WHY NULLABLE WITH NO DEFAULT AND NO BACKFILL. Identical reasoning to
branch_scope_schema.py (v7), and it matters more here because `users` lives in
`registry.db`, SHARED by Retail and Clinic: SQLite writes NULL into every
existing row, Retail's and Clinic's alike, and NULL is defined to mean "no
name set / no preset chosen" -- exactly today's behaviour for every row that
existed before this ran. Clinic never reads either column. A backfill (say,
deriving a name from the email) would invent data and would need to emit a sync
event per row to be honest about it; leaving NULL needs neither.

THIS MIGRATION EMITS NOTHING. No `sync_outbox` row is written by either the
column adds or the capability seed that runs alongside them (see the call-site
comment in registry_db.py::_migrate_registry_schema). A device upgrading is not
making a change its peers need to hear about; each device reaches the same
state by running the same migration.

The four `retail.books.*` capability rows are NOT created here. They are
created by `account_schema._seed_capability_rows`, which this same version's
migration re-runs (see registry_db.py: every step re-runs on any database
behind the version, and that seed is INSERT OR IGNORE over
`user_permissions`). So an existing manager gets four books rows at 'none' and
the owner four at 'full', for free, with nothing here to get wrong.

Additive only, guarded by `PRAGMA table_info` so a re-run against a database
that already has the columns (a retry after a partial failure, or a database
that started life at v9) is a no-op rather than an error.
"""
from __future__ import annotations

import sqlite3


def apply_accounts_v9_schema(conn: sqlite3.Connection) -> None:
    cols = {row[1] for row in conn.execute("PRAGMA table_info(users)").fetchall()}
    if "display_name" not in cols:
        conn.execute("ALTER TABLE users ADD COLUMN display_name TEXT")
    if "role_preset" not in cols:
        conn.execute("ALTER TABLE users ADD COLUMN role_preset TEXT")
    conn.commit()
