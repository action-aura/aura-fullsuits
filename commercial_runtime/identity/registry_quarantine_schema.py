"""registry.db v6 -- registry.db gets its own `sync_apply_quarantine` table.

Phase 5 wave B2 stage 2a (docs/launch-readiness/phase5-waveb2-user-sync.md
§Decision 4) wires an actual `user` apply branch into
`commercial_runtime/sync/sync_service.py`'s `_apply_event`. That branch has
to catch `users`' two non-wire UNIQUE constraints (`email`, `UNIQUE(company_id,
employee_id)`) BY NAME and park the losing event via the existing
`SyncService._quarantine_apply_event` -- exactly the mechanism
`sync_apply_quarantine` already gives retail.db's five money-moving/wave-B
entity types (see products/retail/backend/database/schema.py's own
`CREATE TABLE sync_apply_quarantine` comment for the full "why a quarantine
table, not a raised IntegrityError" reasoning -- wave A's defect #1,
reproduced by construction the moment a SECOND unique constraint exists on a
synced table's own wire-identity column).

`_quarantine_apply_event`/`_has_quarantined_events`/`_retry_quarantined_events`
(sync_service.py) all operate on WHATEVER connection they are called with --
for the registry-configured `SyncService` instance (Decision 6: a SECOND
instance whose `get_conn` returns registry.db), that connection is
registry.db's own. Calling `_quarantine_apply_event` against a registry.db
connection with no `sync_apply_quarantine` table would raise
`sqlite3.OperationalError: no such table: sync_apply_quarantine` on the FIRST
duplicate-email/duplicate-employee_id event -- the exact "loud, permanent
wedge" shape this whole mechanism exists to avoid, just one layer up. This
migration is what closes that gap.

WHY A NEW VERSION (v6), NOT A SILENT EDIT TO v5's OWN MIGRATION FUNCTION:
`apply_registry_sync_schema` (registry_sync_schema.py, v5) already shipped
and already ran against any registry.db that has reached v5 --
`ensure_schema_version`'s whole point is a fast no-op once a database is
already at its target version, so `_migrate_registry_schema` (and everything
nested inside it, including `apply_registry_sync_schema`) never runs again
for a v5-or-later database. Adding a new `CREATE TABLE` to that already-run
function would silently never reach a database that upgraded to v5 before
this change landed -- the identical class of bug
`commercial_runtime/security/migration_safety.py`'s whole versioned-migration
discipline exists to prevent. A new version, appended last in
`_migrate_registry_schema` per that function's own "new steps go last"
convention, is what actually guarantees this table exists everywhere v5
already does.

Shape matches products/retail/backend/database/schema.py's
`sync_apply_quarantine` verbatim -- same columns, same composite PRIMARY KEY
`(entity_id, event_type)` (see that table's own comment for why entity_id
alone would be too coarse: a sale_item and a payment could share an
entity_id from two different entity types converging on the same underlying
row across a device's lifetime). This is not a new design; it is the
existing client-side apply-quarantine pattern, given its own file for the
same reason registry_sync_schema.py was: `users` lives in a different
database than every other synced table.

Additive only: one `CREATE TABLE IF NOT EXISTS` -- no `ALTER TABLE`, no
rebuild, no `DROP`, no `RENAME`. Idempotent by construction, safe to run
twice and safe against a database that already has this table (a re-run
after a partial failure, or one that started life at v6 or later).

Wired into registry_db.py's `_migrate_registry_schema` at
REGISTRY_SCHEMA_VERSION=6 and applied through
commercial_runtime.security.migration_safety.ensure_schema_version, which
takes a live backup and runs PRAGMA integrity_check before AND after, only
advancing `user_version` on full success.

registry.db is SHARED WITH CLINIC. One brand-new, empty-until-used table
costs Clinic nothing: Clinic does not sync today (its backend never imports
`commercial_runtime.sync` at all), so this migration is invisible to every
Clinic read path -- no query anywhere in products/clinic could even name
`sync_apply_quarantine`.
"""
from __future__ import annotations

import sqlite3


def apply_registry_quarantine_schema(conn: sqlite3.Connection) -> None:
    """Idempotent -- see the module docstring's "Additive only" paragraph.
    `CREATE TABLE IF NOT EXISTS` needs no existence guard before it, unlike
    account_schema.py's column-by-column `ALTER TABLE` steps."""
    conn.execute('''
        CREATE TABLE IF NOT EXISTS sync_apply_quarantine (
            entity_id TEXT NOT NULL,
            entity_type TEXT NOT NULL,
            event_type TEXT NOT NULL,
            payload TEXT NOT NULL,
            reason TEXT NOT NULL,
            detail TEXT,
            quarantined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (entity_id, event_type)
        )
    ''')
    conn.commit()
