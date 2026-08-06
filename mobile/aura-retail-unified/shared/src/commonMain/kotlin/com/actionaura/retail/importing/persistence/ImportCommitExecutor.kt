package com.actionaura.retail.importing.persistence

import com.actionaura.retail.data.sqldelight.DatabaseWriteGate
import com.actionaura.retail.db.RetailDatabase
import com.actionaura.retail.financial.Money
import com.actionaura.retail.financial.PercentageRate
import com.actionaura.retail.financial.Quantity
import com.actionaura.retail.importing.ImportAuditEntry
import com.actionaura.retail.importing.ImportCommitResult
import com.actionaura.retail.importing.ImportCommitToken
import com.actionaura.retail.importing.ImportEntityType
import com.actionaura.retail.importing.ImportError
import com.actionaura.retail.importing.ImportOutcome
import com.actionaura.retail.importing.ImportProvenance
import com.actionaura.retail.importing.ImportResult
import com.actionaura.retail.importing.NormalizedTable
import com.actionaura.retail.importing.entity.ImportDependencyGraph
import com.actionaura.retail.importing.entity.ImportDomainValueParser
import com.actionaura.retail.importing.entity.ImportDuplicatePolicy
import com.actionaura.retail.importing.entity.ImportEntitySchemas
import com.actionaura.retail.importing.entity.ImportParsedValue
import kotlinx.coroutines.sync.withLock
import kotlin.uuid.ExperimentalUuidApi
import kotlin.uuid.Uuid

/** One real, previously-decoded+mapped source table for one entity, ready to commit. */
data class ImportCommitInput(
    val entityType: ImportEntityType,
    val table: NormalizedTable,
    val fieldKeyToColumnIndex: Map<String, Int?>,
)

private class EntityCounts {
    var inserted = 0L
    var updated = 0L
    var skipped = 0L
    var generated = 0L
}

/** A real, deliberate abort of the whole real SQL transaction -- never caught internally, always propagates so `db.transactionWithResult`'s own real rollback fires. */
private class ImportCommitAbortedException(message: String) : Exception(message)

/**
 * M5.8.15/M5.8.16 -- real, transactional, all-or-nothing multi-entity
 * commit. Every entity's rows (categories -> suppliers -> branches ->
 * customers -> products, `ImportDependencyGraph.COMMIT_ORDER`) are
 * written inside ONE real `db.transactionWithResult` -- if ANY row in
 * ANY entity throws, every earlier write in this call (including
 * already-"committed"-looking earlier entities) is rolled back by the
 * real SQLite engine, closing the real legacy gap
 * (`import-authority-audit.md` gap #2: "no cross-handler transaction").
 *
 * `DatabaseWriteGate` integration: file read/decode happens entirely
 * BEFORE this function is ever called (the caller already holds a
 * decoded `NormalizedTable` per entity) -- `gate.mutex` is held for
 * exactly the real revalidate-then-commit section here, nothing wider
 * (`import-database-write-gate-report.md`).
 */
object ImportCommitExecutor {

    suspend fun commit(
        db: RetailDatabase,
        gate: DatabaseWriteGate,
        persistence: ImportPersistenceRepository,
        token: ImportCommitToken,
        inputs: List<ImportCommitInput>,
        actorId: String,
        nowEpochMillis: Long,
    ): ImportResult<ImportCommitResult> {
        // Real pre-check, its own short-lived lock acquisition via the
        // repository (released before the write-phase lock below) --
        // catches the common case (expired/consumed/mismatched token)
        // cheaply, before ever touching the write path.
        val preCheck = ImportCommitRevalidator.revalidate(token, persistence, nowEpochMillis)
        if (preCheck is ImportResult.Failure) return preCheck

        return gate.mutex.withLock {
            // Real, second, race-safe check now that we actually hold the
            // gate -- nothing else can consume this dry-run between the
            // pre-check above and this line while we hold the lock.
            val dryRun = ImportPersistenceCore.getDryRun(db, token.dryRunId, token.companyId)
                ?: return@withLock ImportResult.Failure(ImportError.DryRunNotFound(token.dryRunId))
            if (dryRun.consumedAtEpochMillis != null) {
                return@withLock ImportResult.Failure(ImportError.CommitConflict("dry-run ${token.dryRunId.value} was already committed"))
            }
            if (!dryRun.commitEligible) {
                return@withLock ImportResult.Failure(ImportError.CommitConflict("dry-run ${token.dryRunId.value} was never eligible for commit"))
            }

            val importId = "imp-${token.dryRunId.value}-${nowEpochMillis}"
            try {
                val result = db.transactionWithResult {
                    val counts = ImportEntityType.entries.associateWith { EntityCounts() }
                    for (entityType in ImportDependencyGraph.sortByCommitOrder(inputs.map { it.entityType })) {
                        val input = inputs.first { it.entityType == entityType }
                        val c = counts.getValue(entityType)
                        when (entityType) {
                            ImportEntityType.CATEGORIES -> processCategories(db, token.companyId, input, nowEpochMillis, c)
                            ImportEntityType.SUPPLIERS -> processSuppliers(db, token.companyId, input, nowEpochMillis, c)
                            ImportEntityType.BRANCHES -> processBranches(db, token.companyId, input, nowEpochMillis, c)
                            ImportEntityType.CUSTOMERS -> processCustomers(db, token.companyId, input, nowEpochMillis, c)
                            ImportEntityType.PRODUCTS -> processProducts(db, token.companyId, token.branchId, input, nowEpochMillis, c)
                        }
                    }

                    val insertedCounts = counts.mapValues { it.value.inserted }.filterValues { it > 0L }
                    val updatedCounts = counts.mapValues { it.value.updated }.filterValues { it > 0L }
                    val skippedTotal = counts.values.sumOf { it.skipped }
                    val provenance = ImportProvenance(
                        importId = importId,
                        sourceHash = token.sourceHash,
                        safeFileName = dryRun.sourceDescriptor.displayName,
                        format = dryRun.format,
                        companyId = token.companyId,
                        branchId = token.branchId,
                        entityTypes = inputs.map { it.entityType },
                        mappingVersion = token.mappingVersion,
                        dryRunId = token.dryRunId,
                        actorId = actorId,
                        startedAtEpochMillis = nowEpochMillis,
                        completedAtEpochMillis = nowEpochMillis,
                        outcome = ImportOutcome.COMMITTED,
                        insertedCounts = insertedCounts,
                        updatedCounts = updatedCounts,
                        skippedCount = skippedTotal,
                        warningCount = dryRun.warningCount,
                        schemaVersion = token.schemaVersion,
                    )
                    val (_, wasNew) = ImportPersistenceCore.saveProvenanceIdempotent(db, provenance, token.idempotencyKey)
                    if (!wasNew) {
                        // Real, unexpected anomaly, not a normal retry path:
                        // the dry-run was independently confirmed unconsumed
                        // above, so a genuine first commit for it can never
                        // legitimately collide with an existing provenance
                        // row under the same idempotency key. Abort the
                        // whole transaction rather than accept a result that
                        // does not actually correspond to these writes.
                        throw ImportCommitAbortedException("idempotency key ${token.idempotencyKey} was already claimed by a different commit")
                    }
                    val marked = ImportPersistenceCore.markDryRunConsumed(db, token.dryRunId, token.companyId, nowEpochMillis)
                    if (!marked) {
                        throw ImportCommitAbortedException("dry-run ${token.dryRunId.value} was consumed concurrently")
                    }
                    ImportPersistenceCore.appendAuditEntry(db, ImportAuditEntry(importId, nowEpochMillis, "COMMIT_COMPLETED", "inserted=${insertedCounts.values.sum()} updated=${updatedCounts.values.sum()} skipped=$skippedTotal"))

                    ImportCommitResult(
                        importId = importId, dryRunId = token.dryRunId, outcome = ImportOutcome.COMMITTED,
                        insertedCounts = insertedCounts, updatedCounts = updatedCounts,
                        skippedCount = skippedTotal, warningCount = dryRun.warningCount, committedAtEpochMillis = nowEpochMillis,
                    )
                }
                ImportResult.Success(result)
            } catch (e: Exception) {
                // Real, separate, always-succeeding write recording the
                // FAILED attempt -- deliberately does NOT touch
                // import_provenance (whose idempotency-key UNIQUE index
                // must stay reserved for a real eventual success only,
                // never consumed by a failed attempt) -- see
                // `import-transaction-rollback-report.md`.
                runCatching {
                    ImportPersistenceCore.appendAuditEntry(db, ImportAuditEntry(importId, nowEpochMillis, "COMMIT_FAILED", e.message ?: e::class.simpleName.orEmpty()))
                }
                ImportResult.Failure(ImportError.InternalFailure(e.message ?: "commit failed"))
            }
        }
    }
}

private fun rawCell(table: NormalizedTable, row: com.actionaura.retail.importing.NormalizedRow, fieldKeyToColumnIndex: Map<String, Int?>, key: String): String? {
    val colIndex = fieldKeyToColumnIndex[key] ?: return null
    return row.cells.getOrNull(colIndex)
}

@OptIn(ExperimentalUuidApi::class)
private fun processCategories(db: RetailDatabase, companyId: Long, input: ImportCommitInput, nowEpochMillis: Long, counts: EntityCounts) {
    val seenKeys = mutableMapOf<String, Long>()
    for (row in input.table.rows) {
        val nameRaw = rawCell(input.table, row, input.fieldKeyToColumnIndex, "name")
        val (nameValue, _) = ImportDomainValueParser.parse(nameRaw, com.actionaura.retail.importing.ImportFieldParser.TEXT, row.rowNumber, "name")
        val name = (nameValue as? ImportParsedValue.TextValue)?.value
        if (name.isNullOrEmpty()) { counts.skipped++; continue }
        if (ImportDuplicatePolicy.classifyWithinFile(ImportEntityType.CATEGORIES, row.rowNumber, name, seenKeys) != null) { counts.skipped++; continue }
        val existing = db.catalogQueries.selectCategoryByExactName(companyId, name).executeAsOneOrNull()
        val decision = ImportDuplicatePolicy.classifyAgainstDatabase(ImportEntityType.CATEGORIES, row.rowNumber, name, existing?.id)
        if (decision != null) { counts.skipped++; continue } // SUPPLIERS/BRANCHES/CATEGORIES policy: SKIP on match
        val descRaw = rawCell(input.table, row, input.fieldKeyToColumnIndex, "description")
        db.catalogQueries.insertCategory(Uuid.random().toString(), companyId, name, descRaw?.trim()?.ifEmpty { null }, nowEpochMillis)
        counts.inserted++
    }
}

private fun processSuppliers(db: RetailDatabase, companyId: Long, input: ImportCommitInput, nowEpochMillis: Long, counts: EntityCounts) {
    val seenKeys = mutableMapOf<String, Long>()
    for (row in input.table.rows) {
        val nameRaw = rawCell(input.table, row, input.fieldKeyToColumnIndex, "name")
        val (nameValue, _) = ImportDomainValueParser.parse(nameRaw, com.actionaura.retail.importing.ImportFieldParser.TEXT, row.rowNumber, "name")
        val name = (nameValue as? ImportParsedValue.TextValue)?.value
        if (name.isNullOrEmpty()) { counts.skipped++; continue }
        if (ImportDuplicatePolicy.classifyWithinFile(ImportEntityType.SUPPLIERS, row.rowNumber, name, seenKeys) != null) { counts.skipped++; continue }
        val existing = db.partiesQueries.selectSupplierByExactName(companyId, name).executeAsOneOrNull()
        val decision = ImportDuplicatePolicy.classifyAgainstDatabase(ImportEntityType.SUPPLIERS, row.rowNumber, name, existing?.id)
        if (decision != null) { counts.skipped++; continue }
        val phone = rawCell(input.table, row, input.fieldKeyToColumnIndex, "phone")?.trim()?.ifEmpty { null }
        val email = rawCell(input.table, row, input.fieldKeyToColumnIndex, "email")?.trim()?.ifEmpty { null }
        val address = rawCell(input.table, row, input.fieldKeyToColumnIndex, "address")?.trim()?.ifEmpty { null }
        db.partiesQueries.insertSupplier(companyId, name, phone, email, address, nowEpochMillis)
        counts.inserted++
    }
}

private fun processBranches(db: RetailDatabase, companyId: Long, input: ImportCommitInput, nowEpochMillis: Long, counts: EntityCounts) {
    val seenKeys = mutableMapOf<String, Long>()
    for (row in input.table.rows) {
        val nameRaw = rawCell(input.table, row, input.fieldKeyToColumnIndex, "name")
        val (nameValue, _) = ImportDomainValueParser.parse(nameRaw, com.actionaura.retail.importing.ImportFieldParser.TEXT, row.rowNumber, "name")
        val name = (nameValue as? ImportParsedValue.TextValue)?.value
        if (name.isNullOrEmpty()) { counts.skipped++; continue }
        if (ImportDuplicatePolicy.classifyWithinFile(ImportEntityType.BRANCHES, row.rowNumber, name, seenKeys) != null) { counts.skipped++; continue }
        val existing = db.catalogQueries.selectBranchByExactName(companyId, name).executeAsOneOrNull()
        val decision = ImportDuplicatePolicy.classifyAgainstDatabase(ImportEntityType.BRANCHES, row.rowNumber, name, existing?.id)
        if (decision != null) { counts.skipped++; continue }
        val address = rawCell(input.table, row, input.fieldKeyToColumnIndex, "address")?.trim()?.ifEmpty { null }
        val phone = rawCell(input.table, row, input.fieldKeyToColumnIndex, "phone")?.trim()?.ifEmpty { null }
        db.catalogQueries.insertBranch(companyId, name, address, phone, nowEpochMillis)
        counts.inserted++
    }
}

private fun processCustomers(db: RetailDatabase, companyId: Long, input: ImportCommitInput, nowEpochMillis: Long, counts: EntityCounts) {
    val seenKeys = mutableMapOf<String, Long>()
    for (row in input.table.rows) {
        val nameRaw = rawCell(input.table, row, input.fieldKeyToColumnIndex, "name")
        val (nameValue, _) = ImportDomainValueParser.parse(nameRaw, com.actionaura.retail.importing.ImportFieldParser.TEXT, row.rowNumber, "name")
        val name = (nameValue as? ImportParsedValue.TextValue)?.value
        if (name.isNullOrEmpty()) { counts.skipped++; continue }

        val emailRaw = rawCell(input.table, row, input.fieldKeyToColumnIndex, "email")
        val (emailParsed, emailIssue) = ImportDomainValueParser.parse(emailRaw, com.actionaura.retail.importing.ImportFieldParser.EMAIL, row.rowNumber, "email")
        if (emailIssue != null) { counts.skipped++; continue } // real, malformed email -- skip, matches "invalid field -> skipped, not crashed"
        val email = (emailParsed as? ImportParsedValue.EmailValue)?.value // null when blank -- real, disclosed legacy limitation (no dedup key)

        var existingId: Long? = null
        if (!email.isNullOrEmpty()) {
            if (ImportDuplicatePolicy.classifyWithinFile(ImportEntityType.CUSTOMERS, row.rowNumber, email, seenKeys) != null) { counts.skipped++; continue }
            existingId = db.partiesQueries.selectCustomerByEmail(companyId, email).executeAsOneOrNull()?.id
        }

        val phone = rawCell(input.table, row, input.fieldKeyToColumnIndex, "phone")?.trim()?.ifEmpty { null }
        val address = rawCell(input.table, row, input.fieldKeyToColumnIndex, "address")?.trim()?.ifEmpty { null }
        val (loyaltyParsed, loyaltyIssue) = ImportDomainValueParser.parse(rawCell(input.table, row, input.fieldKeyToColumnIndex, "loyalty_points"), com.actionaura.retail.importing.ImportFieldParser.QUANTITY_ZERO_OR_MORE, row.rowNumber, "loyalty_points")
        if (loyaltyIssue != null) { counts.skipped++; continue }
        val loyalty = (loyaltyParsed as? ImportParsedValue.QuantityValue)?.value ?: Quantity.ZERO
        val (spentParsed, spentIssue) = ImportDomainValueParser.parse(rawCell(input.table, row, input.fieldKeyToColumnIndex, "total_spent"), com.actionaura.retail.importing.ImportFieldParser.MONEY, row.rowNumber, "total_spent")
        if (spentIssue != null) { counts.skipped++; continue }
        val spent = (spentParsed as? ImportParsedValue.MoneyValue)?.value ?: Money.ZERO

        if (existingId != null) {
            val decision = ImportDuplicatePolicy.classifyAgainstDatabase(ImportEntityType.CUSTOMERS, row.rowNumber, email!!, existingId)
            if (decision != null) {
                db.partiesQueries.updateCustomerFromImport(name, phone, address, loyalty.toString(), spent.toString(), existingId, companyId)
                counts.updated++
                continue
            }
        }
        db.partiesQueries.insertCustomer(companyId, name, phone, email, address, nowEpochMillis)
        counts.inserted++
    }
}

@OptIn(ExperimentalUuidApi::class)
private fun processProducts(db: RetailDatabase, companyId: Long, importBranchId: Long?, input: ImportCommitInput, nowEpochMillis: Long, counts: EntityCounts) {
    val seenKeys = mutableMapOf<String, Long>()
    val categoryIdCache = mutableMapOf<String, String>()

    fun resolveCategoryId(name: String?): String? {
        if (name.isNullOrEmpty()) return null
        categoryIdCache[name]?.let { return it }
        val existing = db.catalogQueries.selectCategoryByExactName(companyId, name).executeAsOneOrNull()
        val id = existing?.id ?: run {
            val newId = Uuid.random().toString()
            db.catalogQueries.insertCategory(newId, companyId, name, null, nowEpochMillis)
            newId
        }
        categoryIdCache[name] = id
        return id
    }

    fun resolveBranchId(): Long? {
        importBranchId?.let { return it }
        val active = db.catalogQueries.selectActiveBranches(companyId).executeAsList()
        if (active.isNotEmpty()) return active.first().id
        db.catalogQueries.insertBranch(companyId, "Main Store", null, null, nowEpochMillis)
        return db.catalogQueries.lastInsertRowId().executeAsOne()
    }

    for (row in input.table.rows) {
        val (nameValue, _) = ImportDomainValueParser.parse(rawCell(input.table, row, input.fieldKeyToColumnIndex, "name"), com.actionaura.retail.importing.ImportFieldParser.TEXT, row.rowNumber, "name")
        val name = (nameValue as? ImportParsedValue.TextValue)?.value
        val (skuValue, _) = ImportDomainValueParser.parse(rawCell(input.table, row, input.fieldKeyToColumnIndex, "sku"), com.actionaura.retail.importing.ImportFieldParser.TEXT, row.rowNumber, "sku")
        val sku = (skuValue as? ImportParsedValue.TextValue)?.value
        val (sellParsed, sellIssue) = ImportDomainValueParser.parse(rawCell(input.table, row, input.fieldKeyToColumnIndex, "sell_price"), com.actionaura.retail.importing.ImportFieldParser.MONEY, row.rowNumber, "sell_price")
        if (name.isNullOrEmpty() || sku.isNullOrEmpty() || sellIssue != null || sellParsed !is ImportParsedValue.MoneyValue) { counts.skipped++; continue }

        if (ImportDuplicatePolicy.classifyWithinFile(ImportEntityType.PRODUCTS, row.rowNumber, sku, seenKeys) != null) { counts.skipped++; continue }

        val barcode = rawCell(input.table, row, input.fieldKeyToColumnIndex, "barcode")?.trim()?.ifEmpty { null }
        val categoryName = rawCell(input.table, row, input.fieldKeyToColumnIndex, "category")?.trim()?.ifEmpty { null }
        val categoryId = resolveCategoryId(categoryName)
        val (costParsed, costIssue) = ImportDomainValueParser.parse(rawCell(input.table, row, input.fieldKeyToColumnIndex, "cost_price"), com.actionaura.retail.importing.ImportFieldParser.MONEY, row.rowNumber, "cost_price")
        if (costIssue != null) { counts.skipped++; continue }
        val costPrice = (costParsed as? ImportParsedValue.MoneyValue)?.value ?: Money.ZERO
        val (taxParsed, taxIssue) = ImportDomainValueParser.parse(rawCell(input.table, row, input.fieldKeyToColumnIndex, "tax_rate"), com.actionaura.retail.importing.ImportFieldParser.PERCENTAGE_RATE, row.rowNumber, "tax_rate")
        if (taxIssue != null) { counts.skipped++; continue }
        val taxRate = (taxParsed as? ImportParsedValue.PercentageRateValue)?.value ?: PercentageRate.ZERO_RATE
        val unit = rawCell(input.table, row, input.fieldKeyToColumnIndex, "unit")?.trim()?.ifEmpty { null } ?: "pcs"
        val (reorderParsed, reorderIssue) = ImportDomainValueParser.parse(rawCell(input.table, row, input.fieldKeyToColumnIndex, "reorder_level"), com.actionaura.retail.importing.ImportFieldParser.INTEGER, row.rowNumber, "reorder_level")
        if (reorderIssue != null) { counts.skipped++; continue }
        val reorderLevel = (reorderParsed as? ImportParsedValue.IntegerValue)?.value ?: 0L
        val (stockParsed, stockIssue) = ImportDomainValueParser.parse(rawCell(input.table, row, input.fieldKeyToColumnIndex, "initial_stock"), com.actionaura.retail.importing.ImportFieldParser.QUANTITY_ZERO_OR_MORE, row.rowNumber, "initial_stock")
        if (stockIssue != null) { counts.skipped++; continue }
        val initialStock = stockParsed as? ImportParsedValue.QuantityValue

        val normalizedName = name.trim().lowercase()
        val existing = db.catalogQueries.selectProductBySku(sku, companyId).executeAsOneOrNull()
        val decision = ImportDuplicatePolicy.classifyAgainstDatabase(ImportEntityType.PRODUCTS, row.rowNumber, sku, existing?.id)

        val productId: Long
        if (decision != null && existing != null) {
            db.catalogQueries.updateProduct(
                barcode, name, normalizedName, categoryId, costPrice.toString(), sellParsed.value.toString(),
                taxRate.toString(), unit, reorderLevel, nowEpochMillis, existing.id, companyId, existing.updated_at,
            )
            productId = existing.id
            counts.updated++
        } else {
            db.catalogQueries.insertProduct(
                companyId, sku, barcode, name, normalizedName, categoryId, costPrice.toString(), sellParsed.value.toString(),
                taxRate.toString(), unit, reorderLevel, nowEpochMillis, nowEpochMillis,
            )
            productId = db.catalogQueries.lastInsertRowId().executeAsOne()
            counts.inserted++
        }

        if (initialStock != null) {
            val branchId = resolveBranchId()
            if (branchId != null) {
                db.inventoryQueries.upsertOpeningStock(companyId, productId, branchId, "0")
                db.inventoryQueries.decrementStock(initialStock.value.toString(), companyId, productId, branchId)
            }
        }
    }
}
