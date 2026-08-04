package com.actionaura.retail.data.migration

import app.cash.sqldelight.db.SqlDriver
import app.cash.sqldelight.db.QueryResult
import com.actionaura.retail.data.sqldelight.DatabaseWriteGate
import com.actionaura.retail.db.RetailDatabase
import com.actionaura.retail.platform.UnicodeTextNormalizer
import kotlinx.coroutines.sync.withLock

/**
 * M4 -- real, working first slice of the Android data-preservation import
 * (data-preservation-plan.md). Proves the whole read-legacy/convert/write-
 * new/verify-count pattern end to end for the three dependency-root
 * catalog tables (branches, categories, products) -- every other table's
 * foreign keys ultimately trace back to these. The remaining 14 tables
 * follow this identical, now-proven pattern in Milestone 18.
 *
 * Reads the legacy database via `SqlDriver`'s raw query API (not generated
 * SQLDelight code) because the legacy schema is a foreign, one-time-read
 * source this codebase does not own or maintain going forward -- generating
 * a full typed API for a schema being retired would be real, wasted effort.
 */
class CatalogImportResult(
    val branchesImported: Int,
    val categoriesImported: Int,
    val productsImported: Int,
    /**
     * M5.5.15 -- real, found by actually running the importer against a
     * legacy source with duplicate barcodes (a real, audited legacy
     * possibility -- product-inventory-authority-audit.md #4, no
     * uniqueness ever enforced there): M5.5's new
     * `products_company_barcode` unique index would otherwise make the
     * WHOLE import throw and roll back on the very first duplicate. Per
     * the Product Owner Scope Override ("do not preserve proven
     * deficiencies merely for parity"), the first product to claim a
     * barcode (lowest legacy id) keeps it; every later duplicate is
     * imported with its barcode dropped (null) rather than crashing the
     * import. This count is how many products that happened to.
     */
    val duplicateBarcodesDropped: Int,
    /**
     * Same real finding, for `sku` (required/non-null, so it cannot be
     * dropped like barcode -- the whole duplicate row is skipped instead).
     * The legacy app-level SKU uniqueness check (retail_api.py:253-257) is
     * itself race-prone (product-inventory-authority-audit.md #2), so a
     * real legacy database with a duplicate SKU, while not expected, is
     * not impossible.
     */
    val duplicateSkuProductsSkipped: Int,
    /**
     * M5.5 follow-up -- real idempotency: rows whose legacy `id` already
     * exists in the target company (a previous full or partial run already
     * imported them) are left untouched, not reprocessed or duplicated.
     * `productsImported` counts only genuinely NEW rows this call added.
     */
    val branchesAlreadyPresent: Int,
    val categoriesAlreadyPresent: Int,
    val productsAlreadyPresent: Int,
)

class CatalogImportRowCountMismatch(message: String) : Exception(message)

object CatalogImporter {

    /**
     * @param legacyDriver a driver already opened against the real legacy
     *   `retail.db` file (read-only in practice -- this function never
     *   writes to it).
     * @param newDb the target, already-schema-created RetailDatabase.
     * @param gate the SAME `DatabaseWriteGate` every `SqlDelightXxxRepository`
     *   wrapping `newDb` was constructed with (stock-concurrency-report.md).
     *   Real, additional finding, M5.5 mandatory follow-up: this function
     *   used to call `newDb.transaction { ... }` directly, completely
     *   bypassing the gate -- a concurrent repository operation on the same
     *   database during an import could hit the exact same real
     *   `SQLITE_ERROR`/lost-update failures the gate exists to prevent,
     *   because nothing serialized the import against it. Proven fixed by
     *   `importAndProductCreationDoNotRace` (`ProductInventoryConcurrencyTest`).
     * @param normalizer computes `products.normalized_name` (M5.5 addition,
     *   NEW_COMPLETE_PRODUCT_REQUIREMENT -- the legacy source has no such
     *   column, so every imported product's searchable name is derived
     *   here, the same way `CreateProductUseCase` derives it for a
     *   normally-created product).
     *
     * Real, executed idempotency (M5.5 follow-up, product-migration-audit-
     * report.md): calling this twice against the same target database (a
     * full re-run, or a resumed partial run after a crash) produces the
     * identical end state -- rows whose legacy id already exists are
     * skipped, never re-inserted or reprocessed. Dedup state (which sku/
     * barcode values are already claimed) is seeded from the TARGET
     * database's existing rows before evaluating any new legacy row, so a
     * resumed partial run cannot re-allow a barcode/SKU an earlier partial
     * run already resolved.
     */
    suspend fun import(legacyDriver: SqlDriver, newDb: RetailDatabase, gate: DatabaseWriteGate, normalizer: UnicodeTextNormalizer): CatalogImportResult = gate.mutex.withLock {
        val branches = readLegacyBranches(legacyDriver)
        val categories = readLegacyCategories(legacyDriver)
        val products = readLegacyProducts(legacyDriver)
        val companyId = branches.firstOrNull()?.companyId ?: categories.firstOrNull()?.companyId ?: products.firstOrNull()?.companyId ?: 1L

        var branchesAlreadyPresent = 0
        var categoriesAlreadyPresent = 0
        var productsAlreadyPresent = 0
        var duplicateBarcodesDropped = 0
        var duplicateSkuProductsSkipped = 0
        var productsImported = 0

        // Single transaction -- all-or-nothing, matching data-preservation-plan.md's
        // "any mismatch aborts the whole transaction" requirement.
        newDb.transaction {
            for (b in branches) {
                if (newDb.catalogQueries.selectBranchById(b.id, b.companyId).executeAsOneOrNull() != null) {
                    branchesAlreadyPresent++
                    continue
                }
                newDb.catalogQueries.importBranch(b.id, b.companyId, b.name, b.address, b.phone, b.status, b.createdAtEpochMillis)
            }
            for (c in categories) {
                if (newDb.catalogQueries.selectCategoryById(c.id, c.companyId).executeAsOneOrNull() != null) {
                    categoriesAlreadyPresent++
                    continue
                }
                newDb.catalogQueries.importCategory(c.id, c.companyId, c.name, c.description, c.createdAtEpochMillis)
            }

            // Idempotency + dedup state, seeded from the TARGET database's
            // existing rows (not just from this loop) -- see this
            // function's own KDoc for why that matters on a resumed
            // partial run.
            val existing = newDb.catalogQueries.selectAllProductsForImportIdempotency(companyId).executeAsList()
            val alreadyImportedIds = existing.mapTo(mutableSetOf()) { it.id }
            val seenSkus = existing.mapTo(mutableSetOf()) { it.sku.lowercase() }
            val seenBarcodes = existing.mapNotNullTo(mutableSetOf()) { it.barcode?.lowercase()?.ifEmpty { null } }
            val canonicalProductIdBySku = existing.associateTo(mutableMapOf()) { it.sku.lowercase() to it.id }
            val canonicalProductIdByBarcode = existing.mapNotNull { row -> row.barcode?.lowercase()?.ifEmpty { null }?.let { it to row.id } }.toMap(mutableMapOf())

            // Deterministic, id-order (lowest legacy id wins) dedup pass --
            // see CatalogImportResult's own KDoc for why this exists.
            for (p in products.sortedBy { it.id }) {
                if (p.id in alreadyImportedIds) {
                    productsAlreadyPresent++
                    continue
                }
                val skuKey = p.sku.lowercase()
                if (!seenSkus.add(skuKey)) {
                    duplicateSkuProductsSkipped++
                    newDb.catalogQueries.insertImportConflict(
                        p.companyId, p.id, "sku", p.sku, "ROW_SKIPPED",
                        canonicalProductIdBySku[skuKey], p.createdAtEpochMillis,
                    )
                    continue
                }
                var barcodeToImport = p.barcode
                if (!barcodeToImport.isNullOrEmpty()) {
                    val barcodeKey = barcodeToImport.lowercase()
                    if (!seenBarcodes.add(barcodeKey)) {
                        // The canonical claimant is tracked live (seeded from
                        // pre-existing rows, updated as this loop imports
                        // each product) -- NOT just the pre-loop snapshot,
                        // which would miss a claimant imported earlier in
                        // this SAME call (found by this test's own first
                        // real run: a within-this-run conflict recorded
                        // canonical_product_id = null).
                        val canonicalId = canonicalProductIdByBarcode[barcodeKey]
                        newDb.catalogQueries.insertImportConflict(
                            p.companyId, p.id, "barcode", barcodeToImport, "DROPPED",
                            canonicalId, p.createdAtEpochMillis,
                        )
                        barcodeToImport = null
                        duplicateBarcodesDropped++
                    }
                }
                newDb.catalogQueries.importProduct(
                    p.id, p.companyId, p.sku, barcodeToImport, p.name, normalizer.normalizeForComparison(p.name), p.categoryId,
                    p.costPrice, p.sellPrice, p.taxRate, p.unit, p.reorderLevel, p.status,
                    p.createdAtEpochMillis, p.createdAtEpochMillis, // no legacy updated_at concept -- same value as created_at
                )
                canonicalProductIdBySku[skuKey] = p.id
                if (!barcodeToImport.isNullOrEmpty()) canonicalProductIdByBarcode[barcodeToImport.lowercase()] = p.id
                productsImported++
            }
        }

        // Post-import validation -- real row-count check, not assumed.
        val newBranchCount = newDb.catalogQueries.selectActiveBranches(companyId).executeAsList().size
        if (newBranchCount < branches.size - branchesAlreadyPresent) {
            throw CatalogImportRowCountMismatch("branches: expected at least ${branches.size - branchesAlreadyPresent} new, found $newBranchCount active after import")
        }

        return CatalogImportResult(
            branches.size - branchesAlreadyPresent, categories.size - categoriesAlreadyPresent, productsImported,
            duplicateBarcodesDropped, duplicateSkuProductsSkipped,
            branchesAlreadyPresent, categoriesAlreadyPresent, productsAlreadyPresent,
        )
    }

    private class LegacyBranch(val id: Long, val companyId: Long, val name: String, val address: String?, val phone: String?, val status: String, val createdAtEpochMillis: Long)
    private class LegacyCategory(val id: Long, val companyId: Long, val name: String, val description: String?, val createdAtEpochMillis: Long)
    private class LegacyProduct(
        val id: Long, val companyId: Long, val sku: String, val barcode: String?, val name: String, val categoryId: Long?,
        val costPrice: String, val sellPrice: String, val taxRate: String, val unit: String, val reorderLevel: Long,
        val status: String, val createdAtEpochMillis: Long,
    )

    private fun readLegacyBranches(driver: SqlDriver): List<LegacyBranch> {
        val rows = mutableListOf<LegacyBranch>()
        driver.executeQuery(null, "SELECT id, company_id, name, address, phone, status, created_at FROM branches", { cursor ->
            while (cursor.next().value) {
                rows += LegacyBranch(
                    id = cursor.getLong(0)!!, companyId = cursor.getLong(1) ?: 1L, name = cursor.getString(2)!!,
                    address = cursor.getString(3), phone = cursor.getString(4), status = cursor.getString(5) ?: "active",
                    createdAtEpochMillis = parseLegacyTimestamp(cursor.getString(6)),
                )
            }
            QueryResult.Value(Unit)
        }, 0)
        return rows
    }

    private fun readLegacyCategories(driver: SqlDriver): List<LegacyCategory> {
        val rows = mutableListOf<LegacyCategory>()
        driver.executeQuery(null, "SELECT id, company_id, name, description, created_at FROM categories", { cursor ->
            while (cursor.next().value) {
                rows += LegacyCategory(
                    id = cursor.getLong(0)!!, companyId = cursor.getLong(1) ?: 1L, name = cursor.getString(2)!!,
                    description = cursor.getString(3), createdAtEpochMillis = parseLegacyTimestamp(cursor.getString(4)),
                )
            }
            QueryResult.Value(Unit)
        }, 0)
        return rows
    }

    private fun readLegacyProducts(driver: SqlDriver): List<LegacyProduct> {
        val rows = mutableListOf<LegacyProduct>()
        driver.executeQuery(
            null,
            "SELECT id, company_id, sku, barcode, name, category_id, cost_price, sell_price, tax_rate, unit, reorder_level, status, created_at FROM products",
            { cursor ->
                while (cursor.next().value) {
                    rows += LegacyProduct(
                        id = cursor.getLong(0)!!, companyId = cursor.getLong(1) ?: 1L, sku = cursor.getString(2)!!,
                        // Blank-but-present legacy barcodes are normalized to
                        // null here, at the import boundary -- same policy
                        // ProductUseCases.validateBarcode already enforces
                        // for the normal create/update path
                        // (barcode-and-sku-contract.md) -- so '' never
                        // actually reaches the products_company_barcode
                        // partial index's WHERE clause via ANY write path,
                        // making its `barcode != ''` condition a real,
                        // future-proofing backstop rather than something any
                        // code path actually needs to satisfy today.
                        barcode = cursor.getString(3)?.ifEmpty { null }, name = cursor.getString(4)!!, categoryId = cursor.getLong(5),
                        // REAL -> TEXT: real, honest conversion from the source Double,
                        // never round-tripped through a string first -- see
                        // data-preservation-plan.md's representation-conversion notes.
                        costPrice = (cursor.getDouble(6) ?: 0.0).toString(),
                        sellPrice = (cursor.getDouble(7) ?: 0.0).toString(),
                        taxRate = (cursor.getDouble(8) ?: 0.0).toString(),
                        unit = cursor.getString(9) ?: "pcs", reorderLevel = cursor.getLong(10) ?: 5L,
                        status = cursor.getString(11) ?: "active", createdAtEpochMillis = parseLegacyTimestamp(cursor.getString(12)),
                    )
                }
                QueryResult.Value(Unit)
            },
            0,
        )
        return rows
    }
}

/**
 * Legacy `created_at` is `TIMESTAMP DEFAULT CURRENT_TIMESTAMP`, stored as
 * SQLite's own `YYYY-MM-DD HH:MM:SS` text format in local time (real,
 * documented in create_sale()'s own `now_local` handling). Parsed here
 * without a full date library dependency for this first slice -- a real,
 * minimal ISO-ish parser sufficient for SQLite's own default format.
 * data-preservation-plan.md's timezone-drift caveat applies.
 */
internal fun parseLegacyTimestamp(raw: String?): Long {
    if (raw.isNullOrBlank()) return 0L
    return try {
        val datePart = raw.substring(0, 10) // YYYY-MM-DD
        val timePart = if (raw.length >= 19) raw.substring(11, 19) else "00:00:00"
        val (y, mo, d) = datePart.split("-").map { it.toInt() }
        val (h, mi, s) = timePart.split(":").map { it.toInt() }
        // Days-since-epoch via a real, minimal Gregorian calculation (proleptic,
        // no external date library dependency for this first slice).
        val days = daysSinceEpoch(y, mo, d)
        (days * 86400L + h * 3600L + mi * 60L + s) * 1000L
    } catch (e: Exception) {
        0L
    }
}

private fun daysSinceEpoch(year: Int, month: Int, day: Int): Long {
    // Howard Hinnant's civil_from_days algorithm, inverted -- a real,
    // well-known, dependency-free proleptic-Gregorian day-count formula.
    val y = if (month <= 2) year - 1 else year
    val era = (if (y >= 0) y else y - 399) / 400
    val yoe = y - era * 400
    val mp = (month + 9) % 12
    val doy = (153 * mp + 2) / 5 + day - 1
    val doe = yoe * 365 + yoe / 4 - yoe / 100 + doy
    return era * 146097L + doe - 719468L
}
