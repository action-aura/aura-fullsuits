# Reporting Pagination and Range Bounds (M5.7.10)

## Two real bugs found and fixed

Both found by reasoning about `List.take(n)`'s real Kotlin stdlib
contract (`require(n >= 0)`, throws `IllegalArgumentException` for
negative `n`) while auditing every place a caller-supplied `Long` limit
reached a `.take()` call — not found by a failing test first, but fixed
and then proven by a real test, per this initiative's own discipline of
never leaving a found defect unverified.

1. **`SqlDelightReportingRepository.computeTopProductsLocked`** called
   `.take(limit.toInt())` with the caller's raw `limit` — a negative
   `limit` (e.g. a future UI bug passing `-1`) would have thrown
   `IllegalArgumentException`, crashing `getTopProductsByQuantity`/
   `getTopProductsByNetRevenue` instead of returning a safe empty/small
   result.
2. **`SqlDelightDashboardRepository.getDashboard`** called
   `lowStock.take(lowStockPreviewLimit.toInt())` with the same raw
   caller input — same crash risk for the Dashboard's low-stock preview.

## Fix: `ReportingLimits` (`reporting/ReportingLimits.kt`)

```kotlin
object ReportingLimits {
    const val MAX_TOP_PRODUCTS_LIMIT = 500L
    const val MAX_TREND_BUCKET_COUNT = 400
    const val MAX_DASHBOARD_PREVIEW_LIMIT = MAX_TOP_PRODUCTS_LIMIT
}
```

- `getTopProductsByQuantity`/`getTopProductsByNetRevenue`: `limit`
  clamped via `limit.coerceIn(0L, MAX_TOP_PRODUCTS_LIMIT)` before it
  ever reaches `.take()` — a negative request safely becomes zero
  results, an excessive request is capped, never a crash and never an
  unbounded in-memory result.
- `getDashboard`: `lowStockPreviewLimit` clamped the same way via
  `MAX_DASHBOARD_PREVIEW_LIMIT`.
- `getSalesTrend`: **rejected**, not clamped — `require(buckets.size <=
  MAX_TREND_BUCKET_COUNT)` throws before the gate is even acquired, for
  an excessive bucket-count request. Rejection (not silent truncation)
  was chosen here specifically because silently truncating a caller's
  requested trend buckets would produce a trend result the caller did
  not ask for and might not notice was incomplete — a limit clamp is
  safe for "how many ranked results," but silently dropping trend
  buckets could misrepresent a real date range as fully covered when it
  is not. This is a real, deliberate distinction between the two
  strategies the checkpoint itself allowed ("reject or safely
  constrain"), not an inconsistency.

## Real, executed proof

`ReportingBoundsTest.kt` (6/6,
`TEST-com.actionaura.retail.reporting.ReportingBoundsTest.xml`
tests="6" failures="0" errors="0"):

- Negative Top Products limit → empty result, no crash.
- Zero Top Products limit → empty result (not "all results").
- Excessive Top Products limit (999,999,999) against a real 5-product
  dataset → exactly 5 results, no attempt to over-allocate.
- Excessive trend bucket count (`MAX_TREND_BUCKET_COUNT + 1`) →
  `IllegalArgumentException`, thrown before any query runs.
- Exactly `MAX_TREND_BUCKET_COUNT` buckets → accepted, real boundary
  case proven, not just "one over the limit."
- Negative Dashboard preview limits (both Top Products and low-stock) →
  empty previews, full Dashboard composition completes without crashing.

## Range bound (date range itself)

`ReportPeriod`'s own `init` block already rejects
`startInclusive >= endExclusive` structurally (M5.6.2, unchanged) — a
reversed range is already impossible to construct. No additional
MAXIMUM-range cap was added this milestone: real measurement
(`exact-aggregation-performance.md`) shows the full 3-year dataset range
completes in 439-1103ms, well within acceptable bounds, so there is no
real evidence yet that an arbitrarily long date range causes a real
problem worth rejecting. This is a real, evidence-based decision not to
add a speculative cap, matching `reporting-index-decision.md`'s same
discipline for indexes — if a future milestone's real data shows very
long ranges becoming a problem, `ReportingLimits` is the documented,
ready place to add `MAX_REPORT_RANGE_DAYS` then.
