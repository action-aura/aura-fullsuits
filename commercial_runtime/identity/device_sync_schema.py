"""registry.db v9 (part 2 of 2) -- devices become a synced entity.

ROADMAP.md's "registry v9 CLAIMED" entry (Track S). Two changes:

1. `devices` gains five columns so a peer can tell how current a device row is
   and which build it runs:

       row_version             INTEGER NOT NULL DEFAULT 1   -- last-writer gate on the apply side
       updated_at_utc          TEXT                          -- NULL until the device announces
       app_version             TEXT
       retail_schema_version   INTEGER
       registry_schema_version INTEGER

   The three build-version columns are what the rollout rule reads: preset
   cards and role edits stay disabled while ANY active device reports a
   `registry_schema_version` below 9 (an old build stores but never reads the
   books rows, so granting the Accountant preset to a person who signs in on
   it would silently give them nothing).

2. `admin_device_assignment` -- ONE row per company saying which device is the
   main computer, with an `epoch` that only ever grows:

       company_id PRIMARY KEY, device_id, epoch, assigned_at_utc,
       assigned_by_user_uid, assigned_by_device_id

   WHY A SEPARATE TABLE AND NOT THE FLAG. `devices.is_admin_device` is guarded
   by the partial unique index `idx_devices_one_admin` -- the database, not
   code, guarantees one holder. That flag is LOCAL truth about what THIS
   device believes. Syncing it directly would let two devices each holding it
   in their own copy collide the moment they exchange rows (the index rejects
   the second, the event is lost). So the flag stays a derived local cache and
   the synced fact is the assignment: "device D holds epoch N". A receiver
   accepts the higher epoch, then recomputes the flag clear-then-set in one
   transaction. `idx_devices_one_admin` is deliberately untouched.

BACKFILL (no emit). For every company that already has a device with
`is_admin_device=1`, insert epoch 1, `assigned_at_utc` = that device row's
`first_seen_at`. That is a PROXY -- the true claim time was never recorded --
and it is documented as such: it is only ever used to break an epoch tie, and
ties between backfilled rows on two devices that both believed they were main
resolve to the smaller device_id, deterministically, on every device. INSERT OR
IGNORE, so a re-run never overwrites a real assignment.

Additive only and idempotent: `PRAGMA table_info` guards every ALTER,
`CREATE TABLE IF NOT EXISTS`, `INSERT OR IGNORE`. Emits nothing.
"""
from __future__ import annotations

import sqlite3

_DEVICE_COLUMNS = (
    ("row_version", "INTEGER NOT NULL DEFAULT 1"),
    ("updated_at_utc", "TEXT"),
    ("app_version", "TEXT"),
    ("retail_schema_version", "INTEGER"),
    ("registry_schema_version", "INTEGER"),
)


def apply_device_sync_schema(conn: sqlite3.Connection) -> None:
    cols = {row[1] for row in conn.execute("PRAGMA table_info(devices)").fetchall()}
    for name, decl in _DEVICE_COLUMNS:
        if name not in cols:
            conn.execute(f"ALTER TABLE devices ADD COLUMN {name} {decl}")

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS admin_device_assignment (
            company_id            TEXT PRIMARY KEY,
            device_id             TEXT NOT NULL,
            epoch                 INTEGER NOT NULL,
            assigned_at_utc       TEXT NOT NULL,
            assigned_by_user_uid  TEXT,
            assigned_by_device_id TEXT
        )
        """
    )

    # One assignment per company that already has a main computer. `first_seen_at`
    # is the documented proxy for the claim time (see module docstring).
    conn.execute(
        """
        INSERT OR IGNORE INTO admin_device_assignment
            (company_id, device_id, epoch, assigned_at_utc)
        SELECT company_id, id, 1, first_seen_at
          FROM devices
         WHERE is_admin_device = 1
        """
    )
    conn.commit()
