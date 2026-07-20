# Wave 1B — Schema Migration & Data-Loss Prevention Report (Part K)

## Databases audited

| Product | Database file | Schema version marker | Migration history |
|---|---|---|---|
| Retail | `retail.db` | `RETAIL_SCHEMA_VERSION = 1` | None yet — no `ALTER TABLE` has ever been needed. |
| Clinic | `clinic.db` | `CLINIC_SCHEMA_VERSION = 1` | 5 additive `ALTER TABLE ... ADD COLUMN` statements + 1 partial unique index, accumulated across Phase 3/Wave 0/Wave 1A. |
| Both | `registry.db` (`commercial_runtime`) | not versioned this wave — schema has been stable since Phase 3, no migration history to protect | — |

## What existed before this wave
Every table is created with `CREATE TABLE IF NOT EXISTS` (idempotent, non-destructive by construction — never touched). Clinic's `ALTER TABLE` statements ran **unconditionally on every single app launch**, wrapped in a blanket `try/except: pass` to swallow the expected "column already exists" error on every launch after the first. This is safe in the narrow sense that it never drops or destructively rewrites anything, but had two real gaps against this wave's requirements:
- No backup was ever taken before a schema change was applied.
- The unconditional retry-every-launch pattern meant there was no reliable signal distinguishing "a real migration is about to run" from "nothing to do," and any error other than "column already exists" (e.g. a genuinely corrupt database) would be silently swallowed by the same blanket `except`.

Foreign keys (`PRAGMA foreign_keys=ON`) were already correctly enabled per-connection in both products (Wave 0, AUDIT-016) — unchanged this wave.

## What was built: `commercial_runtime/security/migration_safety.py`
Uses SQLite's own `PRAGMA user_version` (a free integer field built into every SQLite file) as an explicit version marker, replacing the always-retry pattern:
- **Fast path** (already at target version — every normal launch after the first): reads one `PRAGMA`, returns immediately. No backup, no `ALTER` attempts.
- **Migration path** (on-disk version behind the code's expected version): verifies the *current* file passes `PRAGMA integrity_check` **before** touching anything (refuses to migrate an already-damaged database — migrating it further wouldn't help); takes a live, transaction-consistent backup via `sqlite3.Connection.backup()` (never a raw file copy — a raw copy of a live SQLite file can produce a malformed result if a write is in progress, a failure mode this project has hit before during an unrelated live-database inspection); verifies the *backup* also passes integrity_check; runs the real migration function; verifies the *post-migration* file passes integrity_check; only then advances `PRAGMA user_version`.
- **On any failure**: the exception propagates (not swallowed), `user_version` is left unchanged (so the next launch retries from the same safe starting point, not skipped as "already done"), and the pre-migration backup remains on disk as an explicit, verified-intact recovery point.

Wired into both `products/retail/backend/database/schema.py` (`RETAIL_SCHEMA_VERSION = 1`, currently a no-op migration function — real infrastructure ready for the first actual future Retail schema change, not a placeholder) and `products/clinic/backend/database/schema.py` (`CLINIC_SCHEMA_VERSION = 1`, wraps the existing 5-`ALTER`+index migration).

## Tests
`commercial_runtime/tests/migration_safety_test.py` (new — this is the first file in what was previously an empty `commercial_runtime/tests/` directory), 5/5 passing:
- First migration runs, creates exactly one backup, applies the schema change.
- Already-current-version is a true no-op: migrate function never called, no backup directory even created.
- **Simulated migration failure** (the Definition of Done's explicit requirement): a migration that partially applies then raises confirms the exception propagates, `user_version` does not advance, and the pre-migration backup is fully intact and queryable — the actual recovery path.
- An already-corrupt "database" (not valid SQLite at all) is refused up front with a clear error, before any write is attempted.
- Backup pruning (`prune_old_migration_backups`, keeps the N most recent, never called automatically as a side effect of the migration path itself) keeps exactly the newest files.

## Real-world confirmation, not just synthetic tests
Rebuilt the Windows Clinic executable and launched it against the **genuine pre-existing app data directory** from this session's own installer testing (`%LOCALAPPDATA%\AuraClinic`, containing a real "Windows Test Patient" record, at `user_version=0` since it predates this wave's change):
1. First launch: migration ran for real, created exactly one backup file (`clinic-pre-migration-v0-to-v1-<timestamp>.db`, byte-identical size to the original), patient data confirmed fully intact via a live API query afterward.
2. Second launch: confirmed via filesystem check that **no second backup was created** — the fast no-op path engaged correctly on real, non-synthetic data.

## Result
PASS. Migration failure cannot silently reset the database (proven via a real simulated failure, not just asserted). Pre-upgrade backup behavior exists and was verified both in isolated tests and against a real running instance. Foreign-key and integrity checks remain active throughout.
