"""registry.db v3 -- the multi-device account model.

Implements docs/launch-readiness/multi-device-design.md §6's "registry v3"
bullet: `users` gains the columns the wire identity and the sync layer need,
`role` widens from {admin, employee} to {admin, manager, cashier}, and
capability rows are seeded into the EXISTING `user_permissions` table.

Adds to `users`:
  - `uid TEXT` + a unique index -- the WIRE identity. The local `id` (already
    a uuid4 string) stays the primary key and stays a private detail of this
    install; `uid` is what a peer device names a user by. Backfilled with one
    fresh uuid4 per existing row.
  - `pin_hash TEXT` -- see user_accounts.py's PIN section. A PIN is
    attribution, never authorization.
  - `row_version INTEGER NOT NULL DEFAULT 1` -- the reject-stale marker for
    the admin-device single-writer rule (design §4).
  - `updated_at_utc TEXT`, `deleted_at_utc TEXT` -- last-write stamp and soft
    tombstone. Hard DELETE on a synced table is forbidden by design §8, so
    "this employee left" has to be a value in a column, not a missing row.

Additive only. Every statement is `ALTER TABLE ... ADD COLUMN`, `CREATE ...
IF NOT EXISTS`, or a guarded `UPDATE` -- no table rebuild, no DROP, no
RENAME, no id_map. This is the same discipline
products/retail/backend/database/schema.py documents for retail.db, applied
here, and it is why an install with live shop data can take this migration
with zero risk of losing a row.

Wired into registry_db.py's `_migrate_registry_schema` at
REGISTRY_SCHEMA_VERSION=3 and applied through
commercial_runtime.security.migration_safety.ensure_schema_version, which
takes a live backup and runs PRAGMA integrity_check before AND after, only
advancing `user_version` on full success. A v0 (pre-user_version) database
upgrading straight to v3 runs v1, v2 and v3 in one pass -- which is why every
step below must tolerate being re-run.
"""
from __future__ import annotations

import sqlite3
import uuid

from commercial_runtime.identity.user_accounts import (
    LEGACY_ROLE_EMPLOYEE,
    ROLE_CASHIER,
    now_utc_iso,
    seed_capabilities_for_user,
)

#: How the widened role domain maps the values that can exist on a v2
#: database. `users.role` has only ever been written as 'admin' (create_admin)
#: or 'employee' (create_employee), so these two rows are the whole real
#: migration surface; the NULL/'' case is defence against a hand-edited row.
#:
#:   admin     -> admin      (unchanged -- the owner account)
#:   employee  -> cashier    (least privilege: an existing non-admin staff
#:                            member is a till user until an owner decides
#:                            otherwise. Promoting somebody to manager is an
#:                            authority decision, and a migration is the
#:                            wrong place to guess at one -- guessing high
#:                            would silently hand every existing employee
#:                            refunds, discounts, stock adjustments and
#:                            cash-variance approval on upgrade day.)
#:   NULL/''   -> cashier    (same reasoning)
#:
#: Any other value is left EXACTLY as stored. Rewriting data we cannot
#: explain would destroy the only evidence of how it got there;
#: `user_accounts.normalize_role()` reads such a row as 'cashier' at decision
#: time, so an uninterpretable role is powerless without being erased.
_LEGACY_ROLE_MAP = {LEGACY_ROLE_EMPLOYEE: ROLE_CASHIER}


def _user_columns(conn: sqlite3.Connection) -> set:
    return {row[1] for row in conn.execute("PRAGMA table_info(users)").fetchall()}


def apply_account_schema(conn: sqlite3.Connection) -> None:
    """Idempotent -- inspects the live schema before altering and guards every
    backfill on the rows that still need it, so it is safe against a database
    that already has these columns (a re-run after a partial failure, or one
    that started life at v3 or later)."""
    cols = _user_columns(conn)

    if "uid" not in cols:
        # No inline UNIQUE: SQLite's ALTER TABLE ADD COLUMN cannot add a
        # unique constraint, and adding one the "proper" way means rebuilding
        # the table -- exactly what design §6 forbids on live data. A separate
        # unique index below is the same guarantee without the rebuild.
        conn.execute("ALTER TABLE users ADD COLUMN uid TEXT")

    if "pin_hash" not in cols:
        conn.execute("ALTER TABLE users ADD COLUMN pin_hash TEXT")

    if "row_version" not in cols:
        # NOT NULL is only legal on ADD COLUMN because a non-null DEFAULT is
        # supplied -- SQLite backfills every existing row with it in place.
        conn.execute("ALTER TABLE users ADD COLUMN row_version INTEGER NOT NULL DEFAULT 1")

    if "updated_at_utc" not in cols:
        conn.execute("ALTER TABLE users ADD COLUMN updated_at_utc TEXT")

    if "deleted_at_utc" not in cols:
        # Left NULL for every existing row: nobody has been deleted yet, and
        # a tombstone stamped by a migration would be a lie about when.
        conn.execute("ALTER TABLE users ADD COLUMN deleted_at_utc TEXT")

    _backfill_uids(conn)

    # Partial index, matching device_registry.py's idx_devices_fingerprint.
    # SQLite already treats NULLs as distinct in a unique index, so the
    # predicate is documentation rather than a behaviour change: it says out
    # loud that "no uid yet" is a permitted state (a row inserted by an older
    # code path between this migration and the next launch) while two rows
    # sharing a uid never is.
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_users_uid ON users(uid) WHERE uid IS NOT NULL"
    )

    _migrate_roles(conn)
    _backfill_row_metadata(conn)
    _seed_capability_rows(conn)

    conn.commit()


def _backfill_uids(conn: sqlite3.Connection) -> None:
    """One fresh uuid4 per row that has no wire identity yet.

    Generated in Python, one UPDATE per row, rather than in SQL: SQLite has no
    built-in uuid4, and the usual `lower(hex(randomblob(16)))` trick produces
    a 32-char string that is NOT a valid RFC-4122 UUID. Owner's sync ingest
    gates on `uuid.UUID(entity_id)` (design §8), so a value that merely looks
    random would be rejected on the wire later, at a point where it is far
    more expensive to discover. A shop has tens of accounts, not millions --
    the loop costs nothing.
    """
    rows = conn.execute(
        "SELECT id FROM users WHERE uid IS NULL OR TRIM(uid) = ''"
    ).fetchall()
    for row in rows:
        conn.execute(
            "UPDATE users SET uid=? WHERE id=?", (str(uuid.uuid4()), row[0])
        )


def _migrate_roles(conn: sqlite3.Connection) -> None:
    """Widen {admin, employee} to {admin, manager, cashier} -- see
    `_LEGACY_ROLE_MAP` for the mapping and the reasoning behind it.

    There is no CHECK constraint on `users.role` (registry_db.py:108 is a
    plain `TEXT DEFAULT 'employee'`), so "widening" here is a data
    migration, not a DDL change -- and adding a CHECK now would require the
    table rebuild this file exists to avoid.
    """
    for legacy_value, new_value in _LEGACY_ROLE_MAP.items():
        conn.execute(
            "UPDATE users SET role=? WHERE LOWER(TRIM(COALESCE(role, '')))=?",
            (new_value, legacy_value),
        )
    conn.execute(
        "UPDATE users SET role=? WHERE role IS NULL OR TRIM(role)=''",
        (ROLE_CASHIER,),
    )
    # Anything already inside the widened domain (ROLES) is left alone, and so
    # is anything outside it -- see _LEGACY_ROLE_MAP's docstring.


def _backfill_row_metadata(conn: sqlite3.Connection) -> None:
    """Stamp `updated_at_utc` on rows that have none.

    Uses the migration's own timestamp rather than `created_at`: this is the
    "when did this row last change" marker the sync layer compares, and the
    row genuinely did just change (it gained a uid and possibly a new role).
    Backdating it to creation time would tell a peer the row is older than
    the version it is about to receive.

    `row_version` needs no backfill -- ADD COLUMN's DEFAULT 1 already wrote
    it into every existing row.
    """
    conn.execute(
        "UPDATE users SET updated_at_utc=? WHERE updated_at_utc IS NULL OR TRIM(updated_at_utc)=''",
        (now_utc_iso(),),
    )


def _seed_capability_rows(conn: sqlite3.Connection) -> None:
    """Seed the eight capability codes for every existing account.

    Runs AFTER `_migrate_roles`, so a migrated 'employee' is seeded as the
    cashier it just became rather than as the unrecognised value it was.

    `seed_capabilities_for_user` is INSERT OR IGNORE, so this cannot disturb
    a pre-existing `user_permissions` row -- including the legacy
    subsystem='retail' grants `mt_require_subsystem` reads today, which use a
    different `subsystem` value entirely and are therefore untouched by
    construction, not merely by luck.
    """
    for row in conn.execute("SELECT id, role FROM users").fetchall():
        seed_capabilities_for_user(conn, row[0], row[1])
