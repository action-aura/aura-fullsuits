package com.actionaura.retail.data.migration

import app.cash.sqldelight.db.QueryResult
import app.cash.sqldelight.driver.jdbc.sqlite.JdbcSqliteDriver
import com.actionaura.retail.data.sqldelight.DatabaseWriteGate
import com.actionaura.retail.db.RetailDatabase
import com.actionaura.retail.platform.AndroidUnicodeTextNormalizer
import kotlinx.coroutines.test.runTest
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
    fun importsRealLegacyShapedDataWithPreservedIdsAndConvertedTypes() = runTest {
        val legacyDriver = createSyntheticLegacyDatabase()
        val newDb = newTargetDatabase()
        val gate = DatabaseWriteGate()

        val result = CatalogImporter.import(legacyDriver, newDb, gate, AndroidUnicodeTextNormalizer())

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
    fun legacySourceWithDuplicateBarcodesAcrossProductsImportsWithoutThrowing() = runTest {
        // M5.5.15 -- real check, not assumed: the legacy authority permits
        // duplicate barcodes across products (product-inventory-authority-
        // audit.md #4, no uniqueness enforced), but M5.5 added a REAL
        // products_company_barcode unique index to the unified schema.
        // importProduct is a plain INSERT with no duplicate handling of its
        // own -- the FIRST real run of this test (before CatalogImporter's
        // own dedup pass existed) actually threw a real SQLiteException and
        // rolled back the whole import. Fixed in CatalogImporter itself
        // (CatalogImportResult's own KDoc); this test proves the fix.
        val legacyDriver = createSyntheticLegacyDatabase()
        legacyDriver.execute(
            null,
            "INSERT INTO products(id, company_id, sku, barcode, name, category_id, cost_price, sell_price, tax_rate, unit, reorder_level, status, created_at) " +
                "VALUES (2, 1, 'SKU-002', '000111', 'Cola 500ml', 1, 0.6, 2.49, 10.0, 'can', 12, 'active', '2026-01-15 10:33:00')",
            0,
        )
        val newDb = newTargetDatabase()
        val gate = DatabaseWriteGate()

        val result = CatalogImporter.import(legacyDriver, newDb, gate, AndroidUnicodeTextNormalizer())
        assertEquals(2, result.productsImported)
        assertEquals(1, result.duplicateBarcodesDropped)

        val products = newDb.catalogQueries.selectActiveProducts(1L).executeAsList().sortedBy { it.id }
        assertEquals("000111", products[0].barcode, "the first (lowest-id) claimant keeps the barcode")
        assertEquals(null, products[1].barcode, "the later duplicate's barcode is dropped, not silently duplicated")
    }

    @Test
    fun legacySourceWithDuplicateSkusSkipsTheLaterRowRatherThanThrowing() = runTest {
        val legacyDriver = createSyntheticLegacyDatabase()
        legacyDriver.execute(
            null,
            "INSERT INTO products(id, company_id, sku, barcode, name, category_id, cost_price, sell_price, tax_rate, unit, reorder_level, status, created_at) " +
                "VALUES (2, 1, 'SKU-001', '000222', 'Cola Duplicate SKU', 1, 0.6, 2.49, 10.0, 'can', 12, 'active', '2026-01-15 10:33:00')",
            0,
        )
        val newDb = newTargetDatabase()
        val gate = DatabaseWriteGate()

        val result = CatalogImporter.import(legacyDriver, newDb, gate, AndroidUnicodeTextNormalizer())
        assertEquals(1, result.productsImported)
        assertEquals(1, result.duplicateSkuProductsSkipped)
        assertEquals(1, newDb.catalogQueries.selectActiveProducts(1L).executeAsList().size)
    }

    @Test
    fun reRunningImportIsIdempotentAndCreatesNoDuplicates() = runTest {
        // M5.5 follow-up -- real proof: running import() TWICE against the
        // SAME target database (a full re-run, or a resumed partial run)
        // must produce the identical end state, not duplicate rows or throw.
        val legacyDriver = createSyntheticLegacyDatabase()
        val newDb = newTargetDatabase()
        val gate = DatabaseWriteGate()

        val first = CatalogImporter.import(legacyDriver, newDb, gate, AndroidUnicodeTextNormalizer())
        assertEquals(1, first.productsImported)
        assertEquals(0, first.productsAlreadyPresent)

        val second = CatalogImporter.import(legacyDriver, newDb, gate, AndroidUnicodeTextNormalizer())
        assertEquals(0, second.productsImported, "nothing new to import on the second run")
        assertEquals(1, second.productsAlreadyPresent, "the previously-imported product must be recognized as already present, not reprocessed")
        assertEquals(1, second.branchesAlreadyPresent)
        assertEquals(1, second.categoriesAlreadyPresent)

        assertEquals(1, newDb.catalogQueries.selectActiveProducts(1L).executeAsList().size, "exactly one product row must exist after two import runs, not two")
        assertEquals(1, newDb.catalogQueries.selectActiveBranches(1L).executeAsList().size)
    }

    @Test
    fun duplicateBarcodeConflictIsRecordedInTheAuditTrail() = runTest {
        // "creates an auditable migration mapping" / "records the original
        // conflicting value" / "records the resulting canonical value" --
        // real, durable rows in import_conflicts, not just an in-memory
        // CatalogImportResult that vanishes once the caller discards it.
        val legacyDriver = createSyntheticLegacyDatabase()
        legacyDriver.execute(
            null,
            "INSERT INTO products(id, company_id, sku, barcode, name, category_id, cost_price, sell_price, tax_rate, unit, reorder_level, status, created_at) " +
                "VALUES (2, 1, 'SKU-002', '000111', 'Cola 500ml', 1, 0.6, 2.49, 10.0, 'can', 12, 'active', '2026-01-15 10:33:00')",
            0,
        )
        val newDb = newTargetDatabase()
        val gate = DatabaseWriteGate()
        CatalogImporter.import(legacyDriver, newDb, gate, AndroidUnicodeTextNormalizer())

        val conflicts = newDb.catalogQueries.selectImportConflicts(1L).executeAsList()
        assertEquals(1, conflicts.size)
        val conflict = conflicts.first()
        assertEquals(2L, conflict.legacy_product_id, "the SECOND (later) claimant is the one recorded as conflicting")
        assertEquals("barcode", conflict.field_)
        assertEquals("000111", conflict.original_value, "the original conflicting value survives in the audit trail even though it was dropped from the product row")
        assertEquals("DROPPED", conflict.resolution)
        assertEquals(1L, conflict.canonical_product_id, "the canonical (winning) product id is recorded")
    }

    @Test
    fun duplicateSkuConflictIsRecordedInTheAuditTrail() = runTest {
        val legacyDriver = createSyntheticLegacyDatabase()
        legacyDriver.execute(
            null,
            "INSERT INTO products(id, company_id, sku, barcode, name, category_id, cost_price, sell_price, tax_rate, unit, reorder_level, status, created_at) " +
                "VALUES (2, 1, 'SKU-001', '000222', 'Cola Duplicate SKU', 1, 0.6, 2.49, 10.0, 'can', 12, 'active', '2026-01-15 10:33:00')",
            0,
        )
        val newDb = newTargetDatabase()
        val gate = DatabaseWriteGate()
        CatalogImporter.import(legacyDriver, newDb, gate, AndroidUnicodeTextNormalizer())

        val conflicts = newDb.catalogQueries.selectImportConflicts(1L).executeAsList()
        assertEquals(1, conflicts.size)
        assertEquals("sku", conflicts.first().field_)
        assertEquals("SKU-001", conflicts.first().original_value)
        assertEquals("ROW_SKIPPED", conflicts.first().resolution)
        assertEquals(1L, conflicts.first().canonical_product_id)
    }

    @Test
    fun resumedPartialImportDoesNotReallowABarcodeAlreadyClaimedByAnEarlierRun() = runTest {
        // Real proof dedup state is seeded from the TARGET database, not
        // just from this loop's own in-memory set -- simulates: product 1
        // (barcode 000111) was already imported by an earlier, separate
        // run; THIS run imports the full legacy set, including product 1
        // again (idempotent skip) and a NEW product 2 sharing the same
        // barcode. Product 2's barcode must still be dropped, even though
        // product 1 was never processed by THIS call's own loop.
        val legacyDriver = createSyntheticLegacyDatabase()
        legacyDriver.execute(
            null,
            "INSERT INTO products(id, company_id, sku, barcode, name, category_id, cost_price, sell_price, tax_rate, unit, reorder_level, status, created_at) " +
                "VALUES (2, 1, 'SKU-002', '000111', 'Cola 500ml', 1, 0.6, 2.49, 10.0, 'can', 12, 'active', '2026-01-15 10:33:00')",
            0,
        )
        val newDb = newTargetDatabase()
        val gate = DatabaseWriteGate()
        // Simulate "product 1 already imported by an earlier run" directly,
        // bypassing CatalogImporter -- exactly what a real prior partial
        // run would have left behind.
        newDb.catalogQueries.importProduct(1L, 1L, "SKU-001", "000111", "Cola 330ml", "cola 330ml", null, "0.50", "1.99", "10", "can", 24, "active", 1000L, 1000L)

        val result = CatalogImporter.import(legacyDriver, newDb, gate, AndroidUnicodeTextNormalizer())
        assertEquals(1, result.productsAlreadyPresent, "product 1 recognized as already present")
        assertEquals(1, result.productsImported, "only product 2 is genuinely new")
        assertEquals(1, result.duplicateBarcodesDropped, "product 2's barcode must still be dropped -- it collides with product 1's, even though product 1 wasn't reprocessed this call")

        val product2 = newDb.catalogQueries.selectProductById(2L, 1L).executeAsOne()
        assertEquals(null, product2.barcode)
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
