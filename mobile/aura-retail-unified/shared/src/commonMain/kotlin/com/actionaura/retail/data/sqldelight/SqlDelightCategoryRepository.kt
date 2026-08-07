package com.actionaura.retail.data.sqldelight

import com.actionaura.retail.data.CategoryRepository
import com.actionaura.retail.data.model.Category
import com.actionaura.retail.data.model.activeToStatus
import com.actionaura.retail.data.model.statusToActive
import com.actionaura.retail.db.RetailDatabase
import com.actionaura.retail.db.SelectActiveCategories
import com.actionaura.retail.db.SelectCategoryById
import kotlinx.coroutines.sync.withLock
import kotlinx.datetime.Clock
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import kotlin.uuid.ExperimentalUuidApi
import kotlin.uuid.Uuid

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

    override suspend fun getById(companyId: Long, id: String): Category? = writeMutex.withLock {
        db.catalogQueries.selectCategoryById(id, companyId).executeAsOneOrNull()?.toDomain()
    }

    // M-sync -- id is now a client-generated UUID string (Catalog.sq's own
    // KDoc), not SQLite's rowid: `kotlin.uuid.Uuid` is this module's own
    // Kotlin 2.0.21 stdlib UUID generator (Experimental in this Kotlin
    // version, stable from 2.1.20) -- no existing UUID-generation utility
    // was found anywhere else in commonMain (only `java.util.UUID`, an
    // Android-only JVM type, appears in androidMain), so the stdlib is the
    // real, dependency-free choice here rather than adding a new library.
    // Task 10 (multi-device-sync-foundation) -- every mutating write to a
    // synced entity (categories, starting here) additionally appends a
    // `syncOutbox` row inside the SAME transaction as the business write
    // (Sync.sq's own header comment, this task's own required invariant).
    // `insert`'s outbox write joins the SAME `db.transactionWithResult`
    // block M-sync already established for the category row itself --
    // never a second, separate transaction -- so a crash between the two
    // writes is structurally impossible.
    @OptIn(ExperimentalUuidApi::class)
    override suspend fun insert(companyId: Long, name: String, description: String?, nowEpochMillis: Long): Category = writeMutex.withLock {
        val newId = Uuid.random().toString()
        db.transactionWithResult {
            db.catalogQueries.insertCategory(newId, companyId, name, description, nowEpochMillis)
            db.syncQueries.insertOutboxEvent(
                Uuid.random().toString(), "category", newId, "create",
                categoryPayload(newId, companyId, name, description).toString(),
                nowEpochMillis,
            )
        }
        db.catalogQueries.selectCategoryById(newId, companyId).executeAsOneOrNull()?.toDomain()
            ?: error("category $newId vanished immediately after insert")
    }

    // Task 10 -- `active = false` (archive) maps to `event_type = "delete"`;
    // `active = true` (reactivate) maps to `event_type = "update"` carrying
    // the row's current full state -- per the plan's documented per-platform
    // delete semantics (mobile's soft-archive IS this platform's "delete",
    // Sync.sq's own header comment). Both the status write and the outbox
    // write now happen inside one `db.transaction` -- the M5.1-era version
    // of this method had no transaction at all (a single UPDATE needed
    // none); adding a second write here is exactly why one is now required.
    @OptIn(ExperimentalUuidApi::class)
    override suspend fun setActive(companyId: Long, id: String, active: Boolean): Unit = writeMutex.withLock {
        val nowMillis = Clock.System.now().toEpochMilliseconds()
        db.transaction {
            db.catalogQueries.updateCategoryStatus(activeToStatus(active), id, companyId)
            val payload = if (active) {
                // Reactivate -> "update": the row's current full state, matching the brief exactly.
                val row = db.catalogQueries.selectCategoryById(id, companyId).executeAsOneOrNull()
                    ?: error("category $id vanished immediately after reactivation")
                categoryPayload(id, companyId, row.name, row.description)
            } else {
                // Archive -> "delete": minimal payload (just the id), matching desktop's own
                // established delete-event shape (task-5-report.md) -- applyEvent never needs
                // more than the id to archive a row on another device.
                buildJsonObject { put("id", id); put("company_id", companyId) }
            }
            db.syncQueries.insertOutboxEvent(
                Uuid.random().toString(), "category", id, if (active) "update" else "delete",
                payload.toString(), nowMillis,
            )
        }
    }
}

private fun categoryPayload(id: String, companyId: Long, name: String, description: String?) = buildJsonObject {
    put("id", id)
    put("company_id", companyId)
    put("name", name)
    put("description", description)
}

private fun SelectActiveCategories.toDomain() = Category(
    id = id, companyId = company_id, name = name, description = description,
    isActive = statusToActive(status), productCount = product_count, createdAtEpochMillis = created_at,
)

private fun SelectCategoryById.toDomain() = Category(
    id = id, companyId = company_id, name = name, description = description,
    isActive = statusToActive(status), productCount = product_count, createdAtEpochMillis = created_at,
)
