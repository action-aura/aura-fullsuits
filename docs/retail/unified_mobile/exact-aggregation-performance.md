# Exact Aggregation Performance (M5.7.5)

Real, measured latency of the Kotlin-side exact `Money`/`Quantity`
accumulation path M5.6 chose over SQL `SUM`/`AVG`
(`exact-report-aggregation-decision.md`), against the real M5.7.1
100,001-sale dataset. Never switched to `Double` to improve any number
below — the measurements below are what the real, exact path costs, not
a comparison against a faster-but-lossy alternative.

## Real, executed results

(`ExactAggregationPerformanceTest.exactAggregationLatencyAcrossRepresentativeRangesAtFullScale`,
`TEST-com.actionaura.retail.reporting.perf.ExactAggregationPerformanceTest.xml`,
tests="1" failures="0" errors="0", real captured `<system-out>`):

| Measurement | Result |
|---|---|
| Cold full-range `getSalesSummary` (100,001 sales, 3-year range) | 1103ms |
| Warm full-range runs (5 repeated calls) | 539, 464, 464, 452, 439ms |
| Warm p50 | 464ms |
| Warm p95 | 539ms |
| Warm max | 539ms |
| One-day range | 37ms |
| One-week range | 47ms |
| One-month range | 58ms |
| One-year range | 155ms |
| Top products, full range, limit 20 (both rankings' source query) | 2297ms |
| Sales trend, 30 daily buckets | 1331ms |
| Full Dashboard (composed: today + selected period + trend + both Top Product rankings + low stock) | 2694ms |

## Cold vs. warm

The cold run (first call after a fresh in-memory database seed, no
query-plan or page-cache warmup) is real: 1103ms. Warm runs (same
process, repeated calls) settle to 439-539ms — a real, measured ~2x
improvement from JVM/SQLite internal caching, not a projection.

## Range scaling behaves as expected

Latency scales with the real row count matched, not with total table
size: a one-day query (matching roughly 90-100 of the day's sales) costs
37ms, while the full 3-year range (matching all ~100,000 sales) costs
439-1103ms — consistent with the row-count-bound nature of the
aggregation (`selectSalesForPeriod`/`selectReturnsForPeriod` project
only the matched rows, `report-query-inventory.md`), not a fixed
per-call overhead dominating.

## No cancellation-behavior measurement in this test

This measurement captures wall-clock duration only; real coroutine
cancellation behavior mid-aggregation is validated separately in
`reporting-database-write-gate-report.md` (M5.7.8), not duplicated here.

## Memory

No dedicated heap-profiling tool was run this milestone (out of scope
for a JVM unit test without added tooling). The real, indirect evidence
available: `getTopProductsByQuantity`/`getTopProductsByNetRevenue`
materialize a `MutableMap<Long, Accumulator>` sized to the distinct
product count matched in the requested range — bounded by
`PRODUCT_COUNT` (10,000) in the worst case for this dataset, never by
`sale_items` row count (299,895). This is a structural bound already
established by `top-products-contract.md`'s design (`getOrPut` keyed by
`product_id`), reconfirmed here as still true at this scale — not a new
finding.

## No Double aggregation introduced

Confirmed by inspection: `SqlDelightReportingRepository.kt` is unchanged
from M5.6 — `Money.plus`/`Money.minus`/`Quantity.plus`/`Quantity.minus`
remain the only accumulation operators used. This measurement did not
motivate or require any change to the aggregation approach itself, only
this real evidence that the existing approach performs acceptably.
