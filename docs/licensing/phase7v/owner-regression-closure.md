# Phase 7V — Owner Regression Closure

## Baseline (before toolchain fix)

```
OWNER_ENV=testing .venv/Scripts/python.exe -m pytest -q   (run from owner/)
5 failed, 181 passed in 270.46s
```

Failures, all in `tests/test_backup_restore.py`:
- `test_backup_succeeds_and_records_checksum`
- `test_restore_rejects_tampered_checksum`
- `test_restore_creates_pre_restore_safety_backup`
- `test_restore_actually_recovers_data`
- `test_password_never_appears_in_subprocess_argv`

Root cause: `app.system.backup._pg_bin()` returned the bare string `pg_dump`/`pg_restore` and
relied on `PATH`; this machine's PostgreSQL 17 client tools are installed but not on `PATH`.
Exact error: `app.system.backup.BackupError: [WinError 2] The system cannot find the file
specified`. Not a test-quality problem — a genuine tool-discovery gap. See
`toolchain-closure-report.md` for the fix (`_pg_bin()` now checks `OWNER_PG_BIN_DIR`, then `PATH`,
then `C:/Program Files/PostgreSQL/*/bin`).

## After fix

```
OWNER_ENV=testing .venv/Scripts/python.exe -m pytest -q tests/test_backup_restore.py
7 passed in 14.98s
```

Full suite:

```
OWNER_ENV=testing .venv/Scripts/python.exe -m pytest -q   (run from owner/, real PostgreSQL 17.10)
186 passed in 212.12s (0:03:32)
```

**186/186 passing, 0 failed, 0 skipped**, against real PostgreSQL (not sqlite, not mocked), using
the real `pg_dump.exe`/`pg_restore.exe` binaries discovered by the new fallback logic — no test was
weakened, no assertion removed, no subprocess call replaced with a fake.

This 186 figure includes (already part of the existing suite, re-run in full, not cherry-picked):
- licensing activation tests
- device concurrency tests
- replay tests
- rate-limit tests
- assertion tests
- signing-key rotation tests
- data-boundary tests
- audit-chain tests
- backup/restore (now real, see above)

## What was and was not changed

Changed: `owner/app/system/backup.py::_pg_bin()` — tool discovery only. No change to backup/restore
business logic, no change to what gets backed up, no change to the checksum/pre-restore-safety/
credential-redaction behavior established in Phase 5.

Not changed: nothing else in `owner/`. This was the only Owner-side gap identified in the Phase 7V
baseline.
