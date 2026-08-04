# Top Products Authority Contract (M5.6.6)

Real, executed contract for
`SqlDelightReportingRepository.getTopProductsByQuantity`/
`getTopProductsByNetRevenue`, proven by
`SqlDelightReportingRepositoryTest.kt`'s two top-products cases plus
`TopProductsAuthorityTest.kt` (5/5,
`TEST-com.actionaura.retail.reporting.TopProductsAuthorityTest.xml`
tests="5" failures="0" errors="0").

## Formulas

```
net_quantity = eligible sold quantity (sale_items) - eligible returned quantity (return_items)
net_revenue  = eligible sold line_total (sale_items) - eligible returned line_total (return_items)
```

Both accumulate per `product_id` via real Kotlin `Quantity.plus/minus`
and `Money.plus/minus` over `selectSaleItemsForPeriod`/
`selectReturnItemsForPeriod` (never SQL `SUM()`). A product with a
return in the requested period but no sale in that same period still
gets an accumulator entry (seeded from the return row's own
`product_name_at_sale`) — proven by
`aProductReturnedInALaterPeriodThanItWasSoldStillAppearsWithACorrectNegativeNetInThatLaterPeriod`
(`SqlDelightReportingRepositoryTest.kt`): a product sold in January and
fully returned in February shows `net_revenue = Money.of(-50.00)` in
February's top-products, not an absent entry.

## Never rank gross when reporting net

Both rankings sort exclusively on the returns-adjusted `net_quantity`/
`net_revenue` — there is no code path that ranks by the un-adjusted
sold-only total.

## Deterministic tie-break

```
1. metric (net_quantity or net_revenue) descending
2. product display name ascending
3. product id ascending
```

Implemented via `compareByDescending<TopProductMetric> { metric
}.thenBy { productName }.thenBy { productId }`. Proven by
`tieOnMetricBreaksByProductNameThenById`: two products both with
`net_quantity = 5` come back ordered `Apple`, `Zebra` — alphabetical, not
insertion or database-id order.

## Bounded limit

`limit: Long` truncates the sorted result — proven by
`limitBoundsTheResultToTheTopNByMetric` (5 products seeded, `limit = 2`
returns exactly the top 2 by quantity).

## Currency separation

Quantity ranking has no currency dependency and may combine products
across the whole scope (quantity has no currency). Revenue ranking is
computed entirely within the single company `base_currency`
(`reporting-definition-contract.md`'s currency-separation discipline) —
there is no code path that sums `line_total` values from two different
currencies, since the schema has exactly one implicit currency per
company today.

## Category filter (M5.6.7)

`ReportScope.categoryId` filters via the product's **current**
`category_id` — see `historical-category-reporting-decision.md` for the
full Option-A-vs-Option-B decision record and its real, disclosed
consequence (old results can move when a product is re-categorized).

## Historical Product display and archived-Product reporting

Every metric's `productName` comes from the immutable
`product_name_at_sale`/`ri.product_name_at_sale` snapshot column, never
a live join to `products.name` — proven by
`renamedProductDisplaysTheImmutableHistoricalSnapshotNameNotTheCurrentName`:
renaming a product after the sale still shows the old name in that
period's top-products.

Deactivating (`setActive(..., false)`) a Product does not remove its
historical sales from the ranking — proven by
`archivedProductsHistoricalSalesRemainReportable`.

## Deferred authorization

Same structural note as `sales-trend-contract.md`: `ReportScope`'s
`companyId`/`branchId`/`categoryId` are trusted inputs; real
authorization remains `DEFERRED_TO_MILESTONES_7_TO_10`
(`reporting-authorization-integration-boundary.md`).
