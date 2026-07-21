# Phase 5 -- Owner Database Backup and Recovery (Part Y)

Separate from Retail/Clinic's local SQLite backup mechanism (`commercial_runtime/backup/service.py`) -- this backs up **Owner's own PostgreSQL database** only, via native `pg_dump`/`pg_restore` (`owner/app/system/backup.py`).

## Mechanism
- **Backup**: `pg_dump --format=custom` to a timestamped file (`aura-owner-backup-<label>-<UTC-timestamp>.dump`) under `OWNER_BACKUP_DIR`. A `DatabaseBackupRecord` row is written with `owner_app_version`, `schema_revision` (the live `alembic_version`), `sha256` checksum, `size_bytes`, and `status` (`SUCCESS`/`FAILED`), whichever the outcome. Never committed to Git (`.gitignore` excludes `var/backups/` and `*.dump`).
- **Restore**: verifies the backup file's SHA-256 against the stored checksum first -- a modified or corrupted file is rejected (`RestoreError: Checksum mismatch`) before anything is touched. Then takes a **pre-restore safety backup** of the current live state. Then runs `pg_restore --clean --if-exists --no-owner --dbname <live DB>`.
- **Access control**: `system.backup`/`system.restore` permissions (Super Admin only by default seed), plus `@require_recent_auth` on both routes; the restore route additionally hard-checks `actor.is_super_admin` in code, not just the permission grant, per the spec's explicit "access restricted to Super Admin" instruction.
- **Audit**: every backup attempt (success or failure) and every restore attempt (rejected, failed, or succeeded) writes a complete audit entry via `app/audit/services.py::record()`.

## A real bug found and fixed during this phase's own testing
Initial implementation opened `pg_restore --dbname` against the *same live database* the app's own SQLAlchemy session was still connected to, with an open (uncommitted) transaction. `pg_restore --clean`'s `DROP TABLE` statements request `ACCESS EXCLUSIVE` locks, which queued indefinitely behind the app's own idle-in-transaction connection's weaker lock -- a real deadlock, reproduced via `owner/tests/test_backup_restore.py` (the test hung until its 300-second subprocess timeout). Fixed by committing and releasing the app's own connection (`db_session.commit(); db_session.remove(); engine.dispose()`) immediately before invoking `pg_restore`. Re-run confirmed the fix: the same restore that previously hung now completes in ~2 seconds. This is disclosed here specifically because it is exactly the kind of defect a "we tested backup and it works" claim would otherwise silently miss.

A second, related fix: `str(engine.url)` masks the database password as `***` by design (SQLAlchemy security default) -- the original code passed this masked URL to `pg_dump`/`pg_restore`, which then failed authentication. Fixed via `engine.url.render_as_string(hide_password=False)`.

## Test evidence
`owner/tests/test_backup_restore.py`, 6/6 passing: backup succeeds and records a real checksum; backup failure (missing `pg_dump` binary) is recorded with an error detail, not silently swallowed; restore rejects a tampered/mismatched checksum before touching data; restore's pre-restore safety backup is provably created (audit trail + on-disk file, since the live DB's own bookkeeping table is itself subject to being rolled back by the restore -- see the test's own docstring for why a naive row-count assertion would be wrong); **restore actually recovers deleted data** (`test_restore_actually_recovers_data`: create a customer, back up, delete the customer, restore, confirm the customer is back); the backup route is permission- and CSRF-gated for a non-Super-Admin role.

## Manual verification this phase
`information_schema.table_constraints` queried directly after a real restore confirmed all 79 foreign key constraints present and correct post-restore (not just "table exists" -- full referential integrity intact).

## Not built this phase (explicitly out of scope, per spec)
Cloud backup, scheduled/automatic backups, public backup download, any UI for browsing backup file contents.
