package com.actionaura.retail.data.sqldelight

import com.actionaura.retail.data.CategoryRepository
import com.actionaura.retail.data.model.Category
import com.actionaura.retail.data.model.activeToStatus
import com.actionaura.retail.data.model.statusToActive
import com.actionaura.retail.db.RetailDatabase
import com.actionaura.retail.db.SelectActiveCategories
import com.actionaura.retail.db.SelectCategoryById
import kotlinx.coroutines.sync.withLock

/**
 * M5.1/M5.5 -- real, SQLDelight-backed `CategoryRepository`.
 *
 * `gate` -- real, confirmed finding (M5.5.14, stock-concurrency-report.md):
 * `JdbcSqliteDriver` wraps a single shared JDBC `Connection`, not safe for
 * concurrent access from multiple real OS threads. A per-repository-
 * instance `Mutex` was the first fix but was only safe under an
 * UNENFORCED assumption (exactly one instance per database) --
 * `DatabaseWriteGate` (M5.5 follow-up) makes the real construction rule
 * explicit and checkable: one gate per `RetailDatabase`, injected into
 * every repository wrapping that database, so real mutual exclusion holds
 * even across multiple repository objects.
 */
class SqlDelightCategoryRepository(private val db: RetailDatabase, private val gate: DatabaseWriteGate) : CategoryRepository {
    private val writeMutex get() = gate.mutex

    override suspend fun listActive(companyId: Long): List<Category> = writeMutex.withLock {
        db.catalogQueries.selectActiveCategories(companyId).executeAsList().map { it.toDomain() }
    }

    override suspend fun getById(companyId: Long, id: Long): Category? = writeMutex.withLock {
        db.catalogQueries.selectCategoryById(id, companyId).executeAsOneOrNull()?.toDomain()
    }

    override suspend fun insert(companyId: Long, name: String, description: String?, nowEpochMillis: Long): Category = writeMutex.withLock {
        val id = db.transactionWithResult {
            db.catalogQueries.insertCategory(companyId, name, description, nowEpochMillis)
            db.catalogQueries.lastInsertRowId().executeAsOne()
        }
        db.catalogQueries.selectCategoryById(id, companyId).executeAsOneOrNull()?.toDomain()
            ?: error("category $id vanished immediately after insert")
    }

    override suspend fun setActive(companyId: Long, id: Long, active: Boolean): Unit = writeMutex.withLock {
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
