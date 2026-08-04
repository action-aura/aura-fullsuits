package com.actionaura.retail.data.migration

import app.cash.sqldelight.db.QueryResult
import app.cash.sqldelight.driver.jdbc.sqlite.JdbcSqliteDriver
import com.actionaura.retail.db.RetailDatabase
import kotlin.test.Test
import kotlin.test.assertEquals

/**
 * M4 -- real, executed proof of the data-preservation import pattern
 * (data-preservation-plan.md), not just a design on paper. Builds a
 * synthetic legacy database using the EXACT real CREATE TABLE statements
 * from products/retail/backend/database/schema.py's branches/categories/
 * products (real REAL columns, real TIMESTAMP default), seeds it with
 * representative rows, runs CatalogImporter against a real target
 * RetailDatabase, and verifies row counts and converted values.
 */
class CatalogImporterTest {

    private fun createSyntheticLegacyDatabase(): JdbcSqliteDriver {
        val driver = JdbcSqliteDriver(JdbcSqliteDriver.IN_MEMORY)
        // Verbatim from schema.py's real CREATE TABLE statements (branches/categories/products).
        driver.execute(null, """
            CREATE TABLE branches (
                id INTEGER PRIMARY KEY AUTOINCREMENT, company_id INTEGER DEFAULT 1, name TEXT NOT NULL,
                address TEXT, phone TEXT, status TEXT DEFAULT 'active', created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """.trimIndent(), 0)
        driver.execute(null, """
            CREATE TABLE categories (
                id INTEGER PRIMARY KEY AUTOINCREMENT, company_id INTEGER DEFAULT 1, name TEXT NOT NULL,
                description TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """.trimIndent(), 0)
        driver.execute(null, """
            CREATE TABLE products (
                id INTEGER PRIMARY KEY AUTOINCREMENT, company_id INTEGER DEFAULT 1, sku TEXT NOT NULL, barcode TEXT,
                name TEXT NOT NULL, category_id INTEGER, cost_price REAL DEFAULT 0, sell_price REAL DEFAULT 0,
                tax_rate REAL DEFAULT 0, unit TEXT DEFAULT 'pcs', reorder_level INTEGER DEFAULT 5,
                status TEXT DEFAULT 'active', created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """.trimIndent(), 0)

        driver.execute(null, "INSERT INTO branches(id, company_id, name, address, phone, status, created_at) VALUES (1, 1, 'Main Branch', '123 St', '555-1234', 'active', '2026-01-15 10:30:00')", 0)
        driver.execute(null, "INSERT INTO categories(id, company_id, name, description, created_at) VALUES (1, 1, 'Beverages', 'Drinks', '2026-01-15 10:31:00')", 0)
        driver.execute(null, "INSERT INTO products(id, company_id, sku, barcode, name, category_id, cost_price, sell_price, tax_rate, unit, reorder_level, status, created_at) " +
            "VALUES (1, 1, 'SKU-001', '000111', 'Cola 330ml', 1, 0.5, 1.99, 10.0, 'can', 24, 'active', '2026-01-15 10:32:00')", 0)
        return driver
    }

    private fun newTargetDatabase(): RetailDatabase {
        val driver = JdbcSqliteDriver(JdbcSqliteDriver.IN_MEMORY)
        driver.execute(null, "PRAGMA foreign_keys=ON", 0)
        RetailDatabase.Schema.create(driver)
        return RetailDatabase(driver)
    }

    @Test
    fun importsRealLegacyShapedDataWithPreservedIdsAndConvertedTypes() {
        val legacyDriver = createSyntheticLegacyDatabase()
        val newDb = newTargetDatabase()

        val result = CatalogImporter.import(legacyDriver, newDb)

        assertEquals(1, result.branchesImported)
        assertEquals(1, result.categoriesImported)
        assertEquals(1, result.productsImported)

        val products = newDb.catalogQueries.selectActiveProducts(1L).executeAsList()
        assertEquals(1, products.size)
        val product = products.first()
        assertEquals("Cola 330ml", product.name)
        assertEquals("1.99", product.sell_price) // REAL 1.99 -> TEXT "1.99", real conversion proof
        assertEquals("Beverages", product.category_name) // FK (category_id=1) resolved correctly -- proves preserved-ID import kept relationships intact

        val branches = newDb.catalogQueries.selectActiveBranches(1L).executeAsList()
        assertEquals("Main Branch", branches.first().name)
    }

    @Test
    fun legacyTimestampParsedToPlausibleEpochMillis() {
        // 2026-01-15 10:30:00 local -- sanity bound, not an exact-timezone
        // assertion (data-preservation-plan.md's own documented caveat).
        val millis = parseLegacyTimestamp("2026-01-15 10:30:00")
        val year2020Millis = 1577836800000L
        val year2030Millis = 1893456000000L
        assert(millis in year2020Millis..year2030Millis) { "parsed timestamp $millis is not a plausible 2026 date" }
    }
}
