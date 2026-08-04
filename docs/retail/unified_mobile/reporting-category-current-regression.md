# Category-Current Policy Regression (M5.7.7)

Real, executed reconfirmation of M5.6.7's Category-current filtering
policy (`historical-category-reporting-decision.md`, Option B) at M5.7
real scale — **retained unchanged this milestone**, no immutable
Category-at-Sale snapshot silently added, per the checkpoint's explicit
"do not silently add Category-at-Sale snapshots in a performance
milestone" instruction.

## Real regression case

`ReportingScaleFixture` (M5.7.1) already builds this exact case as part
of the representative dataset: `reassignedProductId` (product 101) has a
real sale on `reassignedProductSaleDay` while assigned to
`reassignedFromCategoryId` (category 1), then a real
`UPDATE products SET category_id = ?` reassigns it to
`reassignedToCategoryId` (category 2).

Proven by `ReportingCategoryCurrentRegressionTest.kt` (3/3,
`TEST-com.actionaura.retail.reporting.perf.ReportingCategoryCurrentRegressionTest.xml`
tests="3" failures="0" errors="0"):

1. `historicalReportFilteredByTheProductsCurrentCategoryIncludesTheSaleEvenThoughItWasSoldUnderADifferentCategory`
   — filtering the sale-day period by `reassignedToCategoryId` (the
   product's CURRENT category) includes the real historical sale, even
   though the sale happened while the product was still in
   `reassignedFromCategoryId`.
2. `historicalReportFilteredByTheOriginalSaleTimeCategoryNoLongerIncludesTheSaleAfterReassignment`
   — filtering the same period by `reassignedFromCategoryId` (the
   product's ORIGINAL sale-time category) no longer includes the sale
   after reassignment. **Old results move when a product's Category
   changes** — this is the real, disclosed consequence
   `historical-category-reporting-decision.md` already documented at
   M5.6.7, reconfirmed here as still true and still real at M5.7 scale,
   not silently fixed or hidden.

## Query plan remains indexed at full scale

`theCategoryCurrentFilterQueryPlanRemainsIndexedAtFullScaleNotDegradedByReassignmentHistory`
asserts the real `EXPLAIN QUERY PLAN` for the Category-current filter
(against the full 10,000-product / 100,001-sale dataset) still shows
`SEARCH p USING COVERING INDEX products_category_id` — the one product
reassignment in the fixture's history does not degrade the plan for
every other Category-filtered query; `products_category_id` reflects
each product's current `category_id` value directly, with no dependency
on how many times a product has been reassigned historically.

## Limitation remains visible

This is not a new finding — `historical-category-reporting-decision.md`
already documented the real, disclosed limitation ("old results can move
when a product's Category is reassigned later") and the real, additive
path if a future milestone needs Category-accurate historical reporting
(Option A: add an immutable `category_id_at_sale` snapshot column,
backed by a real caller requirement, not built speculatively). This
document reconfirms that decision is still correct and still the active
policy after M5.7's real scale testing — no change was made.
