# Reporting Definition Contract (M5.6.4, M5.6.9)

Real, implemented shape of the shared reporting authority. Dependency
direction, as required by the governing checkpoint:

```
Shared UI (not built this milestone)
  -> Shared reporting ViewModel/state (not built this milestone)
  -> Shared reporting use cases (not built this milestone)
  -> ReportingRepository (interface, reporting/ReportingRepository.kt)
  -> SqlDelightReportingRepository (reporting/SqlDelightReportingRepository.kt)
  -> SQLDelight read models (Reporting.sq: selectSalesForPeriod,
     selectReturnsForPeriod, selectSaleItemsForPeriod, selectReturnItemsForPeriod)
```

No use case or ViewModel layer exists yet — out of scope for M5.6 per
the governing checkpoint ("do not begin full Compose screen
implementation"). This milestone stops at the repository.

## Read models (`reporting/ReportingModels.kt`)

All immutable `data class`/`sealed class`. No SQLDelight-generated row
type and no platform date type escapes the `reporting` package — every
query result is converted to `Money`/`Quantity`/`Long` (epoch millis) at
the repository boundary before being wrapped in a model.

- `ReportScope(companyId, branchId?, categoryId?)` — captured once per
  call, never re-read mid-computation (see `sales-trend-contract.md` for
  why this matters for trend buckets).
- `CurrencySalesSummary` — one currency's `grossSales`/`confirmedReturns`/`netSales`/`transactionCount`.
- `SalesSummary` — `Map<CurrencyCode, CurrencySalesSummary>` plus `dataQualityIssues`.
- `SalesTrendBucket`/`SalesTrendResult` — M5.6.5.
- `TopProductMetric`/`TopProductsResult` — M5.6.6.
- `DashboardSnapshot` — M5.6.8 (not yet implemented as of this commit).
- `ReportingDataQualityIssue` — `MalformedMoney`/`MalformedQuantity`, M5.6.12.

## Currency separation (M5.6.4)

Real schema finding (`Sales.sq`, `Returns.sq`, `Catalog.sq` grep):
**no table in the unified schema carries a per-row currency column.**
Every `sales`/`returns`/`sale_items`/`return_items`/`products` money
value is implicitly denominated in the company's single configured
`base_currency` setting (`SettingsRepository.getSetting(companyId,
"base_currency")`, default `"USD"` if unset — matching
`reporting-authority-audit.md` §6's own finding that the legacy
authority has exactly one implicit currency and no test proves currency
separation because none exists to prove).

`SqlDelightReportingRepository` still keys every result by
`CurrencyCode` (`Map<CurrencyCode, CurrencySalesSummary>` in
`SalesSummary`, a `currency` field on every `CurrencySalesSummary` /
`TopProductMetric`) as a **structural discipline requirement**, not
because multiple currencies exist today: the contract is that this
repository can never silently combine totals across currencies, so if a
per-row currency column is ever added, the aggregation logic does not
need to be redesigned — only the row-to-currency-key mapping changes.

Proven by `resultIsKeyedByTheCompanysConfiguredCurrencyNeverAFalseCombinedTotal`:
setting `base_currency = "EUR"` for company 1 makes
`SalesSummary.totalsByCurrency` come back keyed by `CurrencyCode("EUR")`,
not `"USD"`.

## No FX conversion

Out of scope for M5.6, per the governing checkpoint. Not attempted.

## Category filter (forward reference)

`ReportScope.categoryId` filters `selectSaleItemsForPeriod`/
`selectReturnItemsForPeriod` via `JOIN products p ... p.category_id`,
i.e. the product's **current** category, not a historical snapshot — see
`historical-category-reporting-decision.md` (M5.6.7) for the full
decision record.
