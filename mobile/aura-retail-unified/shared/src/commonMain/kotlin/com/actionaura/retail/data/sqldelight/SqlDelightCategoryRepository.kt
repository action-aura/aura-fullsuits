package com.actionaura.retail.data.sqldelight

import com.actionaura.retail.data.CategoryRepository
import com.actionaura.retail.data.model.Category
import com.actionaura.retail.data.model.activeToStatus
import com.actionaura.retail.data.model.statusToActive
import com.actionaura.retail.db.RetailDatabase
import com.actionaura.retail.db.SelectActiveCategories
import com.actionaura.retail.db.SelectCategoryById

/** M5.1 -- real, SQLDelight-backed `CategoryRepository`. */
class SqlDelightCategoryRepository(private val db: RetailDatabase) : CategoryRepository {

    override suspend fun listActive(companyId: Long): List<Category> =
        db.catalogQueries.selectActiveCategories(companyId).executeAsList().map { it.toDomain() }

    override suspend fun getById(companyId: Long, id: Long): Category? =
        db.catalogQueries.selectCategoryById(id, companyId).executeAsOneOrNull()?.toDomain()

    override suspend fun insert(companyId: Long, name: String, description: String?, nowEpochMillis: Long): Category {
        val id = db.transactionWithResult {
            db.catalogQueries.insertCategory(companyId, name, description, nowEpochMillis)
            db.catalogQueries.lastInsertRowId().executeAsOne()
        }
        return getById(companyId, id) ?: error("category $id vanished immediately after insert")
    }

    override suspend fun setActive(companyId: Long, id: Long, active: Boolean) {
        db.catalogQueries.updateCategoryStatus(activeToStatus(active), id, companyId)
    }
}

private fun SelectActiveCategories.toDomain() = Category(
    id = id, companyId = company_id, name = name, description = description,
    isActive = statusToActive(status), productCount = product_count, createdAtEpochMillis = created_at,
)

private fun SelectCategoryById.toDomain() = Category(
    id = id, companyId = company_id, name = name, description = description,
    isActive = statusToActive(status), productCount = product_count, createdAtEpochMillis = created_at,
)
