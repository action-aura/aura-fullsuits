# Phase 1 test report

## Full suite, end of wave

```
python products/run_all_tests.py
65 file(s) run, 65 passed, 0 failed
```

Up from the pre-wave baseline of 46 files / 564 tests
(`phase1-scope-and-baseline.md`) — 19 new test files added, zero existing
files broken, at every checkpoint along the way (re-run after every
wiring change to Retail/Clinic, not just once at the end).

## New test files this wave

| Area | Files | Notes |
|---|---:|---|
| `commercial_runtime/einvoicing/tests/` | 14 | Unit tests for every shared module |
| `commercial_runtime/tests/einvoicing_migration_test.py` | 1 | Migration mechanism |
| `products/retail/tests/retail_einvoicing_regression_test.py` | 1 | Flag-OFF safety net, written before feature code |
| `products/retail/tests/retail_einvoicing_test.py` | 1 | Real end-to-end integration |
| `products/clinic/tests/clinic_einvoicing_regression_test.py` | 1 | Flag-OFF safety net |
| `products/clinic/tests/clinic_einvoicing_test.py` | 1 | Real end-to-end integration |

## Coverage

```
pytest commercial_runtime/einvoicing/tests/ --cov=commercial_runtime.einvoicing --cov-report=term-missing
169 passed
TOTAL   2314 stmts   60 miss   97% cover
```

Target was 80% (`testing.md`); actual is 97%. Lowest-covered individual
module: `routes.py` at 89% (uncovered lines are mostly defensive
`finally`/error branches not reachable without a real network failure
injected at the Flask test-client layer).

## What was verified with a real running artifact, not just unit tests

- **Real Windows DPAPI encryption** — `test_credentials.py` runs against
  actual `CryptProtectData`/`CryptUnprotectData` on this machine, not a
  mock.
- **Real 20-thread concurrency race** —
  `test_sequence.py::test_concurrent_allocations_never_duplicate` and
  `test_outbox_repository.py::test_concurrent_claim_never_double_claims_the_same_row`
  both spin real OS threads against a real SQLite file.
- **Real PyInstaller build** — Retail's `.spec` was built end-to-end
  (`python -m PyInstaller products/retail/packaging/aura_retail.spec`),
  the resulting `AuraRetail.exe` was launched, and `/api/health` and
  `/api/einvoicing/status` were confirmed responding correctly (401 on the
  latter — no session — proving the blueprint registered and is routing,
  not silently 404ing or 500ing from a missing hidden import).
- **Real end-to-end product flow** — both integration suites drive the
  actual Flask app (not a bare test blueprint): create a sale/invoice,
  enable the feature, submit via the real worker against `MockProvider`,
  confirm `CLEARED` + a real QR image byte stream, confirm the reconciliation
  sweep recovers a simulated lost enqueue, confirm pre-enablement history is
  never backfilled.

## Regression guarantee

`retail_einvoicing_regression_test.py` / `clinic_einvoicing_regression_test.py`
pin, with the feature at its default (OFF):

- `sorted(response.keys())` for sale/return/dashboard (Retail) and
  invoice-create/invoice-get/payment (Clinic) — frozen literal lists
  captured from the real running app before any feature code existed.
- `PRAGMA table_info(...)` for every table this feature touches — proves
  no `ALTER` ran on an existing table.
- `SELECT COUNT(*) FROM einvoice_outbox` stays 0 through a full
  sale+return / invoice+payment cycle.
- `threading.enumerate()` is unchanged after boot + a real transaction —
  no background thread exists when the feature has never been enabled.

All four re-run and green after every subsequent wiring step in this wave.
