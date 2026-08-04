package com.actionaura.retail.reporting

/**
 * M5.6 -- the canonical reporting query authority. Real, not a boundary
 * stub (`RepositoryBoundaries.kt`'s own note explains the transition).
 * Every method takes a `ReportScope` captured once by the caller --
 * "report captures scope once at invocation," no re-reading a mutable
 * current-branch/current-company value mid-computation
 * (`sales-trend-contract.md`).
 */
interface ReportingRepository {
    /** M5.6.3 -- gross/confirmed-returns/net sales and transaction count for one period, keyed by currency. */
    suspend fun getSalesSummary(scope: ReportScope, period: ReportPeriod): SalesSummary

    /** M5.6.5 -- one bucket per `(period, label)` pair; caller supplies the buckets (from `ReportPeriodFactory`) and their display label keys. */
    suspend fun getSalesTrend(scope: ReportScope, buckets: List<Pair<ReportPeriod, String>>): SalesTrendResult

    /** M5.6.6 -- quantity-ranked (`net_quantity` descending, deterministic tie-break). */
    suspend fun getTopProductsByQuantity(scope: ReportScope, period: ReportPeriod, limit: Long): TopProductsResult

    /** M5.6.6 -- net-revenue-ranked (`net_revenue` descending, deterministic tie-break, never combines currencies). */
    suspend fun getTopProductsByNetRevenue(scope: ReportScope, period: ReportPeriod, limit: Long): TopProductsResult
}
