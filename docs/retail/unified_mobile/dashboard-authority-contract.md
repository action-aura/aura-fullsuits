# Dashboard Authority Contract (M5.6.8-M5.6.9)

Real, executed contract for `DashboardRepository`/
`SqlDelightDashboardRepository`, proven by
`SqlDelightDashboardRepositoryTest.kt` (3/3,
`TEST-com.actionaura.retail.reporting.SqlDelightDashboardRepositoryTest.xml`
tests="3" failures="0" errors="0").

## Shape

```kotlin
interface DashboardRepository {
    suspend fun getDashboard(
        scope: ReportScope,
        todayPeriod: ReportPeriod,
        selectedPeriod: ReportPeriod,
        trendBuckets: List<Pair<ReportPeriod, String>>,
        topProductsLimit: Long,
        lowStockPreviewLimit: Long,
        nowEpochMillis: Long,
    ): DashboardSnapshot
}
```

`todayPeriod`/`selectedPeriod`/`trendBuckets` are caller-supplied
`ReportPeriod`s (from `ReportPeriodFactory`), not computed inside the
Dashboard authority — the same "this repository owns no date-boundary
math" discipline as `ReportingRepository`.

## Pure composition, no duplicated formulas

`SqlDelightDashboardRepository` computes nothing itself: every field on
`DashboardSnapshot` is a direct pass-through of a `ReportingRepository`
or `ProductRepository` call —

| Field | Source |
|---|---|
| `today`, `selectedPeriod` | `ReportingRepository.getSalesSummary` |
| `salesTrend` | `ReportingRepository.getSalesTrend` |
| `topProductsByQuantity`/`topProductsByNetRevenue` | `ReportingRepository.getTopProductsByQuantity`/`getTopProductsByNetRevenue` |
| `lowStockCount`, `lowStockPreview` | `ProductRepository.listLowStock` (M5.5.12, already the real low-stock authority) |
| `currency` | the single key of `selectedPeriod.totalsByCurrency` |
| `lastRefreshedEpochMillis` | the caller-injected `nowEpochMillis` parameter |
| `dataQualityIssues` | union of every composed call's own `dataQualityIssues` |

Proven by
`dashboardComposesTodayAndSelectedPeriodExactlyMatchingTheReportingRepositoryItDelegatesTo`
and `topProductsOnTheDashboardExactlyMatchTheReportingRepositoryRankings`:
a direct call to `ReportingRepository`/`ProductRepository` and the
dashboard's own composed value assert `equals()`, not just
"close enough" — any future accidental duplication of the aggregation
logic inside the Dashboard authority would break these tests
immediately.

## No client-supplied totals

Every number is computed inside `getDashboard` from the repository
calls above; the method signature has no parameter through which a
caller could inject a pre-computed total.

## No "profit" label

`DashboardSnapshot` has no `profit`/`margin` field. This milestone has
no cost-of-goods or accounting authority to compute real profit from —
`today`/`selectedPeriod` report gross/confirmed-returns/net **sales
movement** only, never profit. Any future profit figure belongs to a
real accounting integration, not a relabeling of `netSales`.

## Low stock is company-wide, not Branch-scoped

`ProductRepository.listLowStock(companyId)` has no Branch parameter — it
mirrors the legacy `dashboard_stats` low-stock query exactly (M5.5.12),
which itself has no Branch scoping. `scope.branchId` therefore narrows
every sales/trend/top-products figure on the snapshot but intentionally
does **not** narrow `lowStockCount`/`lowStockPreview`. This is a real,
disclosed asymmetry, not an oversight — proven by
`lowStockCountAndPreviewExactlyMatchProductRepositoryListLowStock`
asserting the dashboard's low-stock figures equal a direct
`ProductRepository.listLowStock` call.

## Deterministic snapshot time

`lastRefreshedEpochMillis` comes from the caller-injected
`nowEpochMillis` parameter — there is no internal `Clock.System` read.
Proven by the first test asserting `snapshot.lastRefreshedEpochMillis ==
999_000L` for an arbitrarily chosen injected value.

## Deferred authorization

Same structural note as every other M5.6 authority: `ReportScope` is a
trusted input; real authorization remains
`DEFERRED_TO_MILESTONES_7_TO_10`
(`reporting-authorization-integration-boundary.md`).
