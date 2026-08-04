package com.actionaura.retail.reporting

import com.actionaura.retail.data.SettingsRepository
import com.actionaura.retail.data.sqldelight.DatabaseWriteGate
import com.actionaura.retail.db.RetailDatabase
import com.actionaura.retail.financial.CurrencyCode
import com.actionaura.retail.financial.FinancialResult
import com.actionaura.retail.financial.Money
import com.actionaura.retail.financial.Quantity
import kotlinx.coroutines.sync.withLock

private const val ELIGIBLE_STATUS = "completed"
private const val BASE_CURRENCY_SETTING_KEY = "base_currency"
private const val DEFAULT_CURRENCY = "USD"

/**
 * M5.6 -- real, SQLDelight-backed `ReportingRepository`.
 *
 * `ELIGIBLE_STATUS` is a real, forward-looking decision, not dead code:
 * `reporting-authority-audit.md` §4 confirms every sale/return row in the
 * legacy authority is unconditionally `'completed'` today (no reporting
 * query there filters by status at all, because nothing else exists to
 * filter) -- the unified schema's own `status` column is filtered
 * explicitly here so a real future status value is correctly excluded
 * the moment one exists, rather than silently included the way it would
 * be if this repository copied the legacy authority's own absence of a
 * filter.
 *
 * `resolveCurrency` is called OUTSIDE `gate.mutex.withLock` deliberately
 * -- it goes through `SettingsRepository`, which holds its OWN
 * `DatabaseWriteGate`-backed lock; calling it from inside this
 * repository's own `withLock` block would be the exact non-reentrant-
 * Mutex deadlock hazard `stock-concurrency-report.md` already found and
 * fixed once for `CategoryRepository.insert`/`BranchRepository.insert`.
 */
class SqlDelightReportingRepository(
    private val db: RetailDatabase,
    private val gate: DatabaseWriteGate,
    private val settingsRepository: SettingsRepository,
) : ReportingRepository {

    override suspend fun getSalesSummary(scope: ReportScope, period: ReportPeriod): SalesSummary {
        val currency = resolveCurrency(scope.companyId)
        return gate.mutex.withLock {
            val issues = mutableListOf<ReportingDataQualityIssue>()
            val summary = computeSummaryLocked(scope, period, currency, issues)
            SalesSummary(mapOf(currency to summary), issues)
        }
    }

    override suspend fun getSalesTrend(scope: ReportScope, buckets: List<Pair<ReportPeriod, String>>): SalesTrendResult {
        val currency = resolveCurrency(scope.companyId)
        return gate.mutex.withLock {
            val issues = mutableListOf<ReportingDataQualityIssue>()
            val trendBuckets = buckets.map { (period, label) ->
                SalesTrendBucket(period, label, computeSummaryLocked(scope, period, currency, issues))
            }
            SalesTrendResult(trendBuckets, issues)
        }
    }

    override suspend fun getTopProductsByQuantity(scope: ReportScope, period: ReportPeriod, limit: Long): TopProductsResult {
        val currency = resolveCurrency(scope.companyId)
        return gate.mutex.withLock { computeTopProductsLocked(scope, period, limit, currency, byRevenue = false) }
    }

    override suspend fun getTopProductsByNetRevenue(scope: ReportScope, period: ReportPeriod, limit: Long): TopProductsResult {
        val currency = resolveCurrency(scope.companyId)
        return gate.mutex.withLock { computeTopProductsLocked(scope, period, limit, currency, byRevenue = true) }
    }

    private suspend fun resolveCurrency(companyId: Long): CurrencyCode =
        CurrencyCode(settingsRepository.getSetting(companyId, BASE_CURRENCY_SETTING_KEY) ?: DEFAULT_CURRENCY)

    private fun computeSummaryLocked(scope: ReportScope, period: ReportPeriod, currency: CurrencyCode, issues: MutableList<ReportingDataQualityIssue>): CurrencySalesSummary {
        var gross = Money.ZERO
        var count = 0L
        db.reportingQueries.selectSalesForPeriod(
            scope.companyId, ELIGIBLE_STATUS, period.startInclusiveEpochMillis, period.endExclusiveEpochMillis, scope.branchId,
        ).executeAsList().forEach { row ->
            when (val parsed = Money.parse(row.total)) {
                is FinancialResult.Success -> { gross += parsed.value; count++ }
                is FinancialResult.Failure -> issues += ReportingDataQualityIssue.MalformedMoney("sales", row.id, row.total)
            }
        }

        var confirmedReturns = Money.ZERO
        db.reportingQueries.selectReturnsForPeriod(
            scope.companyId, ELIGIBLE_STATUS, period.startInclusiveEpochMillis, period.endExclusiveEpochMillis, scope.branchId,
        ).executeAsList().forEach { row ->
            when (val parsed = Money.parse(row.refund_amount)) {
                is FinancialResult.Success -> confirmedReturns += parsed.value
                is FinancialResult.Failure -> issues += ReportingDataQualityIssue.MalformedMoney("returns", row.id, row.refund_amount)
            }
        }

        // Real, deliberately unclamped -- reporting-authority-audit.md §1's
        // own cited legacy invariant (test_revenue_becoming_negative_is_not_clamped).
        val net = gross - confirmedReturns
        return CurrencySalesSummary(currency, gross, confirmedReturns, net, count)
    }

    private fun computeTopProductsLocked(scope: ReportScope, period: ReportPeriod, limit: Long, currency: CurrencyCode, byRevenue: Boolean): TopProductsResult {
        val issues = mutableListOf<ReportingDataQualityIssue>()
        data class Accumulator(var name: String, var quantity: Quantity, var revenue: Money)
        val perProduct = mutableMapOf<Long, Accumulator>()

        db.reportingQueries.selectSaleItemsForPeriod(
            scope.companyId, ELIGIBLE_STATUS, period.startInclusiveEpochMillis, period.endExclusiveEpochMillis, scope.branchId, scope.categoryId,
        ).executeAsList().forEach { row ->
            val qty = Quantity.zeroOrMore(row.quantity)
            val rev = Money.parse(row.line_total).getOrNull()
            if (qty == null) { issues += ReportingDataQualityIssue.MalformedQuantity("sale_items", row.product_id, row.quantity); return@forEach }
            if (rev == null) { issues += ReportingDataQualityIssue.MalformedMoney("sale_items", row.product_id, row.line_total); return@forEach }
            val acc = perProduct.getOrPut(row.product_id) { Accumulator(row.product_name_at_sale, Quantity.ZERO, Money.ZERO) }
            acc.quantity += qty
            acc.revenue += rev
        }

        db.reportingQueries.selectReturnItemsForPeriod(
            scope.companyId, ELIGIBLE_STATUS, period.startInclusiveEpochMillis, period.endExclusiveEpochMillis, scope.branchId, scope.categoryId,
        ).executeAsList().forEach { row ->
            val qty = Quantity.zeroOrMore(row.quantity)
            val rev = Money.parse(row.line_total).getOrNull()
            if (qty == null) { issues += ReportingDataQualityIssue.MalformedQuantity("return_items", row.product_id, row.quantity); return@forEach }
            if (rev == null) { issues += ReportingDataQualityIssue.MalformedMoney("return_items", row.product_id, row.line_total); return@forEach }
            // A return in THIS period for a product sold in an EARLIER
            // period (not present in perProduct yet) still needs an entry
            // -- real, valid negative-net scenario (M5.6.13/M5.6.15's own
            // required "Return in later month" / "negative period net"
            // cases).
            val acc = perProduct.getOrPut(row.product_id) { Accumulator(row.product_name_at_sale, Quantity.ZERO, Money.ZERO) }
            acc.quantity -= qty
            acc.revenue -= rev
        }

        val comparator: Comparator<TopProductMetric> = if (byRevenue) {
            compareByDescending<TopProductMetric> { it.netRevenue }.thenBy { it.productName }.thenBy { it.productId }
        } else {
            compareByDescending<TopProductMetric> { it.netQuantity }.thenBy { it.productName }.thenBy { it.productId }
        }
        val metrics = perProduct.entries
            .map { (productId, acc) -> TopProductMetric(productId, acc.name, acc.quantity, acc.revenue, currency) }
            .sortedWith(comparator)
            .take(limit.toInt())
        return TopProductsResult(metrics, issues)
    }
}
