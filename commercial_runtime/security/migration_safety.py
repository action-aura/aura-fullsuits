"""
Aura FullSuits -- schema migration safety helper (Wave 1B, Part K).

Both products' schema.py files run their `ALTER TABLE ... ADD COLUMN`
migrations unconditionally on every single app launch, wrapped in a
try/except that swallows "column already exists" errors. That's safe in the
sense that it never DROPs or destructively rewrites anything, but it has two
real gaps against this wave's Definition of Done:

  - No pre-upgrade backup exists before a schema change is applied.
  - The unconditional per-launch retry means there's no reliable signal for
    "a real migration is about to happen" versus "nothing to do" -- every
    launch pays the same (small but nonzero) cost and risk.

This uses SQLite's own `PRAGMA user_version` (a free integer field built into
every SQLite file for exactly this purpose) as an explicit schema-version
marker, so a migration only runs -- and only backs up first -- when the
on-disk version is genuinely behind the version the running code expects.

The backup is taken via `sqlite3.Connection.backup()` (a live, transaction-
consistent copy), never a raw file copy -- a raw `cat`/`cp` of a live SQLite
database can produce a malformed copy if a write is in progress (this
project has hit exactly that failure mode before, on an unrelated live
inspection attempt -- see docs/mobile session history). Copying a database
that is not open for writes at the time is safe either way, but using the
API path removes the need to reason about that.
"""
import os
import shutil
import sqlite3
import time


class MigrationError(Exception):
    pass


def _integrity_ok(db_path: str) -> bool:
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute('PRAGMA integrity_check').fetchone()
        return bool(row) and row[0] == 'ok'
    finally:
        conn.close()


def ensure_schema_version(conn, db_path, target_version, migrate_fn, backup_dir):
    """Bring `conn`'s database up to `target_version`, safely.

    - No-op (fast path) if the database is already at target_version.
    - Otherwise: verify current-file integrity, take a live backup into
      `backup_dir`, run `migrate_fn(conn)` (expected to apply whatever ALTER
      statements are needed -- may itself be defensive/idempotent, that's
      unchanged), then verify integrity again and only then advance
      `PRAGMA user_version`.
    - On any failure during the migrate step, the on-disk database is left
      exactly as the backup captured it (nothing in this function deletes or
      truncates the live file) and `user_version` is NOT advanced, so the
      next launch retries from the same safe starting point. The backup file
      remains on disk as an explicit recovery point either way.
    - If the database's version is AHEAD of `target_version` -- this build
      is OLDER than whatever wrote the database -- refuse outright instead
      of opening it. This is the "rolled back to an older build" case: the
      code doesn't know what the newer schema's columns/constraints mean, so
      the only safe move is to say so and stop, before this function's own
      backup/migrate machinery (built for the opposite direction) touches
      anything. There is no safe automatic path from a newer schema back to
      an older one -- no downgrade, no repair -- so this never attempts one.
      Nothing is written on this path (no backup either): refusing means
      nothing is being changed, so writing a file here would only add a new
      way for an inert path to fail.
    """
    try:
        current = conn.execute('PRAGMA user_version').fetchone()[0]
    except sqlite3.DatabaseError as e:
        raise MigrationError(
            f"Refusing to migrate {db_path}: could not even read its schema version "
            f"BEFORE any migration was attempted ({e}). This does not look like a "
            f"valid SQLite database. Restore from a known-good backup instead."
        )

    if current == target_version:
        return  # already up to date -- most launches take this path

    if current > target_version:
        raise MigrationError(
            f"Refusing to open {db_path}: its schema version ({current}) is NEWER than "
            f"what this build of the application expects ({target_version}). This database "
            f"was created or migrated by a newer version of the application, and this older "
            f"build must not operate on a schema it does not understand -- doing so could "
            f"silently write rows a newer schema's constraints would have refused. There is "
            f"no safe automatic downgrade, so nothing was changed. Install the newer build "
            f"again, or point AURA_APP_DATA at a fresh directory to start over."
        )

    if not _integrity_ok(db_path):
        raise MigrationError(
            f"Refusing to migrate {db_path}: PRAGMA integrity_check failed "
            f"BEFORE any migration was attempted. This database was already "
            f"damaged; migrating it further would not help. Restore from a "
            f"known-good backup instead."
        )

    os.makedirs(backup_dir, exist_ok=True)
    stamp = time.strftime('%Y%m%dT%H%M%S')
    base = os.path.splitext(os.path.basename(db_path))[0]
    backup_path = os.path.join(backup_dir, f'{base}-pre-migration-v{current}-to-v{target_version}-{stamp}.db')

    backup_conn = sqlite3.connect(backup_path)
    try:
        conn.backup(backup_conn)
    finally:
        backup_conn.close()

    if not _integrity_ok(backup_path):
        # The backup itself is unusable -- do not proceed with a migration
        # we couldn't safely capture a rollback point for.
        raise MigrationError(f"Pre-migration backup at {backup_path} failed integrity_check; migration aborted.")

    migrate_fn(conn)
    conn.commit()

    if not _integrity_ok(db_path):
        raise MigrationError(
            f"{db_path} failed PRAGMA integrity_check AFTER migration. "
            f"A pre-migration backup is available at {backup_path} -- restore from it. "
            f"user_version was NOT advanced, so this will not silently repeat as 'already migrated'."
        )

    conn.execute(f'PRAGMA user_version = {int(target_version)}')
    conn.commit()


def prune_old_migration_backups(backup_dir, keep=5):
    """Keep only the `keep` most recent pre-migration backups per product,
    so this doesn't grow unbounded across many small releases. Never called
    automatically from ensure_schema_version -- an explicit, separate step,
    so a backup is never removed as a side effect of the code path that
    might need it."""
    if not os.path.isdir(backup_dir):
        return
    files = sorted(
        (f for f in os.listdir(backup_dir) if f.endswith('.db')),
        key=lambda f: os.path.getmtime(os.path.join(backup_dir, f)),
        reverse=True,
    )
    for stale in files[keep:]:
        try:
            os.remove(os.path.join(backup_dir, stale))
        except OSError:
            pass
