package com.actionaura.retail.data

import com.actionaura.retail.data.model.Product
import com.actionaura.retail.financial.Money
import com.actionaura.retail.financial.PercentageRate

/** M5.1 -- thin, typed plumbing over the products table. Pricing/reorder business rules are Milestone 5.5 use-case scope. */
interface ProductRepository {
    suspend fun listActive(companyId: Long): List<Product>
    suspend fun getById(companyId: Long, id: Long): Product?
    suspend fun getByBarcode(companyId: Long, barcode: String): Product?
    suspend fun getBySku(companyId: Long, sku: String): Product?
    suspend fun insert(
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
    ): Product
    suspend fun setActive(companyId: Long, id: Long, active: Boolean)
}
