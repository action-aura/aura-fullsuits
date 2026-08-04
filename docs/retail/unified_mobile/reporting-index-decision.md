# Reporting Index Decision (M5.7.4)

## Decision: **no new index added this milestone**

Real, measured evidence (below) shows the existing single-column indexes
(`sales_company_id`, `sales_branch_id`, `returns_company_id`,
`sale_items_sale_id`, `return_items_return_id`, `products_category_id`)
are sufficient to keep every real reporting query in bounded, acceptable
time at 100,001-sale/299,895-sale_item/9,995-return scale
(`reporting-query-plan-report.md`, `exact-aggregation-performance.md`).
No `SCAN <table>` (full table scan) occurred in any of the 17 captured
plans. Per this milestone's own priority order (correctness >
transaction consistency > isolation > bounded resource usage > query
performance) and the checkpoint's explicit caution against speculative
indexing ("do not add one index per possible filter combination... add
only indexes justified by measured plans"), no migration is added.

## Candidate index considered and rejected: `(company_id, created_at)` / `(branch_id, created_at)` on `sales`/`returns`

**Real finding that motivated considering this**: every `ORDER BY
created_at` query (`selectSalesForPeriod`, `selectReturnsForPeriod`)
shows `USE TEMP B-TREE FOR ORDER BY` — the existing indexes narrow the
row set but do not deliver rows pre-sorted, so SQLite sorts the matched
rows after fetching.

**Real measurement that rejected adding it**
(`ExactAggregationPerformanceTest.exactAggregationLatencyAcrossRepresentativeRangesAtFullScale`,
`TEST-com.actionaura.retail.reporting.perf.ExactAggregationPerformanceTest.xml`,
1/1, real captured `<system-out>`):

```
cold full-range getSalesSummary (100,001 sales, 3-year range): 1103ms
warm full-range runs: [539, 464, 464, 452, 439]ms  p50=464ms  p95=539ms  max=539ms
one-day range: 37ms   one-week: 47ms   one-month: 58ms   one-year: 155ms
top products (full range, limit 20): 2297ms
sales trend (30 daily buckets): 1331ms
full dashboard (composed): 2694ms
```

The temp-B-tree-sort cost is already included in every one of these
numbers (it is a real step inside `selectSalesForPeriod`/
`selectReturnsForPeriod`'s execution) — and even the worst case (full
3-year range, ~100K rows, cold JVM) completes in ~1.1 seconds, with warm
runs consistently under 550ms. This is well within acceptable bounds for
a mobile reporting screen at this representative scale. Adding a
composite index would trade real write-amplification (every Sale/Return
insert would maintain one more index) and real migration/backup-size
cost for a latency improvement that real measurement shows is not
needed.

## Candidate index considered and rejected: `returns_branch_id`

**Real finding**: `returns` has no Branch-scoped index — a
Branch-filtered Return query is indexed on `company_id` only, with
`branch_id` applied as a residual filter.

**Real reasoning against adding it**: at this dataset's scale (9,995
returns for one company), the residual filter runs over a row count
smaller than the `sales` table's own worst-case query, which was already
measured at 464-1103ms. Returns-by-Branch is real but strictly cheaper
than the largest already-measured query. No standalone timing was
captured for this specific shape (it was not separately isolated in
`ExactAggregationPerformanceTest`), which is itself a disclosed
limitation of this milestone's measurement, not a claim of proof — if a
future milestone's real data shows this shape becoming a bottleneck
(e.g. a business with far more returns than sales, an unusual but real
possible profile), `returns_branch_id` is the documented, ready
candidate to add then, backed by real measurement at that time.

## What WAS already correctly indexed (no gap found)

- `products_category_id` — used as a real `COVERING INDEX` for every
  Category-filtered Top Products query, no additional table lookup
  needed.
- `sale_items_sale_id`/`return_items_return_id` — the join keys back to
  `sales`/`returns`, both real, both used.
- `sales_branch_id` — used directly for every single-Branch report,
  including the archived-Branch historical case.

## Barcode-index lesson retained

M5.5.16's own real finding (a partial index SQLite could not prove
satisfied a query's `WHERE` condition, requiring a dedicated
non-partial index) was explicitly re-checked against: none of the
indexes relied on by the 17 real plans in this milestone are partial
indexes (`products_category_id`, `sales_company_id`, `sales_branch_id`,
`returns_company_id`, `sale_items_sale_id`, `return_items_return_id` are
all plain, unconditional indexes) — so that specific failure mode does
not apply here. Recorded as a real, checked-against precedent, not
assumed irrelevant.

## Migration status

No SQLDelight schema migration was added this milestone. `Sales.sq`,
`Returns.sq`, and `Catalog.sq` are unchanged from M5.6.
