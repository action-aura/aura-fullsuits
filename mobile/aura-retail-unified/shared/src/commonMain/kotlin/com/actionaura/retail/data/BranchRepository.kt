package com.actionaura.retail.data

import com.actionaura.retail.data.model.Branch

/**
 * M5.1 -- thin, typed plumbing over the branches table. `setActive`
 * enforces exactly one invariant at this layer (not deferred to the
 * Milestone 5.4 use case): a company can never end up with zero active
 * branches. This specific rule needs the same lock-fresh-read-then-write
 * atomicity `SaleRepository` uses for oversell protection (M3's
 * transaction-boundary-audit.md) -- two devices concurrently deactivating
 * two different branches must not both succeed and leave zero active
 * branches. Every other branch business rule (which branch is "current",
 * legacy default-branch backfill) is Milestone 5.4 use-case scope.
 */
interface BranchRepository {
    suspend fun listActive(companyId: Long): List<Branch>
    suspend fun getById(companyId: Long, id: Long): Branch?
    suspend fun insert(companyId: Long, name: String, address: String?, phone: String?, nowEpochMillis: Long): Branch
    suspend fun setActive(companyId: Long, id: Long, active: Boolean): DomainResult<Unit>
    suspend fun countActive(companyId: Long): Long
}
