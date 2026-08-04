package com.actionaura.retail.reporting

import com.actionaura.retail.data.ProductRepository

/**
 * M5.6.8 -- real `DashboardRepository`. Pure composition: every number in
 * `DashboardSnapshot` comes from `ReportingRepository` (already-proven
 * exact aggregation) or `ProductRepository.listLowStock` (already-proven
 * M5.5.12 low-stock authority) -- this class computes nothing itself
 * beyond assembling the snapshot, so it structurally cannot duplicate or
 * drift from either authority's own formula.
 *
 * `listLowStock` is company-wide, not Branch-scoped (`ProductRepository`'s
 * own KDoc: "mirrors the legacy dashboard_stats low_stock query exactly"
 * -- the legacy authority has no Branch-scoped low-stock query to port),
 * so `scope.branchId` intentionally does not narrow the low-stock figures
 * even though it narrows every sales figure on the same snapshot.
 */
class SqlDelightDashboardRepository(
    private val reportingRepository: ReportingRepository,
    private val productRepository: ProductRepository,
) : DashboardRepository {

    override suspend fun getDashboard(
        scope: ReportScope,
        todayPeriod: ReportPeriod,
        selectedPeriod: ReportPeriod,
        trendBuckets: List<Pair<ReportPeriod, String>>,
        topProductsLimit: Long,
        lowStockPreviewLimit: Long,
        nowEpochMillis: Long,
    ): DashboardSnapshot {
        val todaySummary = reportingRepository.getSalesSummary(scope, todayPeriod)
        val selectedSummary = reportingRepository.getSalesSummary(scope, selectedPeriod)
        val trend = reportingRepository.getSalesTrend(scope, trendBuckets)
        val byQuantity = reportingRepository.getTopProductsByQuantity(scope, selectedPeriod, topProductsLimit)
        val byRevenue = reportingRepository.getTopProductsByNetRevenue(scope, selectedPeriod, topProductsLimit)
        val lowStock = productRepository.listLowStock(scope.companyId)

        // Both summaries always contain exactly one entry, keyed by the
        // company's single resolved currency (`getSalesSummary`'s own
        // contract) -- there is never a currency mismatch to reconcile here.
        val currency = selectedSummary.totalsByCurrency.keys.first()
        val issues = todaySummary.dataQualityIssues + selectedSummary.dataQualityIssues +
            trend.dataQualityIssues + byQuantity.dataQualityIssues + byRevenue.dataQualityIssues

        return DashboardSnapshot(
            scope = scope,
            currency = currency,
            today = todaySummary.totalsByCurrency.getValue(currency),
            selectedPeriod = selectedSummary.totalsByCurrency.getValue(currency),
            lowStockCount = lowStock.size.toLong(),
            lowStockPreview = lowStock.take(lowStockPreviewLimit.toInt()).map {
                LowStockPreviewItem(it.product.id, it.product.name, it.totalOnHandAcrossBranches, it.product.reorderLevel)
            },
            topProductsByQuantity = byQuantity.metrics,
            topProductsByNetRevenue = byRevenue.metrics,
            salesTrend = trend,
            lastRefreshedEpochMillis = nowEpochMillis,
            dataQualityIssues = issues,
        )
    }
}
