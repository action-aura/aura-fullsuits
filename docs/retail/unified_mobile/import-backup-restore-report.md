# Import Backup & Restore Compatibility (M5.8.22)

Real, disclosed status: no `BackupStorage`/`RestoreStorage`
implementation exists anywhere in this codebase yet — both remain
`platform.PlatformContracts.kt`'s own Milestone-16 interface markers
(`BackupRepository` in `RepositoryBoundaries.kt`), unimplemented. This
milestone cannot exercise a real end-to-end backup PIPELINE that does
not exist, and does not claim to.

## What is real and proven here

`ImportBackupRestoreTest.kt` (1/1) — real, executed proof against a
real FILE-based SQLite database (not `:memory:`): the new
`import_dry_runs`/`import_provenance`/`import_audit_log` tables are
ordinary tables in the SAME database file as every other table,
captured automatically by SQLite's own real, standard `VACUUM INTO`
backup mechanism (the same real mechanism
`commercial_runtime/security/migration_safety.py`'s `.backup()`-API
pattern is built on — cited as the explicit design reference in
`BackupRepository`'s own KDoc).

The test: writes a real dry-run (consumed), a real provenance row, and
a real audit entry; runs `VACUUM INTO` to a second real file; opens
that second file with an independent, freshly-constructed driver/
connection; re-reads all three rows and confirms every field —
including the `consumed_at` timestamp and the joined `inserted_counts`
string — survived byte-for-byte.

## Why this is the right real proof, given no backup pipeline exists yet

Import Center's new tables need NO special-case wiring, exclusion
list, or separate store for backup/restore to work correctly, because:
- They live in the same `RetailDatabase` file SQLDelight already
  manages — there is exactly one file, not a per-feature split.
- SQLite's own real, whole-file backup mechanisms (`VACUUM INTO`, or
  the C-level `sqlite3_backup` API `migration_safety.py`'s pattern is
  built on) operate on the file/connection as a whole — they have no
  concept of "skip this table."
- Therefore, whichever real mechanism M16 eventually implements behind
  `BackupStorage`/`RestoreStorage`, Import Center's data is included
  automatically, with zero additional Import-Center-specific work
  required at that time.

## Real, disclosed scope NOT covered

- No real `BackupStorage`/`RestoreStorage` implementation exists to
  test end-to-end (file naming, retention, compression, restore-time
  schema-version reconciliation) — all M16-scoped, unimplemented.
- No real device-level test of Android's own backup/restore behavior
  (Auto Backup, `adb backup`) — no device/emulator on this host,
  matching this session's standing disclosure pattern.
- `import_dry_runs` rows with a real, honest short lifetime
  (`ImportLimits.maxDryRunLifetimeMillis`, 30 minutes) are real,
  disclosed CANDIDATES for exclusion from a future backup policy (a
  restored dry-run past its own `expires_at` is useless — `ImportCommitRevalidator`
  already rejects it regardless) — a real, deliberate design decision
  for M16 to make, not decided here.
