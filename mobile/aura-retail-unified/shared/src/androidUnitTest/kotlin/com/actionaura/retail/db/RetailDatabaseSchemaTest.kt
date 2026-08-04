package com.actionaura.retail.db

import app.cash.sqldelight.driver.jdbc.sqlite.JdbcSqliteDriver
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertNull

/**
 * M4 -- real, executed proof the generated SQLDelight schema actually
 * works end to end (creates every one of the 19 real tables, real FK
 * constraints, real inserts/queries through the generated typed API) --
 * not just that it compiles. Uses an in-memory JDBC SQLite driver (JVM-only
 * test tooling; AndroidSqliteDriver needs a real Android Context/
 * instrumentation, out of reach for a plain unit test) -- the schema and
 * generated query code under test are the same commonMain code that runs
 * on a real device via AndroidDatabaseDriverFactory.
 */
class RetailDatabaseSchemaTest {

    private fun newDatabase(): RetailDatabase {
        val driver = JdbcSqliteDriver(JdbcSqliteDriver.IN_MEMORY)
        driver.execute(null, "PRAGMA foreign_keys=ON", 0)
        RetailDatabase.Schema.create(driver)
        return RetailDatabase(driver)
    }

    @Test
    fun schemaCreatesAllTablesAndBasicCrudWorks() {
        val db = newDatabase()

        db.catalogQueries.insertCategory(1L, "Beverages", null, 1000L)
        val categoryId = db.catalogQueries.lastInsertRowId().executeAsOne()

        db.catalogQueries.insertProduct(
            1L, "SKU-001", "0000000001", "Cola 330ml", categoryId,
            "0.50", "1.50", "10", "can", 24, 2000L,
        )
        val productId = db.catalogQueries.lastInsertRowId().executeAsOne()

        val products = db.catalogQueries.selectActiveProducts(1L).executeAsList()
        assertEquals(1, products.size)
        assertEquals("Cola 330ml", products.first().name)
        assertEquals("1.50", products.first().sell_price) // TEXT, not REAL -- real proof the decimal-as-string decision actually took effect end to end

        db.inventoryQueries.upsertOpeningStock(1L, productId, 1L, "100")
        val onHand = db.inventoryQueries.selectStockOnHand(1L, productId, 1L).executeAsOneOrNull()
        assertEquals("100", onHand)
    }

    @Test
    fun foreignKeyEnforcementIsReallyOn() {
        // Real proof foreign_keys=ON actually takes effect (not just
        // declared) -- inserting a sale_item referencing a non-existent
        // product must fail.
        val db = newDatabase()
        var threw = false
        try {
            db.salesQueries.insertSaleItem(999999L, 999999L, "ghost product", "1", "10.00", "0", "0", "10.00")
        } catch (e: Exception) {
            threw = true
        }
        assertEquals(true, threw, "expected a foreign-key constraint violation for a non-existent sale_id/product_id")
    }

    @Test
    fun settingsUpsertReplacesNotDuplicates() {
        // Real proof of the INSERT OR REPLACE fix for the dialect
        // incompatibility found this milestone (ON CONFLICT DO UPDATE
        // wasn't supported by SQLDelight's default dialect).
        val db = newDatabase()
        db.settingsQueries.upsertSetting(1L, "tax_calculation_mode", "after_discount")
        db.settingsQueries.upsertSetting(1L, "tax_calculation_mode", "before_discount")
        val all = db.settingsQueries.selectAllSettings(1L).executeAsList()
        assertEquals(1, all.size, "upsert must replace, not duplicate, the same (company_id, skey)")
        assertEquals("before_discount", all.first().svalue)
    }
}
