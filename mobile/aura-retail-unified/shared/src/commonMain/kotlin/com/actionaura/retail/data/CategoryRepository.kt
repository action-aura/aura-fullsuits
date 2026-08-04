package com.actionaura.retail.data

import com.actionaura.retail.data.model.Category

/**
 * M5.1 -- thin, typed plumbing over the categories table. Business rules
 * (duplicate-name detection, Arabic/Unicode normalization, archive/
 * reactivate semantics) deliberately live one layer up, in the Milestone
 * 5.3 use-case, not here -- per the governing spec's required dependency
 * direction: use case -> repository interface -> repository implementation
 * -> SQLDelight query authority.
 */
interface CategoryRepository {
    suspend fun listActive(companyId: Long): List<Category>
    suspend fun getById(companyId: Long, id: Long): Category?
    suspend fun insert(companyId: Long, name: String, description: String?, nowEpochMillis: Long): Category
    suspend fun setActive(companyId: Long, id: Long, active: Boolean)
}
