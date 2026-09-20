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

Additive only: two `CREATE TABLE IF NOT EXISTS` statements, one
`INSERT OR IGNORE` seed row, and -- since v8 -- one guarded
`ALTER TABLE ... ADD COLUMN`. No rebuild, no `DROP`, no `RENAME`.

v8 (2026-09-20) IS WHY THIS PARAGRAPH CHANGED, and the change matters more
than the column. It used to say "no `ALTER TABLE` ... this needs no
`PRAGMA table_info` guard at all", which stopped being true the moment a
column had to be added to a table that already exists in the field. It now
carries one, in exactly account_schema.py's shape.

THE DRIFT THIS FIXES. The paragraph above still says these tables mirror
retail's "verbatim -- same columns, same defaults". That was true when
written. Retail schema **v31** then added `sync_cursor.relay_url` to
retail.db and this copy did not follow, with nothing asserting the mirror
still held. products/retail/backend/app.py registers
`make_sync_internal_blueprint` TWICE -- once at `/api/sync` on the retail
connection, once at `/api/registry-sync` on this database's -- and the
shared `_internal/cursor` route calls
`SyncService.ensure_cursor_matches_relay()` whenever the client passes a
`relay_url` parameter. Android's `SyncCoordinator` passes it. So the
registry stream queried a column living in the other database and returned
HTTP 500, on every install, not merely on a stale one:

    sqlite3.OperationalError: no such column: relay_url

Found in logcat on a real phone, then reproduced against a fresh database
built by this function alone (columns were exactly ['id', 'last_seq']).

SEMANTICS ARE RETAIL v31'S, UNCHANGED ON PURPOSE. A stored NULL means
"different from any real relay", so the first pull after upgrading resets
`last_seq` to 0 exactly once -- a harmless one-time catch-up pull, which is
the cost retail's own v31 comment already argues for. Copying that rule
rather than inventing a kinder one keeps the two streams explainable by a
single sentence.

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


def _sync_cursor_columns(conn: sqlite3.Connection) -> set:
    """Live column names on `sync_cursor`. Mirrors account_schema.py's
    `_user_columns` rather than inventing a second way to ask the same
    question."""
    return {row[1] for row in conn.execute("PRAGMA table_info(sync_cursor)").fetchall()}


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

    # v8: the column retail.db's own `sync_cursor` gained at retail v31.
    # Guarded, because unlike the two statements above this one is NOT
    # naturally idempotent -- `ALTER TABLE ... ADD COLUMN` raises
    # "duplicate column name" on a second run, and every step wired into
    # `ensure_schema_version` has to be safe to run twice (a database
    # upgrading from v4 runs the whole chain; one already at v7 runs it
    # again when the version bumps to 8).
    #
    # NO DEFAULT, deliberately. NULL is the signal
    # `SyncService.ensure_cursor_matches_relay` reads as "this cursor is not
    # bound to any known relay yet", which makes the first pull after the
    # upgrade reset `last_seq` to 0 once. A default of '' or of the current
    # relay would suppress that reset and leave a high-water mark pointing
    # into a relay it was never measured against -- the exact confusion
    # retail v31 exists to prevent.
    if "relay_url" not in _sync_cursor_columns(conn):
        conn.execute("ALTER TABLE sync_cursor ADD COLUMN relay_url TEXT")

    conn.commit()
