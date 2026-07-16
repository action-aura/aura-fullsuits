# Test Coverage and Evidence

## Executive finding: the "116 / 95 passing tests" claim is PROVEN, but the combined `pytest` run is not reliable as a regression command

Both prior-phase test-count claims (Retail 116, Clinic 95) are **PROVEN** — reproduced
in this environment. But a real, reproducible cross-file test-pollution defect was
found while reproducing them: running all of a product's test files together in one
`pytest` invocation causes dozens of spurious failures that do not occur when each
file is run in its own process. This is documented as issue `AUDIT-001` in the
master defect registry (category: test infrastructure, not a customer-facing bug).

## Retail — exact commands and results

Environment: `C:\Users\Dell\Desktop\AuraEnterprise\AuraEnterprise\.venv\Scripts\python.exe -m pytest`, Python 3.11.9, pytest 9.1.1, run from `aura-fullsuits/`.

| Command | Result |
|---|---|
| `pytest products/retail/tests -q` (all 4 files, one process) | **38 failed, 78 passed** in 55.31s |
| `pytest products/retail/tests/retail_import_export_test.py -q` (isolated) | 25 passed in 26.41s |
| `pytest products/retail/tests/retail_localization_test.py -q` (isolated) | 18 passed in 4.64s |
| `pytest products/retail/tests/retail_pricing_test.py -q` (isolated) | 26 passed in 23.97s |
| `pytest products/retail/tests/retail_security_test.py -q` (isolated) | 47 passed in 64.09s |
| **Sum, isolated runs** | **116 passed, 0 failed** — matches the prior claim exactly |

Root cause of the combined-run failures (reproduced and traced, not guessed):
`commercial_runtime/identity/registry_db.py` computes `DB_PATH` as a **module-level
constant** at import time (`_app_data = os.environ.get('AURA_APP_DATA') or ...`,
line ~26). Each test file sets its own `AURA_APP_DATA` to a fresh temp directory at
its own module level and expects `registry_db` to honor it — but because Python
caches imported modules, `registry_db` (and the Flask `app` module, also imported
at each test file's top level) is only ever truly re-executed on its *first*
import within the pytest process. Every subsequent test file's `os.environ` change
has no effect on the already-frozen `DB_PATH`. When the first file's `teardown_module`
deletes its temp directory (`shutil.rmtree`), later files' calls to `registry_conn()`
hit a now-deleted (then silently recreated-empty) SQLite file whose tables were
never (re-)created against the *current* test's expectations in every combination
of ordering (in one observed run, `retail_security_test.py` failed 100% of its own
tests with `sqlite3.OperationalError: no such table: users`).

Same defect exists in Clinic (`commercial_runtime/identity/registry_db.py` is
shared): `pytest products/clinic/tests -q` → **64 failed, 31 passed** combined, vs.
**95 passed, 0 failed** when the six files are run individually (see below).

This is a **test-harness defect, not a proven production defect** — a real deployed
process sets `AURA_APP_DATA` exactly once, before any import, and never changes it
mid-process, so the module-level-caching behavior that breaks multi-file pytest runs
never triggers in a real install. But it does mean: (a) `pytest products/retail/tests`
/`pytest products/clinic/tests` as a single command is **not a trustworthy regression
check** today — a developer who runs it and sees failures cannot tell real regressions
apart from this pollution without already knowing about it, and (b) the "116/95 tests
pass" claim, while true, was previously undocumented as being order/isolation-
dependent, which the audit spec explicitly warned against assuming.

## Clinic — exact commands and results

| Command | Result |
|---|---|
| `pytest products/clinic/tests -q` (all 6 files, one process) | **64 failed, 31 passed** in 52.44s |
| `pytest products/clinic/tests/clinic_independence_test.py -q` | 5 passed in 0.12s |
| `pytest products/clinic/tests/clinic_localization_test.py -q` | 13 passed in 3.52s |
| `pytest products/clinic/tests/clinic_onboarding_auth_test.py -q` | 15 passed in 5.17s |
| `pytest products/clinic/tests/clinic_privacy_test.py -q` | 9 passed in 5.63s |
| `pytest products/clinic/tests/clinic_rbac_test.py -q` | 24 passed in 55.59s |
| `pytest products/clinic/tests/clinic_workflow_test.py -q` | 29 passed in 51.79s |
| **Sum, isolated runs** | **95 passed, 0 failed** — matches the prior claim exactly |

## Android

`testDebugUnitTest` for both `android/aura-retail` and `android/aura-clinic`:
**0 tests exist** (`NO-SOURCE` — no `app/src/test` or `app/src/androidTest`
directories in either project). This was already documented honestly in Phase 4
(`docs/android/android-{retail,clinic}-build-report.md`) and is re-confirmed here,
not re-run (no source changed).

## Test category breakdown (by file, what each actually covers)

| File | Category | What it covers |
|---|---|---|
| `retail_pricing_test.py` | Financial / unit + integration | `core/retail/pricing.py` formula unit tests (both tax modes) + HTTP-level sale/return/dashboard end-to-end tests |
| `retail_security_test.py` | Security / integration | Password hashing, legacy-hash migration, account lockout, session handling, cross-company isolation, demo-wipe authorization |
| `retail_localization_test.py` | Localization / integration | Language persistence endpoint, DB write, invalid-language handling |
| `retail_import_export_test.py` | Functional / integration | CSV import validation and execution |
| `clinic_rbac_test.py` | Authorization / integration | Role enforcement across clinic routes |
| `clinic_privacy_test.py` | Privacy / integration | Cross-tenant data exposure checks (the IDOR-fix regression suite) |
| `clinic_workflow_test.py` | Functional + financial / integration | Patients, visits, prescriptions, doctors, lab expenses, **invoices/payments/dashboard stats** |
| `clinic_onboarding_auth_test.py` | Auth / integration | First-run admin creation, login |
| `clinic_localization_test.py` | Localization / integration | Same shape as Retail's |
| `clinic_independence_test.py` | Repo-independence / static | Confirms no import-time dependency on the original monolith |

No file in either suite is a true "unit test" in isolation from Flask/SQLite except
`retail_pricing_test.py`'s Part A (`core/retail/pricing.py` called directly, no app
boot) — every other test boots a real Flask app against a real (temp-file) SQLite
database and exercises HTTP routes. This is closer to integration testing than unit
testing throughout; there is no mocked-database layer anywhere in either suite.

## Coverage map — critical feature to test-coverage level

| Feature | Coverage |
|---|---|
| Retail tax/discount formula (`core/retail/pricing.py`) | Automated (unit-level, both modes, both `calculate_line`/`calculate_invoice`) |
| Retail sale creation via `/api/sub/retail/sales` (server-side trust behavior) | **No coverage** — no test asserts that the server rejects/recomputes a client-submitted total that disagrees with `unit_price × quantity × tax/discount`. Tests only submit self-consistent, already-correct totals (see `03-retail-financial-audit.md`). |
| Retail return/refund creation (`/api/sub/retail/returns`) | Partial — `retail_pricing_test.py` covers full/partial/multi-line returns computed from CORRECT client-submitted figures. **No test covers**: return without a matching original sale, return quantity exceeding what was sold, duplicate/repeated return of the same sale (see `03`). |
| Android Retail POS tax/discount submission | **No coverage at all** — zero Android unit/instrumented tests exist; the client-side gap found in this audit (`03`) was found by source reading, not by a failing test. |
| Clinic invoice creation (server-side subtotal/discount/tax computation) | Automated (`clinic_workflow_test.py::test_invoice_creation_totals_and_discount_before_tax`, `test_invoice_discount_cannot_exceed_subtotal`) |
| Clinic payment recording — overpayment/negative/duplicate-submission | **No coverage** — `clinic_workflow_test.py::test_payment_recording_and_invoice_status_transitions` exists but (per source read in `04`) does not exercise overpayment, negative amount, or duplicate-submission cases. |
| Cross-tenant isolation (Clinic) | Automated (`clinic_privacy_test.py`, `clinic_rbac_test.py`) — this is the one area with genuinely strong regression coverage, a direct result of the Phase 3 IDOR-fix work. |
| Cross-tenant isolation (Retail) | Partial — `retail_security_test.py::test_cross_company_data_isolation` exists (1 test) vs. Clinic's dedicated multi-test suite for the same concern. |
| Backup/restore | No coverage (no backup/restore feature exists to test — see `18`) |
| Android on-device behavior (any) | No coverage (no test harness, no device) |
| Performance at scale | No coverage (no benchmark harness in the repo) |

## Untested critical workflows (explicit list)

1. Concurrent/rapid duplicate sale submissions on Retail beyond the single idempotency-key happy path.
2. Application crash / process kill mid-transaction, for either product (no test simulates this; `03`/`06` assess it by code inspection of transaction boundaries only).
3. Android end-to-end sale flow (cart → checkout → server persistence) — never exercised by any automated test on any platform; this is exactly where this audit's biggest finding (Android's tax/discount omission) lives, and it was invisible to every existing test.
4. Schema upgrade/downgrade (no migration mechanism exists to test).
