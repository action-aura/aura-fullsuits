# Wave 1B — AUDIT-010: Pytest Cross-File Isolation Correction

## Root cause (precisely identified, not guessed)
Three separate modules each compute a filesystem path **once, at Python module import time**, from the `AURA_APP_DATA` environment variable:

- `commercial_runtime/identity/registry_db.py`: `DB_PATH = os.path.join(_db_dir, 'registry.db')` — a module-level constant.
- `products/clinic/backend/database/schema.py` (and Retail's equivalent): `BASE_DIR`/`SUBSYS_DIR` — module-level constants.
- `products/{retail,clinic}/backend/config.py`: `DATABASE_DIR` — a module-level constant.

Every test file follows the same pattern: create a fresh `tempfile.mkdtemp()`, `os.environ.update(AURA_APP_DATA=str(DATA))`, **then** `import app` (which transitively imports the three modules above), then register a `teardown_module()` that `shutil.rmtree()`s its own temp directory.

This works correctly when a test file runs alone (confirmed: every one of the 8 Clinic files and 9 Retail files passes 100% in isolation). It breaks when multiple files run in one `pytest` invocation, because Python caches imported modules in `sys.modules` for the life of the process: **only the first test file's `AURA_APP_DATA` value actually takes effect** for `registry_db.DB_PATH`/`schema.SUBSYS_DIR`/`config.DATABASE_DIR` — every subsequent file's environment-variable change is silently ignored, since those modules' top-level code never re-runs. When the *first* file's `teardown_module()` then deletes its own temp directory, every *other* file in the same pytest process is still unknowingly pointed at that now-deleted path, producing `sqlite3.OperationalError: no such table: users` and similar.

Verified via `git stash`: the identical failure count (23 failed / 30 passed for `clinic_workflow_test.py` + `clinic_rbac_test.py` run together) reproduces with this wave's unrelated `clinic_api.py` change fully reverted — confirming the pollution is pre-existing and orthogonal to any specific test's content.

## Why this is not a production defect
A real running instance of either product — the Windows `.exe`, or the embedded Android backend — only ever imports `registry_db`/`schema`/`config` **once per process**, exactly matching what these modules assume. The module-level caching that breaks a multi-file pytest run is completely safe and correct in every real deployment scenario. Reworking these modules to re-resolve `AURA_APP_DATA` on every call would be architecture surgery motivated purely by test-harness convenience — exactly what this wave was instructed not to do ("do not alter production behavior merely to make bad tests pass").

## The fix: process-level isolation, matching real usage
`products/run_all_tests.py` (new) runs each test file as its own `pytest` **subprocess** (`subprocess.run([sys.executable, '-m', 'pytest', str(path), ...])`), giving each file a genuinely fresh Python interpreter and empty `sys.modules` — identical to how a real process only ever imports these modules once. Results are aggregated into one pass/fail report.

```
python products/run_all_tests.py                    # everything (Retail + Clinic)
python products/run_all_tests.py retail              # Retail only
python products/run_all_tests.py clinic               # Clinic only
```

## Result
```
python products/run_all_tests.py
17 file(s) run, 17 passed, 0 failed
```
- Retail: 9 files, 171 tests, all passing.
- Clinic: 8 files, 108 tests, all passing.
- **Combined: 279 tests, 0 failures** — matches this wave's stated pre-existing baseline exactly.
- `commercial_runtime/` has no separate test directory; its code is exercised indirectly through every product test file that imports it (which is every file above).

## Status
**AUDIT-010: RESOLVED.** Root cause precisely identified and explained (not a mystery, not "unexplained"); confirmed to be a test-harness artifact with zero production impact; a deterministic, supported single-command test runner now exists and produces 279/279 passing with zero cross-file pollution. Production code (`registry_db.py`, `schema.py`, `config.py`) was intentionally left unchanged, per instruction, since the module-level caching pattern is correct and safe for real single-process deployments.
