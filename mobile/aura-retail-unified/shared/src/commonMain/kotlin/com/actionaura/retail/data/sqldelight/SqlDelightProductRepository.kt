package com.actionaura.retail.data.sqldelight

import com.actionaura.retail.data.ProductRepository
import com.actionaura.retail.data.model.Product
import com.actionaura.retail.data.model.activeToStatus
import com.actionaura.retail.data.model.statusToActive
import com.actionaura.retail.data.parseStoredMoney
import com.actionaura.retail.data.parseStoredRate
import com.actionaura.retail.db.RetailDatabase
import com.actionaura.retail.db.SelectActiveProducts
import com.actionaura.retail.db.SelectProductByBarcode
import com.actionaura.retail.db.SelectProductById
import com.actionaura.retail.db.SelectProductBySku
import com.actionaura.retail.financial.Money
import com.actionaura.retail.financial.PercentageRate

/** M5.1 -- real, SQLDelight-backed `ProductRepository`. */
class SqlDelightProductRepository(private val db: RetailDatabase) : ProductRepository {

    override suspend fun listActive(companyId: Long): List<Product> =
        db.catalogQueries.selectActiveProducts(companyId).executeAsList().map { it.toDomain() }

    override suspend fun getById(companyId: Long, id: Long): Product? =
        db.catalogQueries.selectProductById(id, companyId).executeAsOneOrNull()?.toDomain()

    override suspend fun getByBarcode(companyId: Long, barcode: String): Product? =
        db.catalogQueries.selectProductByBarcode(barcode, companyId).executeAsOneOrNull()?.toDomain()

    override suspend fun getBySku(companyId: Long, sku: String): Product? =
        db.catalogQueries.selectProductBySku(sku, companyId).executeAsOneOrNull()?.toDomain()

    override suspend fun insert(
        companyId: Long,
        sku: String,
        barcode: String?,
        name: String,
        categoryId: Long?,
        costPrice: Money,
        sellPrice: Money,
        taxRate: PercentageRate,
        unit: String,
        reorderLevel: Long,
        nowEpochMillis: Long,
    ): Product {
        val id = db.transactionWithResult {
            db.catalogQueries.insertProduct(
                companyId, sku, barcode, name, categoryId,
                costPrice.toString(), sellPrice.toString(), taxRate.toString(), unit, reorderLevel, nowEpochMillis,
            )
            db.catalogQueries.lastInsertRowId().executeAsOne()
        }
        return getById(companyId, id) ?: error("product $id vanished immediately after insert")
    }

    override suspend fun setActive(companyId: Long, id: Long, active: Boolean) {
        db.catalogQueries.updateProductStatus(activeToStatus(active), id, companyId)
    }
}

private fun SelectActiveProducts.toDomain() = Product(
    id = id, companyId = company_id, sku = sku, barcode = barcode, name = name,
    categoryId = category_id, categoryName = category_name,
    costPrice = parseStoredMoney(cost_price), sellPrice = parseStoredMoney(sell_price), taxRate = parseStoredRate(tax_rate),
    unit = unit, reorderLevel = reorder_level, isActive = statusToActive(status), createdAtEpochMillis = created_at,
)

private fun SelectProductById.toDomain() = Product(
    id = id, companyId = company_id, sku = sku, barcode = barcode, name = name,
    categoryId = category_id, categoryName = category_name,
    costPrice = parseStoredMoney(cost_price), sellPrice = parseStoredMoney(sell_price), taxRate = parseStoredRate(tax_rate),
    unit = unit, reorderLevel = reorder_level, isActive = statusToActive(status), createdAtEpochMillis = created_at,
)

private fun SelectProductByBarcode.toDomain() = Product(
    id = id, companyId = company_id, sku = sku, barcode = barcode, name = name,
    categoryId = category_id, categoryName = category_name,
    costPrice = parseStoredMoney(cost_price), sellPrice = parseStoredMoney(sell_price), taxRate = parseStoredRate(tax_rate),
    unit = unit, reorderLevel = reorder_level, isActive = statusToActive(status), createdAtEpochMillis = created_at,
)

private fun SelectProductBySku.toDomain() = Product(
    id = id, companyId = company_id, sku = sku, barcode = barcode, name = name,
    categoryId = category_id, categoryName = category_name,
    costPrice = parseStoredMoney(cost_price), sellPrice = parseStoredMoney(sell_price), taxRate = parseStoredRate(tax_rate),
    unit = unit, reorderLevel = reorder_level, isActive = statusToActive(status), createdAtEpochMillis = created_at,
)
