# Phase 7V — Product Test-Runner Closure (Part D)

## Root cause (already correctly diagnosed in Wave 1B, re-confirmed here)

`products/run_all_tests.py`, built for AUDIT-010, exists precisely to solve the cross-file pytest
pollution the Phase 7V spec describes. Root cause (see
`docs/release/wave1b/pytest-isolation-correction.md` for the original full write-up, re-verified
still accurate): `commercial_runtime/identity/registry_db.py`'s `DB_PATH`, the products'
`database/schema.py` `BASE_DIR`/`SUBSYS_DIR`, and `config.py`'s `DATABASE_DIR` are all computed
once at **module import time** from `AURA_APP_DATA`. A single `pytest` process importing multiple
test files only lets the first file's environment value take effect (`sys.modules` caching), so
every later file's fresh temp directory is ignored, and the first file's teardown deletes a
directory later files still depend on — `no such table: users` and similar. This is not a
production defect: a real process only ever imports these modules once per run, exactly matching
the module-level-constant assumption; reworking them to re-resolve on every call would be
production-code surgery purely to satisfy a test harness, which is explicitly out of scope.

## Chosen isolation method (unchanged, still correct)

`products/run_all_tests.py` runs each test file as its own `pytest` **subprocess**
(`subprocess.run([sys.executable, '-m', 'pytest', str(path), ...])`) — a fresh interpreter and
empty `sys.modules` per file, identical to how a real deployed process only ever imports these
modules once. Results are aggregated into one pass/fail report and one process exit code.

## Coverage check against Phase 7V's file list

`SUITES` already includes `retail`, `clinic`, `commercial_runtime`, and `licensing_contracts`
(`commercial_runtime/licensing_contracts/tests`), and `discover()` globs both `*_test.py` and
`test_*.py` within each directory. Confirmed by inspection that every Phase 7 test file added
since Wave 1B is already picked up with **zero runner changes needed**:

- `products/clinic/tests/clinic_capability_guard_test.py`, `clinic_phase7_migration_test.py`
- `products/retail/tests/retail_capability_guard_test.py`, `retail_phase7_migration_test.py`
- `commercial_runtime/tests/migration_safety_test.py`
- all 22 files under `commercial_runtime/licensing_contracts/tests/`

(`owner/tests/` is intentionally not part of this runner — Owner has its own separate,
already-supported full-suite command, `pytest` from `owner/`, covered in
`owner-regression-closure.md`.)

## Exact supported command

```
cd aura-fullsuits
.venv/Scripts/python.exe products/run_all_tests.py
```

## Result

```
46 file(s) run, 46 passed, 0 failed
585 tests total, 0 failures
```

This run was performed **after** the Part G `config.py` TLS-verification fix landed, so it also
serves as the regression check for that change — no test suite (including
`retail_security_test.py` and `retail_financial_authority_test.py`) was affected by it.

## Why this is not hiding failures

- The runner does not catch, filter, retry, or reinterpret any subprocess result — a non-zero
  `pytest` exit code for any file is surfaced verbatim as `[FAIL]` and the aggregate exit code is
  non-zero.
- Every one of the 585 tests actually executed inside its own real subprocess against a real
  SQLite database in a real fresh temp directory — nothing here is mocked at the test-selection or
  reporting layer.
- The known pollution only ever appeared under `pytest products/clinic/tests` (a raw multi-file
  invocation this runner deliberately avoids); it was never a hidden failure inside any individual
  file, and per-file runs have been 100% green throughout Phase 7 and Phase 7V.
