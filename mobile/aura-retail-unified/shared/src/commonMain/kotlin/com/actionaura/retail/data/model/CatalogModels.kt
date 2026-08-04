package com.actionaura.retail.data.model

import com.actionaura.retail.financial.Money
import com.actionaura.retail.financial.PercentageRate

/**
 * M5.1 -- typed, UI-safe read models for the catalog tables. No raw
 * SQLDelight row types, no `Double`/`Float` for money or rate fields
 * (database-schema-contract.md rule 1) ever cross out of the
 * `data.sqldelight` package -- everything above the repository
 * implementation deals only in these.
 */
data class Branch(
    val id: Long,
    val companyId: Long,
    val name: String,
    val address: String?,
    val phone: String?,
    val isActive: Boolean,
    val createdAtEpochMillis: Long,
)

data class Category(
    val id: Long,
    val companyId: Long,
    val name: String,
    val description: String?,
    val isActive: Boolean,
    val productCount: Long,
    val createdAtEpochMillis: Long,
)

data class Product(
    val id: Long,
    val companyId: Long,
    val sku: String,
    val barcode: String?,
    val name: String,
    val categoryId: Long?,
    val categoryName: String?,
    val costPrice: Money,
    val sellPrice: Money,
    val taxRate: PercentageRate,
    val unit: String,
    val reorderLevel: Long,
    val isActive: Boolean,
    val createdAtEpochMillis: Long,
)

internal const val STATUS_ACTIVE = "active"
internal const val STATUS_ARCHIVED = "archived"
internal fun statusToActive(status: String): Boolean = status == STATUS_ACTIVE
internal fun activeToStatus(active: Boolean): String = if (active) STATUS_ACTIVE else STATUS_ARCHIVED
