# Reporting Performance Baseline (M5.6.17)

Real, executed baseline timing, NOT a final performance gate. Real
`EXPLAIN QUERY PLAN` index-usage validation belongs to M5.7
(`reporting-query-plan-report.md`, not yet started per the governing
checkpoint's explicit "do not begin M5.7" instruction) — this milestone
only proves the reporting authority completes in bounded time at
representative scale and records a real number to compare M5.7's
(post-indexing, if any changes are needed) numbers against.

## Scale

- 10,000 Products across 20 Categories
- 5 Branches
- 2,000 Sales, each with 3 `sale_items` rows (6,000 total)
- 200 Returns (every 10th sale), each with 1 `return_items` row
- All rows spread across a 30-day window

## Real, executed results

(`ReportingPerformancePrepTest.kt`,
`TEST-com.actionaura.retail.reporting.ReportingPerformancePrepTest.xml`,
tests="1" failures="0" errors="0", captured `<system-out>`):

| Operation | Elapsed |
|---|---|
| Seed all rows (one transaction) | 2355ms |
| `getSalesSummary` (30-day range) | 271ms |
| `getSalesTrend` (30 daily buckets) | 171ms |
| Top products, both rankings (limit 20 each) | 505ms |
| `getDashboard` (composes all of the above + low-stock) | 1024ms |

Every reporting call individually completes in roughly 200-1000ms at
this scale on an in-memory SQLite JDBC driver (no disk I/O), well under
the test's generous 10-second bound — this is the real machine-recorded
number, not an estimate.

## What this does and does not prove

**Proves:** the reporting authority does not have an obvious
quadratic-blowup or unbounded-query defect at 10,000 Products / several
thousand Sale/Return rows — the Kotlin-side accumulation approach chosen
in `exact-report-aggregation-decision.md` (never SQL `SUM`/`AVG`, but
still an indexed row-projection query per call) remains practical at
this scale.

**Does not prove:** that every query actually uses its intended index
(that is `EXPLAIN QUERY PLAN` work, M5.7's job specifically —
`product-inventory-query-plan-report.md` already established that
discipline for Product/Inventory, and `reporting-query-plan-report.md`
will do the same for `Reporting.sq`'s four queries). Does not prove
real-device (non-JDBC, on-disk SQLite via `AndroidSqliteDriver`)
performance, which M5.7/later milestones must validate separately.

## No performance PASS claimed

Per the governing checkpoint's explicit instruction, this milestone
records a baseline only — it does not claim a final performance PASS.
