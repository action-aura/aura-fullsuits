# Reporting Scale Dataset (M5.7.1)

Real, deterministic, seeded dataset (`ReportingScaleFixture.kt`) shared
by every M5.7 query-plan/performance/concurrency test. Generation is
bulk/direct (one transaction, arithmetic ID tracking instead of
`lastInsertRowId()` round trips) so fixture setup does not dominate test
time — every downstream report query in M5.7 still executes against the
real `RetailDatabase` schema and a real SQLite engine (`JdbcSqliteDriver`,
in-memory).

## Real, executed row counts

(`ReportingScaleDatasetTest.fixtureProducesExactlyTheClaimedRowCountsAndCharacteristics`,
`TEST-com.actionaura.retail.reporting.perf.ReportingScaleDatasetTest.xml`,
tests="1" failures="0" errors="0", real captured `<system-out>`):

```
products=10000        categories=30 (archived=5)   branches=6 (archived=1)
sales=100001          sale_items=299895
returns=9995          return_items=19991
archivedProductCount=20
archivedBranchSaleCount=50        (real historical sales attributed to the archived branch)
laterPeriodReturnCount=1927       (returns confirmed >40 days after their originating sale)
reassignedProductId=101  from=category 1  to=category 2
tiedProductIds=[10000, 9999, 9998]  tiedDay=1704931200000  (2024-01-11 UTC)
dateRange=[1704067200000, 1798761600000)  (2024-01-01 UTC through 2027-01-01 UTC, 3 years)
generationElapsedMs=15858
```

## Checkpoint requirement coverage

| Required characteristic | Real value in this dataset |
|---|---|
| 1 business | `companyId = 1` throughout |
| Multiple active branches | 5 active (`branches[0..4]`) |
| ≥1 archived branch with historical Sales | 1 archived branch, 50 real historical sales attributed to it |
| Multiple active Categories | 25 active |
| Archived Categories | 5 archived |
| 10,000 Products | Exactly 10,000 |
| Active and archived Products | 9,980 active, 20 archived |
| ≥100,000 finalized Sales | 100,001 |
| Sale lines materially larger than Sales | 299,895 (≈3.0×) |
| Partial Returns | Half of all returns are single-line (10.00 refund vs the sale's 30.00 total) |
| Full Returns | The other half refund the full 30.00 / all 3 lines |
| Returns finalized in a later period than their Sale | 1,927 real returns dated +45 days after their originating sale, crossing month/year boundaries |
| Multiple years of dates | 2024-01-01 through 2026-12-31 (3 full years) |
| Daily/weekly/monthly distribution | Sales cycle through every day in the 3-year range (`saleIndex % totalDays`), so daily/weekly/monthly buckets all have real, varying population |
| Multiple currency cases where the schema permits | **Not applicable** — the real schema (`reporting-definition-contract.md`) has exactly one implicit currency per company, no per-row currency column; this is a real, previously-documented schema fact, not skipped |
| Malformed financial rows in a SEPARATE fixture | `ReportingScaleFixture.seedDataQualityFixture` — a distinct, small, deliberately-corrupted fixture dated 2027-06-01, outside this dataset's 2024-2027 range, never mixed into the 100K-row dataset above |
| Tied Top Product metrics | 3 products (`tiedProductIds`), identical quantity (2) and revenue (20.00), isolated on one specific day (2024-01-11) so a dedicated tie-break query-plan/ranking test can target them cleanly |
| Product Category reassignment history | `reassignedProductId` (101) has a real sale under category 1, then a real `UPDATE products SET category_id = 2` — the exact shape `historical-category-reporting-decision.md`'s Option-B regression test needs |
| Current and historical Branch combinations | Active branches keep receiving new sales throughout generation; the archived branch's 50 sales are frozen history |

## Environment

```
runtime = Microsoft 17.0.19  (OpenJDK/Temurin build, java.vendor+java.version)
os      = Windows 11 10.0
JDBC/SQLite = app.cash.sqldelight:sqlite-driver:2.3.2 (bundles xerial sqlite-jdbc transitively)
SQLDelight  = 2.3.2 (same version pinned across runtime/coroutines-extensions/android-driver/sqlite-driver)
driver      = JdbcSqliteDriver.IN_MEMORY (no on-disk file, real byte-size measurement not applicable here — a real on-disk size will be captured separately if M5.7.12's Android driver validation warrants it)
```

## Generation duration

**15,858ms** (real, measured `System.currentTimeMillis()` delta) for the
full ~435,000-row transaction (10,000 products + 30 categories + 6
branches + 100,001 sales + 299,895 sale_items + 9,995 returns + 19,991
return_items), on the machine/environment above. This is a real, one-time
fixture-generation cost paid once per test that calls
`ReportingScaleFixture.seed(...)` — every M5.7 test using this fixture
will report its own generation time separately in its own evidence,
since each test gets a fresh in-memory database (no cross-test dataset
reuse, matching the isolation discipline every prior milestone's tests
already use).

## Warm vs. cold conditions

This run is a real "cold" JVM-process condition (Gradle test worker JVM
started fresh for this test class). M5.7.5's exact-aggregation
performance measurement separately captures cold-vs-warm timing for the
report QUERIES themselves (not fixture generation), per that
sub-milestone's own requirement.
