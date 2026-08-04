package com.actionaura.retail.data.sqldelight

import com.actionaura.retail.data.BranchRepository
import com.actionaura.retail.data.DomainResult
import com.actionaura.retail.data.RepositoryError
import com.actionaura.retail.data.model.Branch
import com.actionaura.retail.data.model.activeToStatus
import com.actionaura.retail.data.model.statusToActive
import com.actionaura.retail.db.Branches
import com.actionaura.retail.db.RetailDatabase

/** M5.1 -- real, SQLDelight-backed `BranchRepository`. */
class SqlDelightBranchRepository(private val db: RetailDatabase) : BranchRepository {

    override suspend fun listActive(companyId: Long): List<Branch> =
        db.catalogQueries.selectActiveBranches(companyId).executeAsList().map { it.toDomain() }

    override suspend fun getById(companyId: Long, id: Long): Branch? =
        db.catalogQueries.selectBranchById(id, companyId).executeAsOneOrNull()?.toDomain()

    override suspend fun insert(companyId: Long, name: String, address: String?, phone: String?, nowEpochMillis: Long): Branch {
        val id = db.transactionWithResult {
            db.catalogQueries.insertBranch(companyId, name, address, phone, nowEpochMillis)
            db.catalogQueries.lastInsertRowId().executeAsOne()
        }
        return getById(companyId, id) ?: error("branch $id vanished immediately after insert")
    }

    override suspend fun setActive(companyId: Long, id: Long, active: Boolean): DomainResult<Unit> {
        if (active) {
            db.catalogQueries.updateBranchStatus(activeToStatus(true), id, companyId)
            return DomainResult.Success(Unit)
        }
        // Deactivation: read the active count and the target's current status
        // inside the same transaction as the write, so two concurrent
        // deactivations of two different branches cannot both read "count=2,
        // safe" and both proceed, leaving zero active branches (the exact
        // TOCTOU shape SaleRepository's oversell protection guards against).
        return db.transactionWithResult {
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

    override suspend fun countActive(companyId: Long): Long =
        db.catalogQueries.countActiveBranches(companyId).executeAsOne()
}

private fun Branches.toDomain() = Branch(
    id = id, companyId = company_id, name = name, address = address, phone = phone,
    isActive = statusToActive(status), createdAtEpochMillis = created_at,
)
