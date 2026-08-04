package com.actionaura.retail.reporting

import app.cash.sqldelight.driver.jdbc.sqlite.JdbcSqliteDriver
import com.actionaura.retail.data.sqldelight.DatabaseWriteGate
import com.actionaura.retail.data.sqldelight.SqlDelightSettingsRepository
import com.actionaura.retail.db.RetailDatabase
import kotlinx.coroutines.test.runTest
import kotlinx.datetime.LocalDate
import kotlinx.datetime.TimeZone
import kotlinx.datetime.atStartOfDayIn
import kotlin.test.Test
import kotlin.test.assertTrue

private fun epochMillisUtc(year: Int, month: Int, day: Int): Long =
    LocalDate(year, month, day).atStartOfDayIn(TimeZone.UTC).toEpochMilliseconds()

/**
 * M5.6.17 -- real, executed baseline timing at representative scale.
 * NOT the final performance gate (`reporting-query-plan-report.md`,
 * M5.7, owns real `EXPLAIN QUERY PLAN` index-usage validation) -- this
 * only proves the reporting authority completes in a reasonable time at
 * 10,000 Products / substantial Sales-Returns data / multiple Branches
 * and Categories, and records a real baseline to compare M5.7 against.
 */
class ReportingPerformancePrepTest {

    private fun newDb(): RetailDatabase {
        val driver = JdbcSqliteDriver(JdbcSqliteDriver.IN_MEMORY)
        driver.execute(null, "PRAGMA foreign_keys=ON", 0)
        RetailDatabase.Schema.create(driver)
        return RetailDatabase(driver)
    }

    @Test
    fun reportingAuthorityCompletesInBoundedTimeAtRepresentativeScale() = runTest {
        val db = newDb()
        val gate = DatabaseWriteGate()
        val settings = SqlDelightSettingsRepository(db, gate)

        val branchIds = mutableListOf<Long>()
        val categoryIds = mutableListOf<Long>()
        val productIds = mutableListOf<Long>()

        val seedStart = System.currentTimeMillis()
        db.transaction {
            repeat(5) { i ->
                db.catalogQueries.insertBranch(1L, "Branch-$i", null, null, 500L)
                branchIds += db.catalogQueries.lastInsertRowId().executeAsOne()
            }
            repeat(20) { i ->
                db.catalogQueries.insertCategory(1L, "Category-$i", null, 500L)
                categoryIds += db.catalogQueries.lastInsertRowId().executeAsOne()
            }
            repeat(10_000) { i ->
                db.catalogQueries.insertProduct(
                    1L, "SKU-$i", null, "Product-$i", "product-$i", categoryIds[i % categoryIds.size],
                    "1.00", "2.00", "0", "unit", 5, 1000L, 1000L,
                )
                productIds += db.catalogQueries.lastInsertRowId().executeAsOne()
            }
        }

        val startDay = epochMillisUtc(2026, 1, 1)
        db.transaction {
            repeat(2_000) { saleIndex ->
                val branchId = branchIds[saleIndex % branchIds.size]
                val createdAt = startDay + (saleIndex % 30) * 86_400_000L
                db.salesQueries.insertSale(1L, null, branchId, null, "POS", "0.00", "0.00", "0.00", "30.00", "30.00", "0.00", "cash", null, null, null, createdAt)
                val saleId = db.catalogQueries.lastInsertRowId().executeAsOne()
                repeat(3) { lineIndex ->
                    val productId = productIds[(saleIndex * 3 + lineIndex) % productIds.size]
                    db.salesQueries.insertSaleItem(saleId, productId, "Product-${(saleIndex * 3 + lineIndex) % productIds.size}", "1", "10.00", "0", "0", "10.00")
                }
                if (saleIndex % 10 == 0) {
                    val returnBranchId = branchIds[saleIndex % branchIds.size]
                    db.returnsQueries.insertReturn(1L, null, saleId, returnBranchId, "POS", null, "cash", "10.00", null, createdAt)
                    val returnId = db.catalogQueries.lastInsertRowId().executeAsOne()
                    db.returnsQueries.insertReturnItem(returnId, productIds[saleIndex % productIds.size], "Returned-Product", "1", "10.00", "10.00")
                }
            }
        }
        val seedElapsedMs = System.currentTimeMillis() - seedStart

        val repo = SqlDelightReportingRepository(db, gate, settings)
        val dashboardRepo = SqlDelightDashboardRepository(repo, com.actionaura.retail.data.sqldelight.SqlDelightProductRepository(db, gate))
        val overallPeriod = ReportPeriodFactory.customRange(startDay, startDay + 30 * 86_400_000L)
        val trendBuckets = ReportPeriodFactory.dailyBuckets(TimeZone.UTC, startDay, startDay + 30 * 86_400_000L).mapIndexed { i, p -> p to "day-$i" }

        val summaryStart = System.currentTimeMillis()
        val summary = repo.getSalesSummary(ReportScope(1L), overallPeriod)
        val summaryElapsedMs = System.currentTimeMillis() - summaryStart

        val trendStart = System.currentTimeMillis()
        val trend = repo.getSalesTrend(ReportScope(1L), trendBuckets)
        val trendElapsedMs = System.currentTimeMillis() - trendStart

        val topStart = System.currentTimeMillis()
        val topByQuantity = repo.getTopProductsByQuantity(ReportScope(1L), overallPeriod, 20L)
        val topByRevenue = repo.getTopProductsByNetRevenue(ReportScope(1L), overallPeriod, 20L)
        val topElapsedMs = System.currentTimeMillis() - topStart

        val dashboardStart = System.currentTimeMillis()
        val dashboard = dashboardRepo.getDashboard(ReportScope(1L), overallPeriod, overallPeriod, trendBuckets, 20L, 20L, 0L)
        val dashboardElapsedMs = System.currentTimeMillis() - dashboardStart

        println("M5.6.17 baseline (10,000 products / 2,000 sales / 6,000 sale_items / 200 returns / 5 branches / 20 categories):")
        println("  seed:            ${seedElapsedMs}ms")
        println("  getSalesSummary: ${summaryElapsedMs}ms")
        println("  getSalesTrend (30 buckets): ${trendElapsedMs}ms")
        println("  top products (both rankings): ${topElapsedMs}ms")
        println("  getDashboard:    ${dashboardElapsedMs}ms")

        assertTrue(summary.totalsByCurrency.values.first().transactionCount == 2000L)
        assertTrue(trend.buckets.size == 30)
        assertTrue(topByQuantity.metrics.size <= 20)
        assertTrue(topByRevenue.metrics.size <= 20)
        assertTrue(dashboard.lastRefreshedEpochMillis == 0L)

        // Generous baseline bound -- proves "completes in bounded time," not
        // a tuned performance target. Real index-usage validation is M5.7's
        // job (reporting-query-plan-report.md), not this milestone's.
        assertTrue(summaryElapsedMs < 10_000, "getSalesSummary took ${summaryElapsedMs}ms, expected under 10s at this scale")
        assertTrue(trendElapsedMs < 10_000, "getSalesTrend took ${trendElapsedMs}ms, expected under 10s at this scale")
        assertTrue(topElapsedMs < 10_000, "top products took ${topElapsedMs}ms, expected under 10s at this scale")
    }
}
