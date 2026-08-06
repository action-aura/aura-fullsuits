package com.actionaura.retail.data.sqldelight

import com.actionaura.retail.data.DomainResult
import com.actionaura.retail.data.LowStockProduct
import com.actionaura.retail.data.ProductRepository
import com.actionaura.retail.data.RepositoryError
import com.actionaura.retail.data.StockMovementReason
import com.actionaura.retail.data.model.Product
import com.actionaura.retail.data.model.activeToStatus
import com.actionaura.retail.data.model.statusToActive
import com.actionaura.retail.data.parseStoredMoney
import com.actionaura.retail.data.parseStoredQuantity
import com.actionaura.retail.data.parseStoredRate
import com.actionaura.retail.db.RetailDatabase
import com.actionaura.retail.db.SearchActiveProductsByNormalizedNamePrefix
import com.actionaura.retail.db.SelectActiveProducts
import com.actionaura.retail.db.SelectLowStockProducts
import com.actionaura.retail.db.SelectProductByBarcode
import com.actionaura.retail.db.SelectProductById
import com.actionaura.retail.db.SelectProductBySku
import com.actionaura.retail.financial.Money
import com.actionaura.retail.financial.PercentageRate
import com.actionaura.retail.financial.Quantity
import kotlinx.coroutines.sync.withLock

/** M5.1/M5.5 -- real, SQLDelight-backed `ProductRepository`. `gate`: see SqlDelightCategoryRepository's KDoc (DatabaseWriteGate.kt's real construction-rule fix). */
class SqlDelightProductRepository(private val db: RetailDatabase, private val gate: DatabaseWriteGate) : ProductRepository {
    private val writeMutex get() = gate.mutex

    override suspend fun listActive(companyId: Long): List<Product> = writeMutex.withLock {
        db.catalogQueries.selectActiveProducts(companyId).executeAsList().map { it.toDomain() }
    }

    override suspend fun getById(companyId: Long, id: Long): Product? = writeMutex.withLock {
        db.catalogQueries.selectProductById(id, companyId).executeAsOneOrNull()?.toDomain()
    }

    override suspend fun getByBarcode(companyId: Long, barcode: String): Product? = writeMutex.withLock {
        db.catalogQueries.selectProductByBarcode(barcode, companyId).executeAsOneOrNull()?.toDomain()
    }

    override suspend fun getBySku(companyId: Long, sku: String): Product? = writeMutex.withLock {
        db.catalogQueries.selectProductBySku(sku, companyId).executeAsOneOrNull()?.toDomain()
    }

    override suspend fun searchActiveByNormalizedNamePrefix(companyId: Long, normalizedPrefix: String, limit: Long): List<Product> = writeMutex.withLock {
        db.catalogQueries.searchActiveProductsByNormalizedNamePrefix(companyId, normalizedPrefix, limit).executeAsList().map { it.toDomain() }
    }

    override suspend fun listLowStock(companyId: Long): List<LowStockProduct> = writeMutex.withLock {
        db.catalogQueries.selectLowStockProducts(companyId, companyId).executeAsList().map {
            LowStockProduct(it.toDomain(), parseStoredQuantity(it.total_on_hand))
        }
    }

    override suspend fun insert(
        companyId: Long,
        sku: String,
        barcode: String?,
        name: String,
        normalizedName: String,
        categoryId: String?,
        costPrice: Money,
        sellPrice: Money,
        taxRate: PercentageRate,
        unit: String,
        reorderLevel: Long,
        nowEpochMillis: Long,
    ): DomainResult<Product> = writeMutex.withLock {
        db.transactionWithResult {
            if (db.catalogQueries.selectProductBySku(sku, companyId).executeAsOneOrNull() != null) {
                return@transactionWithResult DomainResult.Failure(RepositoryError.DuplicateValue("product", "sku", sku))
            }
            if (!barcode.isNullOrEmpty() && db.catalogQueries.selectProductByBarcodeAnyStatus(barcode, companyId).executeAsOneOrNull() != null) {
                return@transactionWithResult DomainResult.Failure(RepositoryError.DuplicateValue("product", "barcode", barcode))
            }
            db.catalogQueries.insertProduct(
                companyId, sku, barcode, name, normalizedName, categoryId,
                costPrice.toString(), sellPrice.toString(), taxRate.toString(), unit, reorderLevel,
                nowEpochMillis, nowEpochMillis,
            )
            val id = db.catalogQueries.lastInsertRowId().executeAsOne()
            val created = db.catalogQueries.selectProductById(id, companyId).executeAsOne()
            DomainResult.Success(created.toDomain())
        }
    }

    override suspend fun update(
        companyId: Long,
        id: Long,
        barcode: String?,
        name: String,
        normalizedName: String,
        categoryId: String?,
        costPrice: Money,
        sellPrice: Money,
        taxRate: PercentageRate,
        unit: String,
        reorderLevel: Long,
        expectedUpdatedAtEpochMillis: Long,
        nowEpochMillis: Long,
    ): DomainResult<Product> = writeMutex.withLock {
        db.transactionWithResult {
            if (!barcode.isNullOrEmpty()) {
                val conflict = db.catalogQueries.selectProductByBarcodeAnyStatus(barcode, companyId).executeAsOneOrNull()
                if (conflict != null && conflict.id != id) {
                    return@transactionWithResult DomainResult.Failure(RepositoryError.DuplicateValue("product", "barcode", barcode))
                }
            }
            db.catalogQueries.updateProduct(
                barcode, name, normalizedName, categoryId,
                costPrice.toString(), sellPrice.toString(), taxRate.toString(), unit, reorderLevel, nowEpochMillis,
                id, companyId, expectedUpdatedAtEpochMillis,
            )
            val changed = db.catalogQueries.changes().executeAsOne()
            if (changed == 0L) {
                val current = db.catalogQueries.selectProductById(id, companyId).executeAsOneOrNull()
                return@transactionWithResult if (current == null) {
                    DomainResult.Failure(RepositoryError.NotFound("product", id.toString()))
                } else {
                    DomainResult.Failure(RepositoryError.StaleUpdate("product", id.toString()))
                }
            }
            val updated = db.catalogQueries.selectProductById(id, companyId).executeAsOne()
            DomainResult.Success(updated.toDomain())
        }
    }

    override suspend fun setActive(companyId: Long, id: Long, active: Boolean, nowEpochMillis: Long): Unit = writeMutex.withLock {
        db.catalogQueries.updateProductStatus(activeToStatus(active), nowEpochMillis, id, companyId)
    }

    override suspend fun insertWithInitialStock(
        companyId: Long,
        sku: String,
        barcode: String?,
        name: String,
        normalizedName: String,
        categoryId: String?,
        costPrice: Money,
        sellPrice: Money,
        taxRate: PercentageRate,
        unit: String,
        reorderLevel: Long,
        branchId: Long,
        initialStock: Quantity,
        createdBy: String,
        idempotencyKey: String?,
        nowEpochMillis: Long,
    ): DomainResult<Product> = writeMutex.withLock {
        db.transactionWithResult {
            val existingBySku = db.catalogQueries.selectProductBySku(sku, companyId).executeAsOneOrNull()
            if (existingBySku != null) {
                if (idempotencyKey != null) {
                    val existingMovement = db.inventoryQueries.selectMovementByIdempotencyKey(companyId, idempotencyKey).executeAsOneOrNull()
                    if (existingMovement != null && existingMovement.product_id == existingBySku.id) {
                        return@transactionWithResult DomainResult.Success(existingBySku.toDomain())
                    }
                }
                return@transactionWithResult DomainResult.Failure(RepositoryError.DuplicateValue("product", "sku", sku))
            }
            if (!barcode.isNullOrEmpty() && db.catalogQueries.selectProductByBarcodeAnyStatus(barcode, companyId).executeAsOneOrNull() != null) {
                return@transactionWithResult DomainResult.Failure(RepositoryError.DuplicateValue("product", "barcode", barcode))
            }

            db.catalogQueries.insertProduct(
                companyId, sku, barcode, name, normalizedName, categoryId,
                costPrice.toString(), sellPrice.toString(), taxRate.toString(), unit, reorderLevel,
                nowEpochMillis, nowEpochMillis,
            )
            val id = db.catalogQueries.lastInsertRowId().executeAsOne()

            // LEGACY_PARITY (product-inventory-authority-audit.md #2): only
            // record an opening-stock movement when there IS an opening
            // quantity, matching create_product()'s own "only if
            // initial_stock>0" behavior exactly.
            db.inventoryQueries.upsertOpeningStock(companyId, id, branchId, "0")
            if (initialStock.isPositive()) {
                db.inventoryQueries.decrementStock(initialStock.toString(), companyId, id, branchId)
                db.inventoryQueries.insertMovement(
                    companyId, id, branchId, StockMovementReason.INITIAL_STOCK.name,
                    initialStock.toString(), "0", initialStock.toString(),
                    null, null, null, idempotencyKey, null, createdBy, nowEpochMillis,
                )
            }

            val created = db.catalogQueries.selectProductById(id, companyId).executeAsOne()
            DomainResult.Success(created.toDomain())
        }
    }
}

private fun SelectActiveProducts.toDomain() = Product(
    id = id, companyId = company_id, sku = sku, barcode = barcode, name = name,
    categoryId = category_id, categoryName = category_name,
    costPrice = parseStoredMoney(cost_price), sellPrice = parseStoredMoney(sell_price), taxRate = parseStoredRate(tax_rate),
    unit = unit, reorderLevel = reorder_level, isActive = statusToActive(status),
    createdAtEpochMillis = created_at, updatedAtEpochMillis = updated_at,
)

private fun SelectProductById.toDomain() = Product(
    id = id, companyId = company_id, sku = sku, barcode = barcode, name = name,
    categoryId = category_id, categoryName = category_name,
    costPrice = parseStoredMoney(cost_price), sellPrice = parseStoredMoney(sell_price), taxRate = parseStoredRate(tax_rate),
    unit = unit, reorderLevel = reorder_level, isActive = statusToActive(status),
    createdAtEpochMillis = created_at, updatedAtEpochMillis = updated_at,
)

private fun SelectProductByBarcode.toDomain() = Product(
    id = id, companyId = company_id, sku = sku, barcode = barcode, name = name,
    categoryId = category_id, categoryName = category_name,
    costPrice = parseStoredMoney(cost_price), sellPrice = parseStoredMoney(sell_price), taxRate = parseStoredRate(tax_rate),
    unit = unit, reorderLevel = reorder_level, isActive = statusToActive(status),
    createdAtEpochMillis = created_at, updatedAtEpochMillis = updated_at,
)

private fun SelectProductBySku.toDomain() = Product(
    id = id, companyId = company_id, sku = sku, barcode = barcode, name = name,
    categoryId = category_id, categoryName = category_name,
    costPrice = parseStoredMoney(cost_price), sellPrice = parseStoredMoney(sell_price), taxRate = parseStoredRate(tax_rate),
    unit = unit, reorderLevel = reorder_level, isActive = statusToActive(status),
    createdAtEpochMillis = created_at, updatedAtEpochMillis = updated_at,
)

private fun SearchActiveProductsByNormalizedNamePrefix.toDomain() = Product(
    id = id, companyId = company_id, sku = sku, barcode = barcode, name = name,
    categoryId = category_id, categoryName = category_name,
    costPrice = parseStoredMoney(cost_price), sellPrice = parseStoredMoney(sell_price), taxRate = parseStoredRate(tax_rate),
    unit = unit, reorderLevel = reorder_level, isActive = statusToActive(status),
    createdAtEpochMillis = created_at, updatedAtEpochMillis = updated_at,
)

private fun SelectLowStockProducts.toDomain() = Product(
    id = id, companyId = company_id, sku = sku, barcode = barcode, name = name,
    categoryId = category_id, categoryName = category_name,
    costPrice = parseStoredMoney(cost_price), sellPrice = parseStoredMoney(sell_price), taxRate = parseStoredRate(tax_rate),
    unit = unit, reorderLevel = reorder_level, isActive = statusToActive(status),
    createdAtEpochMillis = created_at, updatedAtEpochMillis = updated_at,
)
