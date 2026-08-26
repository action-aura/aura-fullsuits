"""registry.db v5 -- the registry-side sync outbox and cursor.

Phase 5 wave B2 (docs/launch-readiness/phase5-waveb2-user-sync.md, Decision
1) will eventually sync `users`/`user_permissions` -- both of which live in
`registry.db`, a DIFFERENT database file from `retail.db`'s existing
`sync_outbox`/`sync_cursor`. Three options were weighed there: (a) give
registry.db its own outbox/cursor, (b) `ATTACH` registry.db to the retail
connection and write both in one transaction, (c) write user events into
retail.db's outbox from the identity write sites. (b) was RULED OUT -- both
databases run `PRAGMA journal_mode=WAL` (this file's own `get_conn()` and
products/retail/backend/database/schema.py both set it), and SQLite gives no
cross-database atomic commit once any attached database is in WAL, so it
would look correct and lose the event exactly when the process died between
the two commits. (c) was rejected because it would make
`commercial_runtime.identity` -- code Clinic also runs -- depend on a Retail
table. (a) is what this file builds.

STAGE 1 SCOPE, deliberately narrow: this migration builds ONLY the schema a
second `SyncService` stream needs -- `sync_outbox` and `sync_cursor` in
registry.db itself, mirroring retail's own tables verbatim. It does NOT add
a `user` entity type anywhere, does NOT emit a single user sync event, and
does NOT touch any of the ~26 write sites against `users`/`user_permissions`
-- that is wave B2 stage 2's job. Stage 1 exists to get the schema AND the
`commercial_runtime/sync/sync_service.py` cross-stream allowlist guard in
place and PROVEN before any second stream ever pulls a foreign event --
see that module's own docstring for why the guard has to come first.

Shape matches products/retail/backend/database/schema.py's
`sync_outbox`/`sync_cursor` tables verbatim -- same columns, same defaults,
same `PRIMARY KEY CHECK (id = 1)` shape on `sync_cursor`. This is not a new
design; it is the existing outbox pattern, given its own file because
`users` lives in a different database than the row it will eventually
belong to.

Additive only: two `CREATE TABLE IF NOT EXISTS` statements and one
`INSERT OR IGNORE` seed row -- no `ALTER TABLE`, no rebuild, no `DROP`, no
`RENAME`. Unlike account_schema.py's `ALTER TABLE ... ADD COLUMN` steps,
this needs no `PRAGMA table_info` guard at all: `CREATE TABLE IF NOT EXISTS`
and `INSERT OR IGNORE` are both naturally idempotent, so this is safe to run
twice and safe on a database that already has these tables (a re-run after
a partial failure, or one that started life at v5 or later).

Wired into registry_db.py's `_migrate_registry_schema` at
REGISTRY_SCHEMA_VERSION=5 and applied through
commercial_runtime.security.migration_safety.ensure_schema_version, which
takes a live backup and runs PRAGMA integrity_check before AND after, only
advancing `user_version` on full success.

registry.db is SHARED WITH CLINIC (see registry_db.py's own module
docstring). Two brand-new, empty-until-used tables cost Clinic nothing:
Clinic does not sync today, and its backend never imports
`commercial_runtime.sync` at all, so this migration is invisible to every
Clinic read path -- there is no query anywhere in products/clinic that could
even name `sync_outbox`/`sync_cursor`.
"""
from __future__ import annotations

import sqlite3


def apply_registry_sync_schema(conn: sqlite3.Connection) -> None:
    """Idempotent -- see the module docstring's "Additive only" paragraph
    for why this needs no existence guard before each statement, unlike
    account_schema.py's column-by-column `ALTER TABLE` steps."""
    conn.execute('''
        CREATE TABLE IF NOT EXISTS sync_outbox (
            id TEXT PRIMARY KEY,
            entity_type TEXT NOT NULL,
            entity_id TEXT NOT NULL,
            event_type TEXT NOT NULL,
            payload TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    conn.execute('''
        CREATE TABLE IF NOT EXISTS sync_cursor (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            last_seq INTEGER NOT NULL DEFAULT 0
        )
    ''')
    conn.execute("INSERT OR IGNORE INTO sync_cursor (id, last_seq) VALUES (1, 0)")
    conn.commit()
