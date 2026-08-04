package com.actionaura.retail.data.sqldelight

import com.actionaura.retail.data.BranchRepository
import com.actionaura.retail.data.DomainResult
import com.actionaura.retail.data.RepositoryError
import com.actionaura.retail.data.model.Branch
import com.actionaura.retail.data.model.activeToStatus
import com.actionaura.retail.data.model.statusToActive
import com.actionaura.retail.db.Branches
import com.actionaura.retail.db.RetailDatabase
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock

/** M5.1/M5.5 -- real, SQLDelight-backed `BranchRepository`. `writeMutex`: see SqlDelightCategoryRepository's KDoc (stock-concurrency-report.md's real finding). */
class SqlDelightBranchRepository(private val db: RetailDatabase) : BranchRepository {
    private val writeMutex = Mutex()

    override suspend fun listActive(companyId: Long): List<Branch> = writeMutex.withLock {
        db.catalogQueries.selectActiveBranches(companyId).executeAsList().map { it.toDomain() }
    }

    override suspend fun getById(companyId: Long, id: Long): Branch? = writeMutex.withLock {
        db.catalogQueries.selectBranchById(id, companyId).executeAsOneOrNull()?.toDomain()
    }

    override suspend fun insert(companyId: Long, name: String, address: String?, phone: String?, nowEpochMillis: Long): Branch = writeMutex.withLock {
        val id = db.transactionWithResult {
            db.catalogQueries.insertBranch(companyId, name, address, phone, nowEpochMillis)
            db.catalogQueries.lastInsertRowId().executeAsOne()
        }
        db.catalogQueries.selectBranchById(id, companyId).executeAsOneOrNull()?.toDomain()
            ?: error("branch $id vanished immediately after insert")
    }

    override suspend fun setActive(companyId: Long, id: Long, active: Boolean): DomainResult<Unit> = writeMutex.withLock {
        if (active) {
            db.catalogQueries.updateBranchStatus(activeToStatus(true), id, companyId)
            return@withLock DomainResult.Success(Unit)
        }
        // Deactivation: read the active count and the target's current status
        // inside the same transaction as the write, so two concurrent
        // deactivations of two different branches cannot both read "count=2,
        // safe" and both proceed, leaving zero active branches (the exact
        // TOCTOU shape SaleRepository's oversell protection guards against).
        // writeMutex already excludes real cross-thread interleaving here --
        // the transaction wrap is kept anyway as the correctness boundary
        // that does not depend on the mutex (e.g. if this repository is ever
        // constructed twice against the same db).
        db.transactionWithResult {
            val target = db.catalogQueries.selectBranchById(id, companyId).executeAsOneOrNull()
            if (target == null || !statusToActive(target.status)) {
                // Already inactive (or doesn't exist) -- no-op, not an error;
                // matches idempotent-retry expectations elsewhere in this codebase.
                return@transactionWithResult DomainResult.Success(Unit)
            }
            val activeCount = db.catalogQueries.countActiveBranches(companyId).executeAsOne()
            if (activeCount <= 1L) {
                DomainResult.Failure(RepositoryError.LastActiveProtected("branch", id.toString()))
            } else {
                db.catalogQueries.updateBranchStatus(activeToStatus(false), id, companyId)
                DomainResult.Success(Unit)
            }
        }
    }

    override suspend fun countActive(companyId: Long): Long = writeMutex.withLock {
        db.catalogQueries.countActiveBranches(companyId).executeAsOne()
    }
}

private fun Branches.toDomain() = Branch(
    id = id, companyId = company_id, name = name, address = address, phone = phone,
    isActive = statusToActive(status), createdAtEpochMillis = created_at,
)
