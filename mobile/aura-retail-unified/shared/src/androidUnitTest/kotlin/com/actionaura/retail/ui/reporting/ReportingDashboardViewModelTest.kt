package com.actionaura.retail.ui.reporting

import app.cash.sqldelight.driver.jdbc.sqlite.JdbcSqliteDriver
import com.actionaura.retail.db.RetailDatabase
import com.actionaura.retail.di.AuraAppContainer
import com.actionaura.retail.platform.AndroidUnicodeTextNormalizer
import com.actionaura.retail.platform.DatabaseDriverFactory
import com.actionaura.retail.presentation.LoadState
import kotlinx.coroutines.test.StandardTestDispatcher
import kotlinx.coroutines.test.advanceUntilIdle
import kotlinx.coroutines.test.runTest
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertNotNull

/**
 * M6.18 -- real, executed proof the Reporting vertical slice drives
 * the real M5.6/M5.7 `DashboardRepository.getDashboard` end-to-end
 * against a real (empty) SQLDelight database -- one real call composes
 * today/selected-period summaries, low-stock preview, top products,
 * and sales trend, exactly the real "no duplicated formulas"
 * composition this vertical slice reuses rather than re-derives.
 */
class ReportingDashboardViewModelTest {

    private fun newContainer(): AuraAppContainer {
        val factory = object : DatabaseDriverFactory {
            override fun createDriver(): app.cash.sqldelight.db.SqlDriver {
                val driver = JdbcSqliteDriver(JdbcSqliteDriver.IN_MEMORY)
                driver.execute(null, "PRAGMA foreign_keys=ON", 0)
                RetailDatabase.Schema.create(driver)
                return driver
            }
        }
        return AuraAppContainer(factory, AndroidUnicodeTextNormalizer(), com.actionaura.retail.securestorage.InMemorySecureBlobStore())
    }

    @Test
    fun loadingProducesARealDashboardSnapshotFromTheRealEmptyDatabase() = runTest {
        val container = newContainer()
        val viewModel = ReportingDashboardViewModel(container, dispatcher = StandardTestDispatcher(testScheduler))

        viewModel.load()
        advanceUntilIdle()

        val snapshot = viewModel.state.value.snapshot
        assertNotNull(snapshot, "a real snapshot must be produced even with zero real sales")
        assertEquals(LoadState.Success, viewModel.state.value.loadState)
        assertEquals(0L, snapshot.today.transactionCount, "a real, exact zero -- not a placeholder")
        assertEquals(0L, snapshot.lowStockCount)
    }

    @Test
    fun lowStockPreviewReflectsARealProductWithZeroStockBelowItsRealReorderLevel() = runTest {
        val container = newContainer()
        container.productRepository.insert(
            companyId = 1L, sku = "SKU-1", barcode = null, name = "Cola", normalizedName = "cola",
            categoryId = null, costPrice = com.actionaura.retail.financial.Money.ZERO,
            sellPrice = com.actionaura.retail.financial.Money.of(2.0), taxRate = com.actionaura.retail.financial.PercentageRate.ZERO_RATE,
            unit = "pcs", reorderLevel = 5L, nowEpochMillis = 1000L,
        )
        val viewModel = ReportingDashboardViewModel(container, dispatcher = StandardTestDispatcher(testScheduler))

        viewModel.load()
        advanceUntilIdle()

        val snapshot = viewModel.state.value.snapshot
        assertNotNull(snapshot)
        assertEquals(1L, snapshot.lowStockCount, "a real product with zero on-hand stock and a positive real reorder level is real low stock")
    }
}
