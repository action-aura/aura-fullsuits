"""registry.db v9 (part 2 of 2) -- devices become a synced entity.

ROADMAP.md's "registry v9 CLAIMED" entry (Track S). Two changes, plus two small additions
from the 2026-09-29 security review (the `takeover` column on the assignment
row, and the device-local `takeover_lockout` table; both explained where they
are created below):

1. `devices` gains six columns so a peer can tell how current a device row is
   and which build it runs:

       row_version             INTEGER NOT NULL DEFAULT 1   -- last-writer gate for what the DEVICE says about itself
       admin_row_version       INTEGER NOT NULL DEFAULT 1   -- last-writer gate for what the OWNER decides (label, status)
       updated_at_utc          TEXT                          -- NULL until the device announces
       app_version             TEXT
       retail_schema_version   INTEGER
       registry_schema_version INTEGER

   TWO COUNTERS, NOT ONE (fix round 2, item 12). Every start and heartbeat of a
   computer used to bump the same `row_version` that the owner's rename,
   revoke and reinstate bump, so a computer that restarted more often than the
   owner acted silently outvoted the owner: the rename never reached it and a
   removal was overwritten. The owner's fields (label, status) are now gated
   by `admin_row_version`, which ONLY the owner's actions (and the one-time
   fill of a blank label) move; the device's own facts (platform, app and
   schema versions) stay under `row_version`; `last_seen_at` is merged with
   MAX and is never versioned. Added inside this same v9 step, PRAGMA-guarded
   and idempotent -- NO new registry schema version: v9 has not shipped, and a
   v8 peer skips `device` events entirely, so no mixed build reads it.

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
    ("admin_row_version", "INTEGER NOT NULL DEFAULT 1"),
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

    # 2026-09-29 security review (M1): a takeover is now marked on the wire, so
    # every receiver can write a WARNING audit row "the main was taken over"
    # instead of seeing an assignment indistinguishable from a hand-over.
    # Additive, PRAGMA-guarded like every other column here. 0 = ordinary
    # claim/transfer, 1 = written by a guarded takeover.
    assignment_cols = {row[1] for row in conn.execute(
        "PRAGMA table_info(admin_device_assignment)").fetchall()}
    if "takeover" not in assignment_cols:
        conn.execute("ALTER TABLE admin_device_assignment ADD COLUMN takeover INTEGER NOT NULL DEFAULT 0")

    # 2026-09-29 security review (M2): the takeover password prompt used to
    # spend the SAME failed_login_count as the login screen, so anyone at an
    # unlocked till could lock the owner out of signing in by guessing here,
    # and the takeover had no back-off of its own. This table is the takeover's
    # own counter: keyed per user AND per device (a lock describes THIS till
    # being attacked -- the same reasoning as failed_login_count, which is why
    # it is deliberately not synced and no sync payload names this table).
    #   failed_count      consecutive wrong passwords since the last success
    #   locked_until_utc  NULL until the 3rd miss; then a delay that doubles
    #                     from 15 minutes on each further miss
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS takeover_lockout (
            user_id           TEXT NOT NULL,
            device_id         TEXT NOT NULL,
            failed_count      INTEGER NOT NULL DEFAULT 0,
            locked_until_utc  TEXT,
            last_failed_utc   TEXT,
            PRIMARY KEY (user_id, device_id)
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
