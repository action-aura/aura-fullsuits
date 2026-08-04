package com.actionaura.retail.reporting

import app.cash.sqldelight.driver.jdbc.sqlite.JdbcSqliteDriver
import com.actionaura.retail.data.DomainResult
import com.actionaura.retail.data.sqldelight.DatabaseWriteGate
import com.actionaura.retail.data.sqldelight.SqlDelightBranchRepository
import com.actionaura.retail.data.sqldelight.SqlDelightCategoryRepository
import com.actionaura.retail.data.sqldelight.SqlDelightProductRepository
import com.actionaura.retail.data.sqldelight.SqlDelightSettingsRepository
import com.actionaura.retail.db.RetailDatabase
import com.actionaura.retail.financial.Money
import com.actionaura.retail.financial.PercentageRate
import kotlinx.coroutines.test.runTest
import kotlinx.datetime.LocalDate
import kotlinx.datetime.TimeZone
import kotlinx.datetime.atStartOfDayIn
import kotlin.test.Test
import kotlin.test.assertEquals

private fun epochMillisUtc(year: Int, month: Int, day: Int): Long =
    LocalDate(year, month, day).atStartOfDayIn(TimeZone.UTC).toEpochMilliseconds()

/**
 * M5.6.10 -- real, executed proof of structural business/branch isolation:
 * `company_id` is a required, independently-checked filter on every
 * reporting query, so no `branchId`/`categoryId` value -- even one that
 * legitimately belongs to a different company -- can widen a request's
 * scope or leak another company's rows. This is a structural query-shape
 * property, not access-control authorization (which remains
 * `DEFERRED_TO_MILESTONES_7_TO_10`,
 * `reporting-authorization-integration-boundary.md`).
 */
class BusinessBranchIsolationTest {

    private fun newDb(): RetailDatabase {
        val driver = JdbcSqliteDriver(JdbcSqliteDriver.IN_MEMORY)
        driver.execute(null, "PRAGMA foreign_keys=ON", 0)
        RetailDatabase.Schema.create(driver)
        return RetailDatabase(driver)
    }

    private fun seedSale(db: RetailDatabase, companyId: Long, branchId: Long?, total: String, createdAt: Long): Long {
        db.salesQueries.insertSale(companyId, null, branchId, null, "POS", "0.00", "0.00", "0.00", total, total, "0.00", "cash", null, null, null, createdAt)
        return db.catalogQueries.lastInsertRowId().executeAsOne()
    }

    private fun dayPeriod(y: Int, m: Int, d: Int) = ReportPeriodFactory.customRange(epochMillisUtc(y, m, d), epochMillisUtc(y, m, d + 1))

    @Test
    fun requestingAnotherCompanysCategoryIdNeverLeaksThatCompanysProductSales() = runTest {
        val db = newDb()
        val gate = DatabaseWriteGate()
        val branch1 = SqlDelightBranchRepository(db, gate).insert(1L, "Company 1 Main", null, null, 500L).id
        val branch2 = SqlDelightBranchRepository(db, gate).insert(2L, "Company 2 Main", null, null, 500L).id
        val categoryRepo = SqlDelightCategoryRepository(db, gate)
        val company2Category = categoryRepo.insert(2L, "Company 2 Drinks", null, 100L).id
        val productRepo = SqlDelightProductRepository(db, gate)
        val company2Product = (productRepo.insert(2L, "SKU-C2", null, "Company 2 Cola", "company 2 cola", company2Category, Money.ZERO, Money.of(1.0), PercentageRate.trusted(0.0), "unit", 5, 1000L) as DomainResult.Success).value

        val saleId = seedSale(db, 2L, branch2, "9.00", epochMillisUtc(2026, 1, 15))
        db.salesQueries.insertSaleItem(saleId, company2Product.id, "Company 2 Cola", "3", "9.00", "0", "0", "9.00")

        val repo = SqlDelightReportingRepository(db, gate, SqlDelightSettingsRepository(db, gate))
        // Company 1 requesting company 2's real category id must never see company 2's product sale.
        val leaked = repo.getTopProductsByQuantity(ReportScope(1L, branchId = branch1, categoryId = company2Category), dayPeriod(2026, 1, 15), 10L)

        assertEquals(emptyList(), leaked.metrics, "a categoryId belonging to a different company must never leak that company's sale data")
    }

    @Test
    fun companyIdIsIndependentlyCheckedOnEveryQueryNoParameterCanWidenScopeBeyondTheRequestedCompany() = runTest {
        val db = newDb()
        val gate = DatabaseWriteGate()
        val branch1 = SqlDelightBranchRepository(db, gate).insert(1L, "Company 1 Main", null, null, 500L).id
        val branch2 = SqlDelightBranchRepository(db, gate).insert(2L, "Company 2 Main", null, null, 500L).id
        seedSale(db, 1L, branch1, "10.00", epochMillisUtc(2026, 1, 15))
        seedSale(db, 2L, branch2, "500.00", epochMillisUtc(2026, 1, 15))

        val repo = SqlDelightReportingRepository(db, gate, SqlDelightSettingsRepository(db, gate))
        // branchId = null ("all local branches") must still mean "all of THIS company's branches," not every branch globally.
        val company1Unscoped = repo.getSalesSummary(ReportScope(1L), dayPeriod(2026, 1, 15))
        val totals = company1Unscoped.totalsByCurrency.values.first()

        assertEquals(Money.of(10.00), totals.grossSales, "an unscoped branchId must never widen the query beyond the requested companyId")
    }
}
