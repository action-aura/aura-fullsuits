# Aura Retail Unified Mobile — Milestone 5.6 Test Report

Real, executed evidence only. Every number below comes from a real
Gradle test run and JUnit XML summation (`shared/build/test-results/testDebugUnitTest/*.xml`),
not an estimate.

## Test count progression

| Checkpoint | Shared tests | Delta |
|---|---|---|
| M5.5 baseline cited in the governing checkpoint | 169 | — |
| After Part A (4 mandatory M5.5 follow-ups) | 206 | +37 |
| After M5.6.0-M5.6.2 (audit + aggregation decision + period contract) | 206 | +0 (docs + `ReportPeriodTest` 13 + `SqliteTextMoneyAggregationTest` 6 already counted in the 206) |
| After M5.6.3-M5.6.4 (eligible sales/returns + currency separation) | 215 | +9 |
| After M5.6.5 (sales trend authority) | 221 | +6 |
| After M5.6.6-M5.6.7 (top products + Category/Branch history) | 226 | +5 |
| After M5.6.8-M5.6.9 (Dashboard authority) | 229 | +3 |
| After M5.6.10-M5.6.11 (isolation + write-consistency) | 232 | +3 |
| After M5.6.12 (data quality) | 235 | +3 |
| After M5.6.13 (financial-correctness matrix gaps) | 238 | +3 |
| After M5.6.17 (10K-scale performance baseline) | 239 | +1 |
| After M5.6.18 (authorization integration boundary) | **244** | +5 |

**Net M5.6 contribution: 38 new tests (206 → 244), 0 failures, 0 errors
at every single checkpoint above** — each commit in the sequence was
verified green before the next commit began, not just at the end.

## New test files (this milestone)

| File | Tests | Covers |
|---|---|---|
| `ReportPeriodTest.kt` | 13 | M5.6.2 period/timezone contract |
| `SqliteTextMoneyAggregationTest.kt` | 6 | M5.6.1 SQL type-coercion proof |
| `SqlDelightReportingRepositoryTest.kt` | 9 | M5.6.3/M5.6.4 eligible sales/returns, currency separation |
| `SalesTrendAuthorityTest.kt` | 6 | M5.6.5 sales trend authority |
| `TopProductsAuthorityTest.kt` | 5 | M5.6.6/M5.6.7 top products, Category/Branch history |
| `SqlDelightDashboardRepositoryTest.kt` | 3 | M5.6.8/M5.6.9 Dashboard authority |
| `BusinessBranchIsolationTest.kt` | 2 | M5.6.10 business/branch isolation |
| `ReportConsistencyUnderWritesTest.kt` | 1 | M5.6.11 report consistency under writes (real coroutine race) |
| `ReportingDataQualityTest.kt` | 3 | M5.6.12 data-quality handling |
| `FinancialCorrectnessMatrixTest.kt` | 3 | M5.6.13 remaining financial-correctness cases |
| `ReportingPerformancePrepTest.kt` | 1 | M5.6.17 10K-scale performance baseline |
| `ReportingAccessContextTest.kt` | 5 | M5.6.18 authorization integration boundary |

## Cross-language regression check

`python products/run_all_tests.py retail` (canonical isolated-subprocess
runner, the only supported invocation per `milestone-3-decision.md`'s
own established finding): **194/194 passed, 0 failed**, re-run after
this milestone's Kotlin-only reporting work — zero cross-language
regression, exactly matching the M5.5 baseline count.

```
[PASS] launcher_support_test.py            16 passed
[PASS] retail_backup_restore_test.py       12 passed
[PASS] retail_capability_guard_test.py     11 passed
[PASS] retail_financial_authority_test.py  10 passed
[PASS] retail_import_export_test.py        25 passed
[PASS] retail_localization_test.py         18 passed
[PASS] retail_onboarding_wave0_test.py      8 passed
[PASS] retail_phase7_migration_test.py      7 passed
[PASS] retail_pricing_test.py              26 passed
[PASS] retail_returns_wave0_test.py        10 passed
[PASS] retail_security_test.py             47 passed
[PASS] wave1c_financial_gate_test.py        4 passed
12 file(s) run, 12 passed, 0 failed  ->  194/194
```

## Android build

`:androidApp:assembleDebug` succeeded after every commit in the
sequence above — confirmed by direct Gradle invocation, not inferred.

## Real bugs found and fixed during this milestone

Unlike M5.5, no functional bug was found in the reporting authority
itself during M5.6 — every first-draft implementation passed its
corresponding real test on the first `./gradlew` run. Two real,
non-bug findings shaped the final design instead:

1. **SQLite `SUM()`/`AVG()` type-coercion on TEXT money columns**
   (`SqliteTextMoneyAggregationTest.kt`, M5.6.1): confirmed via
   `typeof()` inspection that SQL aggregation silently produces `REAL`,
   even though the specific tested values (19.99+19.99, 1000×0.1,
   0.1+0.2) showed no visible drift in SQLite's own summation. The
   architectural decision (never trust SQL aggregation for authoritative
   money) was made regardless of the empirically-clean result, because
   the disqualifying factor is the confirmed type-coercion risk, not
   drift in a handful of samples — documented explicitly in
   `exact-report-aggregation-decision.md` to prevent a future reader
   concluding "SQLite SUM is safe because these tests passed."
2. **kotlinx-datetime API mismatch**: `DayOfWeek.isoDayNumber` does not
   exist in the pinned `0.6.1` version — a real, first-guess compile
   error, fixed by using `.ordinal` instead (`MONDAY.ordinal == 0`).

## Baseline preserved

169-test M5.5-checkpoint baseline: preserved and exceeded (244 final).
112-test M5.4 baseline: preserved (subsumed within the above). No
existing test was deleted, weakened, or skipped to reach these numbers.
