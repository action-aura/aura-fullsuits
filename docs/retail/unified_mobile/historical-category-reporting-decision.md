# Historical Category and Branch Reporting Decision (M5.6.7)

## Category — real schema finding

`sale_items`/`return_items` (`Sales.sq`, `Returns.sq`) carry no
`category_id`/`category_name` snapshot column at all — only
`product_name_at_sale`. There is nothing to migrate additively without a
new schema column, so this decision picks explicitly between the two
options the governing checkpoint allowed:

- **Option A** (not chosen): add an immutable `category_id_at_sale`
  snapshot column to `sale_items`/`return_items`.
- **Option B** (chosen): Category filtering in reports uses the
  product's **current** `category_id`
  (`Reporting.sq`'s `JOIN products p ... p.category_id = :categoryId`).

**Why Option B:** the legacy authority itself has no Category-filtered
report at all (`reporting-authority-audit.md` §3 — no
`report_top_products` or dashboard query filters by category), so there
is no existing behavior to preserve or migrate. Adding a snapshot column
this milestone would be new schema surface with no real caller yet
(`RepositoryBoundaries.kt`'s own discipline: "an empty marker is the
honest boundary" for undesigned shapes) and M5.6 explicitly does not
change `Sales.sq`/`Returns.sq`'s row shape. Option B is honestly
documented, not silently assumed: **old Category-filtered results can
move when a product's Category is reassigned later** — this is a real,
tested, and disclosed behavior, not an oversight.

Proven by `categoryFilterUsesTheProductsCurrentCategoryNotAHistoricalSnapshot`
(`TopProductsAuthorityTest.kt`): filtering top-products by "Drinks"
includes Cola while it is in Drinks; after re-categorizing Cola into
Snacks, the same historical period's "Drinks" filter no longer includes
Cola, even though the underlying sale is unchanged.

If a future milestone needs Category-accurate historical reporting
(e.g. financial/regulatory Category-based analysis), the additive fix is
Option A — add the snapshot column then, backed by a real caller
requirement, not speculatively now.

## Branch — already correct, no decision needed

Unlike Category, `sales.branch_id`/`returns.branch_id` are real,
existing foreign keys captured at transaction time — every reporting
query in `Reporting.sq` already filters by the **sale's own**
`branch_id`, never by "whatever branch happens to be currently
selected" in some caller's UI state. There is no live-join to a
"current branch" concept anywhere in the reporting layer. This was
already proven for Cart/Sale finalization in M5.5.11
(`branch-domain-contract.md`) and is proven again for reporting by
`archivedBranchHistoricalSalesRemainReportableInTheTrend`
(`sales-trend-contract.md`) and
`archivedProductsHistoricalSalesRemainReportable`
(`top-products-contract.md`): historical rows remain attributed to their
real originating Branch/Product regardless of that Branch's or
Product's current active/archived state.
