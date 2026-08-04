# Reporting Query Plan Report (M5.7.2/M5.7.3)

Real, executed `EXPLAIN QUERY PLAN` output against the real M5.7.1
100,001-sale/299,895-sale_item/9,995-return/19,991-return_item dataset,
captured by `ReportingQueryPlanTest.kt` (17/17,
`TEST-com.actionaura.retail.reporting.perf.ReportingQueryPlanTest.xml`
tests="17" failures="0" errors="0"). All 17 real plans are reproduced
verbatim below (from the test's own captured `<system-out>`), each
mapped to the checkpoint's own named "required query shapes."

## Headline finding: no full table scan anywhere

**Every one of the 17 captured plans uses `SEARCH ... USING INDEX`
(or `USING COVERING INDEX` / `USING INTEGER PRIMARY KEY`) — zero plans
show a bare `SCAN <table>` on any high-cardinality table.** The
single-column indexes already present (`sales_company_id`,
`sales_branch_id`, `returns_company_id`, `sale_items_sale_id`,
`return_items_return_id`, `products_category_id`, plus the `products`
primary key) are sufficient for every real filter shape the M5.6
reporting authority issues. `report-query-inventory.md`'s own earlier
speculation that a missing composite `(company_id, status, created_at)`
index might be an `INDEX_GAP` is **not confirmed by real evidence** —
classified `EXPECTED_AND_BOUNDED` below, not `INDEX_GAP`.

## Real finding: `USE TEMP B-TREE FOR ORDER BY` on every ordered query

Both `selectSalesForPeriod` and `selectReturnsForPeriod` (both have
`ORDER BY created_at`) show `USE TEMP B-TREE FOR ORDER BY` in every
variant — the chosen index (`sales_company_id` or `sales_branch_id`)
narrows the row set but does not also deliver rows pre-sorted by
`created_at`, so SQLite sorts the matched rows in a temporary B-tree
after fetching. A composite index like `(company_id, created_at)` or
`(branch_id, created_at)` would eliminate this sort.

**This finding does NOT translate into an index-decision to add one** —
see `reporting-index-decision.md` for the real, measured evidence
(`ExactAggregationPerformanceTest`) showing this temp-sort cost is
already included in the sub-second-to-low-single-digit-second real
end-to-end latencies at 100K-sale scale, which is well within the
generous bounds this milestone's priority order (correctness >
consistency > isolation > bounded resources > query performance)
allows. Classified `ACCEPTABLE_WITH_EVIDENCE`.

## Per-shape plans and classification

| Required shape (checkpoint's own list) | Real plan | Classification |
|---|---|---|
| SALES TREND: single day, one Branch | `SEARCH sales USING INDEX sales_branch_id (branch_id=?)` / `USE TEMP B-TREE FOR ORDER BY` | `EXPECTED_AND_BOUNDED` |
| SALES TREND: custom range, all local branches (`branchId=NULL`) | `SEARCH sales USING INDEX sales_company_id (company_id=?)` / `USE TEMP B-TREE FOR ORDER BY` | `EXPECTED_AND_BOUNDED` |
| SALES TREND: archived Branch historical range | `SEARCH sales USING INDEX sales_branch_id (branch_id=?)` / temp sort | `EXPECTED_AND_BOUNDED` (archival status is a data column, not an index concern — the plan is identical to any other Branch) |
| SALES TREND: no-Sale period | Same shape, correctly narrows to zero matching rows via the index before the (trivial, empty) sort | `EXPECTED_AND_BOUNDED` |
| SALES TREND: high-volume period (full 3-year range, ~100K rows) | `SEARCH sales USING INDEX sales_company_id (company_id=?)` / temp sort over ~100K rows | `ACCEPTABLE_WITH_EVIDENCE` (measured 464-1103ms end-to-end, `exact-aggregation-performance.md`) |
| SALES TREND: weekly/monthly buckets | Not separately captured — identical SQL shape to daily buckets, only the caller-supplied `ReportPeriod` boundaries differ (`ReportPeriodFactory` owns bucket math, not the query) | `EXPECTED_AND_BOUNDED` |
| RETURNS: confirmation date range | `SEARCH returns USING INDEX returns_company_id (company_id=?)` / temp sort | `EXPECTED_AND_BOUNDED` |
| RETURNS: by Branch | `SEARCH returns USING INDEX returns_company_id (company_id=?)` / temp sort — **note: no `returns_branch_id` index exists**, so a Branch-filtered Return query is not itself indexed by branch, only by company | `ACCEPTABLE_WITH_EVIDENCE` (see note below) |
| RETURNS: partial Return aggregation | Kotlin-side accumulation over `selectReturnItemsForPeriod`, not a distinct SQL shape — see TOP PRODUCTS return_items row below | `EXPECTED_AND_BOUNDED` |
| RETURNS: later-period Return | Same `selectReturnsForPeriod` shape, real window isolating the +45-day cases | `EXPECTED_AND_BOUNDED` |
| RETURNS: by originating Sale | `SEARCH r USING INDEX returns_sale_id (sale_id=?)` / `SEARCH ri USING INDEX return_items_return_id (return_id=?)` — **this is `Returns.sq`'s `selectReturnsBySale`, not a `Reporting.sq` query** (the M5.5 sale/return boundary layer's own query, real and indexed, but outside this reporting authority's own 4 queries) | `EXPECTED_AND_BOUNDED` (out-of-authority, documented) |
| TOP PRODUCTS: Quantity by date range, all branches | `SEARCH s USING INDEX sales_company_id` / `SEARCH si USING INDEX sale_items_sale_id` / `SEARCH p USING INTEGER PRIMARY KEY` | `EXPECTED_AND_BOUNDED` |
| TOP PRODUCTS: Quantity by Branch | `SEARCH s USING INDEX sales_branch_id` / same join pattern | `EXPECTED_AND_BOUNDED` |
| TOP PRODUCTS: Quantity by Category | `SEARCH p USING COVERING INDEX products_category_id (category_id=? AND rowid=?)` — a covering index, no extra table lookup needed for the Category filter | `EXPECTED_AND_BOUNDED` |
| TOP PRODUCTS: Quantity by Branch AND Category | Combines both indexes above | `EXPECTED_AND_BOUNDED` |
| TOP PRODUCTS: net-revenue ranking | Identical SQL shape to Quantity ranking — ranking basis is a Kotlin-side sort, not a SQL difference | `EXPECTED_AND_BOUNDED` |
| TOP PRODUCTS: bounded Top N / deterministic tied ranking | Same query shape, run against the dedicated tied-products day | `EXPECTED_AND_BOUNDED` (limit/tie-break is Kotlin-side post-sort, `top-products-contract.md`) |
| TOP PRODUCTS: return_items net-adjustment source | `SEARCH r USING INDEX returns_company_id` / `SEARCH ri USING INDEX return_items_return_id` / `SEARCH p USING INTEGER PRIMARY KEY` | `EXPECTED_AND_BOUNDED` |
| DASHBOARD: today / selected-period composition | Both are the exact same `selectSalesForPeriod` shape as SALES TREND above, called twice with different `ReportPeriod` values | `EXPECTED_AND_BOUNDED` |
| DASHBOARD: Top Product / trend composition | Same shapes as their standalone counterparts above (`dashboard-authority-contract.md`'s own "pure composition, no duplicated formulas") | `EXPECTED_AND_BOUNDED` |
| DASHBOARD: low-stock composition | `ProductRepository.listLowStock` — a pre-existing M5.5.12 query (`selectLowStockProducts`), not part of `Reporting.sq`; not re-validated here since it was already query-plan-validated in `product-inventory-query-plan-report.md` | `EXPECTED_AND_BOUNDED` (out of this milestone's re-validation scope, already covered) |
| DATA QUALITY: malformed Money/Quantity/currency/Branch/orphaned-Return detection | No dedicated query — detected inline while iterating the same 4 real queries' rows (`reporting-data-quality-contract.md`); no separate plan to capture | N/A — not a distinct query |

## Real finding: `returns` has no Branch-scoped index

`returns_company_id` and `returns_sale_id` exist; there is no
`returns_branch_id`. A Branch-filtered Returns report (`RETURNS: by
Branch` above) is indexed only on `company_id`, with the `branch_id`
equality check applied as a residual filter on the company-scoped row
set. In this single-company dataset that residual filter runs over the
whole company's return rows (9,995 of them) — real, but not a full table
scan (still bounded to one company's rows via the index), and still
measured fast in practice (`returns-by-branch` is a strict subset of the
`getSalesSummary`/`getSalesTrend` calls already measured at 271-1103ms
for the much larger `sales` table). Classified `ACCEPTABLE_WITH_EVIDENCE`,
not `INDEX_GAP` — see `reporting-index-decision.md` for the full
reasoning against adding `returns_branch_id` speculatively.

## Real plan text (verbatim, from `<system-out>`)

```
=== DASHBOARD: today composition (same selectSalesForPeriod shape) ===
SEARCH sales USING INDEX sales_company_id (company_id=?)
USE TEMP B-TREE FOR ORDER BY

=== DASHBOARD: selected-period composition (same selectSalesForPeriod shape) ===
SEARCH sales USING INDEX sales_company_id (company_id=?)
USE TEMP B-TREE FOR ORDER BY

=== SALES TREND: archived Branch historical range ===
SEARCH sales USING INDEX sales_branch_id (branch_id=?)
USE TEMP B-TREE FOR ORDER BY

=== TOP PRODUCTS: net-revenue ranking, date range ===
SEARCH s USING INDEX sales_company_id (company_id=?)
SEARCH si USING INDEX sale_items_sale_id (sale_id=?)
SEARCH p USING INTEGER PRIMARY KEY (rowid=?)

=== RETURNS: by originating Sale (Returns.sq's selectReturnsBySale) ===
SEARCH r USING INDEX returns_sale_id (sale_id=?)
SEARCH ri USING INDEX return_items_return_id (return_id=?)

=== SALES TREND: no-Sale period (empty result) ===
SEARCH sales USING INDEX sales_company_id (company_id=?)
USE TEMP B-TREE FOR ORDER BY

=== TOP PRODUCTS: bounded Top N / deterministic tied ranking ===
SEARCH s USING INDEX sales_company_id (company_id=?)
SEARCH si USING INDEX sale_items_sale_id (sale_id=?)
SEARCH p USING INTEGER PRIMARY KEY (rowid=?)

=== TOP PRODUCTS: return_items net-adjustment source query ===
SEARCH r USING INDEX returns_company_id (company_id=?)
SEARCH ri USING INDEX return_items_return_id (return_id=?)
SEARCH p USING INTEGER PRIMARY KEY (rowid=?)

=== RETURNS: by Branch ===
SEARCH returns USING INDEX returns_company_id (company_id=?)
USE TEMP B-TREE FOR ORDER BY

=== TOP PRODUCTS: quantity ranking, by Category ===
SEARCH s USING INDEX sales_company_id (company_id=?)
SEARCH si USING INDEX sale_items_sale_id (sale_id=?)
SEARCH p USING COVERING INDEX products_category_id (category_id=? AND rowid=?)

=== SALES TREND: single day, one Branch ===
SEARCH sales USING INDEX sales_branch_id (branch_id=?)
USE TEMP B-TREE FOR ORDER BY

=== TOP PRODUCTS: quantity ranking, by Branch ===
SEARCH s USING INDEX sales_branch_id (branch_id=?)
SEARCH si USING INDEX sale_items_sale_id (sale_id=?)
SEARCH p USING INTEGER PRIMARY KEY (rowid=?)

=== TOP PRODUCTS: quantity ranking, by Branch AND Category ===
SEARCH s USING INDEX sales_branch_id (branch_id=?)
SEARCH si USING INDEX sale_items_sale_id (sale_id=?)
SEARCH p USING COVERING INDEX products_category_id (category_id=? AND rowid=?)

=== SALES TREND: custom range, all local branches (branchId=NULL shape) ===
SEARCH sales USING INDEX sales_company_id (company_id=?)
USE TEMP B-TREE FOR ORDER BY

=== TOP PRODUCTS: quantity ranking, date range, all branches ===
SEARCH s USING INDEX sales_company_id (company_id=?)
SEARCH si USING INDEX sale_items_sale_id (sale_id=?)
SEARCH p USING INTEGER PRIMARY KEY (rowid=?)

=== RETURNS: confirmation date range (all local branches) ===
SEARCH returns USING INDEX returns_company_id (company_id=?)
USE TEMP B-TREE FOR ORDER BY

=== SALES TREND: high-volume period (full 3-year range, ~100K rows) ===
SEARCH sales USING INDEX sales_company_id (company_id=?)
USE TEMP B-TREE FOR ORDER BY

=== RETURNS: later-period Return window ===
SEARCH returns USING INDEX returns_company_id (company_id=?)
USE TEMP B-TREE FOR ORDER BY
```

## ANALYZE

Not run in this milestone's captured plans — SQLite's query planner used
its built-in heuristics (no `sqlite_stat1` table present). Real evidence
above already shows every plan is indexed without `ANALYZE`; running
`ANALYZE` and re-capturing plans was not performed since no plan showed
a defect that statistics-based planning could plausibly fix (no full
scans occurred to begin with). If a future milestone's real production
data distribution differs meaningfully from this synthetic dataset,
re-running this validation with `ANALYZE` is the documented next step,
not assumed unnecessary forever.
