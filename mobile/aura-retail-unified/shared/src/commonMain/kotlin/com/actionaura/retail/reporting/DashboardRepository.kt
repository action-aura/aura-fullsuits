package com.actionaura.retail.reporting

/**
 * M5.6.8 -- the canonical Dashboard authority. Composes
 * `ReportingRepository`+`ProductRepository`'s already-real formulas; owns
 * NO aggregation logic of its own (`dashboard-authority-contract.md`'s
 * own "no duplicated formulas" gate). Deliberately reports operational
 * sales movement, never "profit" -- this milestone has no cost-of-goods
 * authority to compute margin from.
 */
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
