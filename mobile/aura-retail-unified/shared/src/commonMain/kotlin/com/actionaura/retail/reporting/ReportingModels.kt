package com.actionaura.retail.reporting

import com.actionaura.retail.financial.CurrencyCode
import com.actionaura.retail.financial.Money
import com.actionaura.retail.financial.Quantity

/**
 * M5.6.9 -- immutable shared report read models. All exact financial
 * values use `Money`; all quantities use `Quantity`; no SQLDelight
 * generated row type and no platform date type escapes this package
 * (`reporting-definition-contract.md`).
 */

/** Explicit scope captured once at invocation (M5.6.5's own "no current-Branch mutation during report execution" requirement). */
data class ReportScope(val companyId: Long, val branchId: Long? = null, val categoryId: Long? = null)

/**
 * M5.6.3 -- one currency's totals for a period. `transactionCount` is
 * LEGACY_REQUIRED: finalized Sales count only, NOT reduced for returns --
 * matches the legacy authority's own `today_txns`/`month_txns`
 * (`COUNT(*) FROM sales`, reporting-authority-audit.md §1) exactly; there
 * is no "fully reversed sale" tracking anywhere in this codebase to base
 * a different definition on.
 */
data class CurrencySalesSummary(
    val currency: CurrencyCode,
    val grossSales: Money,
    val confirmedReturns: Money,
    val netSales: Money,
    val transactionCount: Long,
)

data class SalesSummary(
    val totalsByCurrency: Map<CurrencyCode, CurrencySalesSummary>,
    val dataQualityIssues: List<ReportingDataQualityIssue> = emptyList(),
)

data class SalesTrendBucket(
    val period: ReportPeriod,
    val bucketLabelKey: String,
    val summary: CurrencySalesSummary,
)

data class SalesTrendResult(
    val buckets: List<SalesTrendBucket>,
    val dataQualityIssues: List<ReportingDataQualityIssue> = emptyList(),
)

/** M5.6.6 -- net_quantity/net_revenue per product, one metric list per ranking basis. */
data class TopProductMetric(
    val productId: Long,
    val productName: String,
    val netQuantity: Quantity,
    val netRevenue: Money,
    val currency: CurrencyCode,
)

data class TopProductsResult(
    val metrics: List<TopProductMetric>,
    val dataQualityIssues: List<ReportingDataQualityIssue> = emptyList(),
)

data class LowStockPreviewItem(val productId: Long, val productName: String, val totalOnHand: Quantity, val reorderLevel: Long)

data class DashboardSnapshot(
    val scope: ReportScope,
    val currency: CurrencyCode,
    val today: CurrencySalesSummary,
    val selectedPeriod: CurrencySalesSummary,
    val lowStockCount: Long,
    val lowStockPreview: List<LowStockPreviewItem>,
    val topProductsByQuantity: List<TopProductMetric>,
    val topProductsByNetRevenue: List<TopProductMetric>,
    val salesTrend: SalesTrendResult,
    val lastRefreshedEpochMillis: Long,
    val dataQualityIssues: List<ReportingDataQualityIssue>,
)

/** M5.6.12 -- surfaced, never silently swallowed. */
sealed class ReportingDataQualityIssue(val code: String) {
    data class MalformedMoney(val table: String, val rowId: Long, val rawValue: String) : ReportingDataQualityIssue("MALFORMED_MONEY")
    data class MalformedQuantity(val table: String, val rowId: Long, val rawValue: String) : ReportingDataQualityIssue("MALFORMED_QUANTITY")
}
