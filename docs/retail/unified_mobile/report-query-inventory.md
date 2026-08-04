# Report Query Inventory (M5.7.0)

Real inventory of every SQLDelight query and Kotlin aggregation path the
M5.6 reporting authority uses, read directly from
`Reporting.sq`/`SqlDelightReportingRepository.kt`/`SqlDelightDashboardRepository.kt`
(all unchanged since M5.6 closeout).

## Base-table indexes that exist today (from `Sales.sq`/`Returns.sq`)

```
sales:        sales_company_id(company_id), sales_customer_id(customer_id), sales_branch_id(branch_id)
sale_items:   sale_items_sale_id(sale_id), sale_items_product_id(product_id)
returns:      returns_company_id(company_id), returns_sale_id(sale_id)
return_items: return_items_return_id(return_id), return_items_product_id(product_id)
products:     products_company_id(company_id), products_category_id(category_id),
              products_company_sku(company_id, sku) UNIQUE,
              products_company_barcode(company_id, barcode) UNIQUE PARTIAL,
              products_barcode_lookup(company_id, barcode),
              products_normalized_name(company_id, normalized_name)
```

**No composite index exists on `sales(company_id, status, created_at)` or
`returns(company_id, status, created_at)` today** — every reporting
query's real filter shape is `company_id = ? AND status = ? AND
created_at >= ? AND created_at < ?`, but the only matching index is the
single-column `sales_company_id`/`returns_company_id`. Whether this is
an `INDEX_GAP` at real scale is exactly what M5.7.2/M5.7.4 must measure
with real `EXPLAIN QUERY PLAN` output, not assumed here.

## Query-by-query inventory

### `selectSalesForPeriod` (`Reporting.sq`)

- **Source**: `Reporting.sq`, used by `getSalesSummary`/`getSalesTrend` (`SqlDelightReportingRepository.computeSummaryLocked`)
- **Base table**: `sales`
- **Joins**: none
- **Filters**: `company_id = :companyId AND status = :status AND created_at >= :startInclusive AND created_at < :endExclusive AND (:branchId IS NULL OR branch_id = :branchId)`
- **Ordering**: `ORDER BY created_at`
- **Existing index candidate**: `sales_company_id` (single-column only)
- **Expected row count at scale**: bounded by the requested date range × eligible-status share of `sales` for one company (M5.7.1's dataset: ~100,000 total sales for 1 business, so a full-history query touches the whole table; a single-day query should touch a small slice if date filtering is efficient)
- **Kotlin exact aggregation**: `Money.parse(row.total)` accumulated via `Money.plus`, malformed rows routed to `ReportingDataQualityIssue.MalformedMoney` (`eligible-sales-and-returns-contract.md`)
- **Memory**: materializes the full matched row list via `executeAsList()` before accumulating — bounded by the query's own date/branch filter, not paginated
- **Concurrency**: executed inside `gate.mutex.withLock` (`reporting-concurrency-report.md`)
- **Result limit**: none (date range is the only bound)
- **Historical semantics**: none — every matched row is read as-is

### `selectReturnsForPeriod` (`Reporting.sq`)

- Same shape as `selectSalesForPeriod` against `returns`, feeding `confirmedReturns` via `Money.parse(row.refund_amount)`.
- **Existing index candidate**: `returns_company_id` (single-column only)

### `selectSaleItemsForPeriod` (`Reporting.sq`)

- **Source**: `Reporting.sq`, used by `getTopProductsByQuantity`/`getTopProductsByNetRevenue` (`SqlDelightReportingRepository.computeTopProductsLocked`)
- **Base table**: `sale_items`
- **Joins**: `JOIN sales s ON si.sale_id = s.id`, `JOIN products p ON si.product_id = p.id`
- **Filters**: `s.company_id = :companyId AND s.status = :status AND s.created_at >= :startInclusive AND s.created_at < :endExclusive AND (:branchId IS NULL OR s.branch_id = :branchId) AND (:categoryId IS NULL OR p.category_id = :categoryId)`
- **Ordering**: none (SQLDelight query has no `ORDER BY`; ordering happens in Kotlin after accumulation)
- **Existing index candidates**: `sales_company_id` (for the `sales` join/filter side), `sale_items_sale_id` (join key), `products_category_id` (Category filter)
- **Expected row count**: 3 lines/sale in M5.7.1's dataset → up to ~3× the matched-sales row count for the same date range
- **Kotlin exact aggregation**: per-`product_id` `Quantity`/`Money` accumulation via `getOrPut`, malformed rows excluded and surfaced (`top-products-contract.md`)
- **Memory**: full matched row list via `executeAsList()`, plus an in-memory `MutableMap<Long, Accumulator>` sized to the distinct product count in range
- **Concurrency**: `gate.mutex.withLock`
- **Result limit**: none on the query itself — `limit: Long` truncates only after full accumulation and sorting in Kotlin (`top-products-contract.md`'s deterministic tie-break sort), not pushed down to SQL
- **Historical semantics**: Category filter uses the product's CURRENT `category_id` via the `JOIN products p`, not a historical snapshot (`historical-category-reporting-decision.md`, Option B)

### `selectReturnItemsForPeriod` (`Reporting.sq`)

- Same shape as `selectSaleItemsForPeriod` against `return_items`/`returns`, feeding the same per-product accumulator via subtraction (returns-adjusted net).
- **Existing index candidates**: `returns_company_id`, `return_items_return_id`, `products_category_id`

## Kotlin-layer call inventory

| Caller | SQLDelight queries used | Notes |
|---|---|---|
| `getSalesSummary` (daily/weekly/monthly/custom via caller-supplied `ReportPeriod`) | `selectSalesForPeriod` + `selectReturnsForPeriod` | Both period-boundary math and status filter come from `ReportPeriodFactory`/`ELIGIBLE_STATUS`, not from this query |
| `getSalesTrend` (one call per bucket, buckets from `ReportPeriodFactory.dailyBuckets`/`weeklyBuckets`/`monthlyBuckets`/caller-built custom list) | `selectSalesForPeriod` + `selectReturnsForPeriod`, once per bucket | **N calls, not 1** — a 30-bucket trend issues 60 real queries (M5.7.6 must audit whether this is acceptable or an N+1 shape) |
| `getTopProductsByQuantity` | `selectSaleItemsForPeriod` + `selectReturnItemsForPeriod` | Ranks in Kotlin, truncates to `limit` after sorting |
| `getTopProductsByNetRevenue` | Same two queries, ranked by `netRevenue` instead |
| `SqlDelightDashboardRepository.getDashboard` | Calls `getSalesSummary` ×2 (today + selected period), `getSalesTrend` (N buckets), `getTopProductsByQuantity`, `getTopProductsByNetRevenue`, plus `ProductRepository.listLowStock` | Composition only, no duplicated SQL (`dashboard-authority-contract.md`) — but the real total query count for one dashboard render is `2 + 2N + 2 + 2 + 1` where N = trend bucket count |
| Data-quality detection (malformed Money/Quantity) | No dedicated query — detected inline while iterating the same `selectSalesForPeriod`/`selectReturnsForPeriod`/`selectSaleItemsForPeriod`/`selectReturnItemsForPeriod` rows via `Money.parse`/`Quantity.zeroOrMore` | Not a separate round trip |

## What M5.7 must still measure (not yet measured as of this inventory)

- Real `EXPLAIN QUERY PLAN` output for every filter shape above at
  100,000-sale scale (M5.7.2/M5.7.3).
- Whether the single-column `company_id`/`branch_id`/`sale_id` indexes
  are sufficient for a real query plan, or whether a composite
  `(company_id, status, created_at)` index is justified by measured
  evidence (M5.7.4).
- The real per-bucket-query-count cost identified above for
  `getSalesTrend`/`getDashboard` (M5.7.6).
