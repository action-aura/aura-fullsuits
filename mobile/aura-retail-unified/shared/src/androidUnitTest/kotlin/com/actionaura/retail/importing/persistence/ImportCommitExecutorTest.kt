package com.actionaura.retail.importing.persistence

import app.cash.sqldelight.driver.jdbc.sqlite.JdbcSqliteDriver
import com.actionaura.retail.data.sqldelight.DatabaseWriteGate
import com.actionaura.retail.db.RetailDatabase
import com.actionaura.retail.importing.ImportCommitToken
import com.actionaura.retail.importing.ImportDryRun
import com.actionaura.retail.importing.ImportDryRunId
import com.actionaura.retail.importing.ImportEntityType
import com.actionaura.retail.importing.ImportError
import com.actionaura.retail.importing.ImportFileDescriptor
import com.actionaura.retail.importing.ImportFormat
import com.actionaura.retail.importing.ImportMappingVersion
import com.actionaura.retail.importing.ImportOutcome
import com.actionaura.retail.importing.ImportProvenance
import com.actionaura.retail.importing.ImportResult
import com.actionaura.retail.importing.ImportSourceId
import com.actionaura.retail.importing.NormalizedColumn
import com.actionaura.retail.importing.NormalizedRow
import com.actionaura.retail.importing.NormalizedTable
import kotlinx.coroutines.async
import kotlinx.coroutines.test.runTest
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertIs
import kotlin.test.assertNotNull
import kotlin.test.assertNull
import kotlin.test.assertTrue

class ImportCommitExecutorTest {

    private fun newDb(driver: app.cash.sqldelight.db.SqlDriver = JdbcSqliteDriver(JdbcSqliteDriver.IN_MEMORY)): RetailDatabase {
        driver.execute(null, "PRAGMA foreign_keys=ON", 0)
        RetailDatabase.Schema.create(driver)
        return RetailDatabase(driver)
    }

    private fun table(header: List<String>, vararg rows: List<String?>): NormalizedTable = NormalizedTable(
        columns = header.mapIndexed { i, h -> NormalizedColumn(i, h) },
        rows = rows.mapIndexed { i, cells -> NormalizedRow((i + 1).toLong(), cells) },
        sourceFormat = ImportFormat.CSV,
    )

    /** identity mapping: column index == field index in `keys`. */
    private fun mapping(keys: List<String>): Map<String, Int?> = keys.withIndex().associate { (i, k) -> k to i }

    private fun categoriesInput(vararg rows: List<String?>) = ImportCommitInput(
        ImportEntityType.CATEGORIES, table(listOf("name", "description"), *rows), mapping(listOf("name", "description")),
    )
    private fun suppliersInput(vararg rows: List<String?>) = ImportCommitInput(
        ImportEntityType.SUPPLIERS, table(listOf("name", "phone", "email", "address"), *rows), mapping(listOf("name", "phone", "email", "address")),
    )
    private fun branchesInput(vararg rows: List<String?>) = ImportCommitInput(
        ImportEntityType.BRANCHES, table(listOf("name", "address", "phone", "status"), *rows), mapping(listOf("name", "address", "phone", "status")),
    )
    private fun customersInput(vararg rows: List<String?>) = ImportCommitInput(
        ImportEntityType.CUSTOMERS, table(listOf("name", "phone", "email", "address", "loyalty_points", "total_spent"), *rows),
        mapping(listOf("name", "phone", "email", "address", "loyalty_points", "total_spent")),
    )
    private fun productsInput(vararg rows: List<String?>) = ImportCommitInput(
        ImportEntityType.PRODUCTS,
        table(listOf("name", "sku", "barcode", "category", "cost_price", "sell_price", "tax_rate", "unit", "reorder_level", "initial_stock"), *rows),
        mapping(listOf("name", "sku", "barcode", "category", "cost_price", "sell_price", "tax_rate", "unit", "reorder_level", "initial_stock")),
    )

    private suspend fun issueDryRun(repo: ImportPersistenceRepository, id: String = "dr-1", companyId: Long = 1L, sourceHash: String = "hash-1", commitEligible: Boolean = true): ImportDryRun {
        val dryRun = ImportDryRun(
            id = ImportDryRunId(id), sourceHash = sourceHash,
            sourceDescriptor = ImportFileDescriptor(ImportSourceId("src"), "file.csv", 100L, "text/csv", "csv"),
            format = ImportFormat.CSV, decoderVersion = 1, mappingVersion = ImportMappingVersion.CURRENT, schemaVersion = 1,
            companyId = companyId, branchId = null, entityTypes = emptyList(), dependencies = emptyList(),
            totalRows = 1, validRows = 1, invalidRows = 0, warningCount = 0, duplicateCount = 0, conflictCount = 0,
            plannedInserts = 1, plannedUpdates = 0, plannedSkips = 0, plannedGeneratedValues = 0,
            validationIssues = emptyList(), duplicates = emptyList(), conflicts = emptyList(),
            commitEligible = commitEligible, createdAtEpochMillis = 1000L, expiresAtEpochMillis = 999_999_999L,
        )
        repo.saveDryRun(dryRun)
        return dryRun
    }

    private fun token(dryRun: ImportDryRun, idempotencyKey: String = "idem-1") = ImportCommitToken(
        dryRunId = dryRun.id, sourceHash = dryRun.sourceHash, mappingVersion = dryRun.mappingVersion, schemaVersion = dryRun.schemaVersion,
        companyId = dryRun.companyId, branchId = dryRun.branchId, idempotencyKey = idempotencyKey, nonce = "n1",
        issuedAtEpochMillis = 1000L, expiresAtEpochMillis = 999_999_999L,
    )

    @Test
    fun aRealCategoryInsertsWhenNoNameMatchExists() = runTest {
        val db = newDb()
        val gate = DatabaseWriteGate()
        val repo = SqlDelightImportPersistenceRepository(db, gate)
        val dryRun = issueDryRun(repo)

        val result = ImportCommitExecutor.commit(db, gate, repo, token(dryRun), listOf(categoriesInput(listOf("Beverages", "Drinks"))), "tester", 2000L)
        assertIs<ImportResult.Success<com.actionaura.retail.importing.ImportCommitResult>>(result)
        assertEquals(1L, result.value.insertedCounts[ImportEntityType.CATEGORIES])
        assertNotNull(db.catalogQueries.selectCategoryByExactName(1L, "Beverages").executeAsOneOrNull())
    }

    @Test
    fun categoryNameMatchIsSkippedNeverUpdated() = runTest {
        val db = newDb()
        val gate = DatabaseWriteGate()
        val repo = SqlDelightImportPersistenceRepository(db, gate)
        db.catalogQueries.insertCategory(1L, "Beverages", "Old description", 500L)
        val dryRun = issueDryRun(repo)

        val result = ImportCommitExecutor.commit(db, gate, repo, token(dryRun), listOf(categoriesInput(listOf("Beverages", "New description"))), "tester", 2000L)
        assertIs<ImportResult.Success<com.actionaura.retail.importing.ImportCommitResult>>(result)
        assertNull(result.value.insertedCounts[ImportEntityType.CATEGORIES])
        assertEquals(1L, result.value.skippedCount)
        val stored = db.catalogQueries.selectCategoryByExactName(1L, "Beverages").executeAsOne()
        assertEquals("Old description", stored.description, "SKIP policy must never overwrite the real existing row")
    }

    @Test
    fun supplierNameMatchIsSkipped() = runTest {
        val db = newDb()
        val gate = DatabaseWriteGate()
        val repo = SqlDelightImportPersistenceRepository(db, gate)
        db.partiesQueries.insertSupplier(1L, "ABC Supply", null, null, null, 500L)
        val dryRun = issueDryRun(repo)

        val result = ImportCommitExecutor.commit(db, gate, repo, token(dryRun), listOf(suppliersInput(listOf("ABC Supply", "555", "a@x.com", "addr"))), "tester", 2000L)
        assertIs<ImportResult.Success<com.actionaura.retail.importing.ImportCommitResult>>(result)
        assertEquals(1L, result.value.skippedCount)
    }

    @Test
    fun branchNameMatchIsSkipped() = runTest {
        val db = newDb()
        val gate = DatabaseWriteGate()
        val repo = SqlDelightImportPersistenceRepository(db, gate)
        db.catalogQueries.insertBranch(1L, "Main Store", null, null, 500L)
        val dryRun = issueDryRun(repo)

        val result = ImportCommitExecutor.commit(db, gate, repo, token(dryRun), listOf(branchesInput(listOf("Main Store", "addr", "555", "active"))), "tester", 2000L)
        assertIs<ImportResult.Success<com.actionaura.retail.importing.ImportCommitResult>>(result)
        assertEquals(1L, result.value.skippedCount)
    }

    @Test
    fun customerEmailMatchUpdatesExisting() = runTest {
        val db = newDb()
        val gate = DatabaseWriteGate()
        val repo = SqlDelightImportPersistenceRepository(db, gate)
        db.partiesQueries.insertCustomer(1L, "Old Name", null, "a@x.com", null, 500L)
        val dryRun = issueDryRun(repo)

        val result = ImportCommitExecutor.commit(db, gate, repo, token(dryRun), listOf(customersInput(listOf("New Name", "555", "a@x.com", "addr", "10", "99.50"))), "tester", 2000L)
        assertIs<ImportResult.Success<com.actionaura.retail.importing.ImportCommitResult>>(result)
        assertEquals(1L, result.value.updatedCounts[ImportEntityType.CUSTOMERS])
        val stored = db.partiesQueries.selectCustomerByEmail(1L, "a@x.com").executeAsOne()
        assertEquals("New Name", stored.name)
        assertEquals("99.50", stored.total_spent)
    }

    @Test
    fun blankEmailCustomerIsAlwaysInsertedNeverDeduped() = runTest {
        val db = newDb()
        val gate = DatabaseWriteGate()
        val repo = SqlDelightImportPersistenceRepository(db, gate)
        val dryRun = issueDryRun(repo)

        val result = ImportCommitExecutor.commit(
            db, gate, repo, token(dryRun),
            listOf(customersInput(listOf("Alice", null, null, null, null, null), listOf("Alice", null, null, null, null, null))),
            "tester", 2000L,
        )
        assertIs<ImportResult.Success<com.actionaura.retail.importing.ImportCommitResult>>(result)
        assertEquals(2L, result.value.insertedCounts[ImportEntityType.CUSTOMERS], "two blank-email rows -- real, disclosed legacy limitation: never treated as duplicates of each other")
    }

    @Test
    fun productInsertsAutoCreatesCategoryAndSetsInitialStock() = runTest {
        val db = newDb()
        val gate = DatabaseWriteGate()
        val repo = SqlDelightImportPersistenceRepository(db, gate)
        db.catalogQueries.insertBranch(1L, "Main Store", null, null, 500L)
        val dryRun = issueDryRun(repo)

        val result = ImportCommitExecutor.commit(
            db, gate, repo, token(dryRun),
            listOf(productsInput(listOf("Coke", "SKU-1", "12345", "Beverages", "1.00", "2.00", "5", "pcs", "3", "50"))),
            "tester", 2000L,
        )
        assertIs<ImportResult.Success<com.actionaura.retail.importing.ImportCommitResult>>(result)
        assertEquals(1L, result.value.insertedCounts[ImportEntityType.PRODUCTS])
        val category = db.catalogQueries.selectCategoryByExactName(1L, "Beverages").executeAsOneOrNull()
        assertNotNull(category, "product's category text must real, auto-create the Category row (real, existing legacy behavior)")
        val product = db.catalogQueries.selectProductBySku("SKU-1", 1L).executeAsOne()
        assertEquals(category.id, product.category_id)
        val branch = db.catalogQueries.selectActiveBranches(1L).executeAsList().first()
        val onHand = db.inventoryQueries.selectStockOnHand(1L, product.id, branch.id).executeAsOneOrNull()
        assertEquals("50", onHand)
    }

    @Test
    fun productSkuMatchUpdatesExisting() = runTest {
        val db = newDb()
        val gate = DatabaseWriteGate()
        val repo = SqlDelightImportPersistenceRepository(db, gate)
        db.catalogQueries.insertProduct(1L, "SKU-1", null, "Old Name", "old name", null, "1.00", "2.00", "0", "pcs", 0, 500L, 500L)
        val dryRun = issueDryRun(repo)

        val result = ImportCommitExecutor.commit(
            db, gate, repo, token(dryRun),
            listOf(productsInput(listOf("New Name", "SKU-1", null, null, "1.00", "3.00", "0", "pcs", "0", null))),
            "tester", 2000L,
        )
        assertIs<ImportResult.Success<com.actionaura.retail.importing.ImportCommitResult>>(result)
        assertEquals(1L, result.value.updatedCounts[ImportEntityType.PRODUCTS])
        val stored = db.catalogQueries.selectProductBySku("SKU-1", 1L).executeAsOne()
        assertEquals("New Name", stored.name)
        assertEquals("3.00", stored.sell_price)
    }

    @Test
    fun duplicateSkuWithinFileSecondRowSkipped() = runTest {
        val db = newDb()
        val gate = DatabaseWriteGate()
        val repo = SqlDelightImportPersistenceRepository(db, gate)
        val dryRun = issueDryRun(repo)

        val result = ImportCommitExecutor.commit(
            db, gate, repo, token(dryRun),
            listOf(productsInput(
                listOf("First", "SKU-1", null, null, "1.00", "2.00", "0", "pcs", "0", null),
                listOf("Second", "SKU-1", null, null, "1.00", "2.00", "0", "pcs", "0", null),
            )),
            "tester", 2000L,
        )
        assertIs<ImportResult.Success<com.actionaura.retail.importing.ImportCommitResult>>(result)
        assertEquals(1L, result.value.insertedCounts[ImportEntityType.PRODUCTS])
        assertEquals(1L, result.value.skippedCount)
    }

    @Test
    fun successfulCommitMarksDryRunConsumedAndWritesProvenanceAndAudit() = runTest {
        val db = newDb()
        val gate = DatabaseWriteGate()
        val repo = SqlDelightImportPersistenceRepository(db, gate)
        val dryRun = issueDryRun(repo)

        val result = ImportCommitExecutor.commit(db, gate, repo, token(dryRun), listOf(categoriesInput(listOf("Beverages", null))), "tester", 2000L)
        assertIs<ImportResult.Success<com.actionaura.retail.importing.ImportCommitResult>>(result)

        val reloaded = repo.getDryRun(dryRun.id, dryRun.companyId)
        assertEquals(2000L, reloaded?.consumedAtEpochMillis)
        val provenance = repo.getProvenanceById(result.value.importId, dryRun.companyId)
        assertNotNull(provenance)
        assertEquals(ImportOutcome.COMMITTED, provenance.outcome)
        val audit = repo.getAuditEntries(result.value.importId)
        assertTrue(audit.any { it.eventCode == "COMMIT_COMPLETED" })
    }

    // ---- Real, driver-injected rollback tests ----

    @Test
    fun aRealFailureDuringTheSecondProductInsertRollsBackTheEntireTransactionIncludingEarlierEntities() = runTest {
        val delegate = JdbcSqliteDriver(JdbcSqliteDriver.IN_MEMORY)
        val failing = FailingOnStatementSqlDriver(delegate, "INSERT INTO products", failOnOccurrence = 2)
        val db = newDb(failing)
        val gate = DatabaseWriteGate()
        val repo = SqlDelightImportPersistenceRepository(db, gate)
        val dryRun = issueDryRun(repo)

        val result = ImportCommitExecutor.commit(
            db, gate, repo, token(dryRun),
            listOf(
                categoriesInput(listOf("Beverages", null)),
                productsInput(
                    listOf("First", "SKU-1", null, null, "1.00", "2.00", "0", "pcs", "0", null),
                    listOf("Second", "SKU-2", null, null, "1.00", "2.00", "0", "pcs", "0", null),
                ),
            ),
            "tester", 2000L,
        )
        assertIs<ImportResult.Failure>(result)
        assertIs<ImportError.InternalFailure>(result.error)

        // Real proof of rollback: the Category insert that happened EARLIER
        // in the SAME real transaction, before the injected product
        // failure, must also be gone -- proves cross-entity atomicity, not
        // just that the failing statement itself didn't apply.
        assertNull(db.catalogQueries.selectCategoryByExactName(1L, "Beverages").executeAsOneOrNull(), "an earlier entity's real insert must be rolled back too")
        assertNull(db.catalogQueries.selectProductBySku("SKU-1", 1L).executeAsOneOrNull(), "the first product's real insert (before the injected failure) must be rolled back too")
        val reloaded = repo.getDryRun(dryRun.id, dryRun.companyId)
        assertNull(reloaded?.consumedAtEpochMillis, "a rolled-back commit must never mark the dry-run consumed")
        assertNull(repo.getProvenanceByIdempotencyKey("idem-1"), "a rolled-back commit must never leave a real provenance row")
    }

    @Test
    fun aRealFailureOnTheFirstCategoryInsertRollsBackEverything() = runTest {
        val delegate = JdbcSqliteDriver(JdbcSqliteDriver.IN_MEMORY)
        val failing = FailingOnStatementSqlDriver(delegate, "INSERT INTO categories", failOnOccurrence = 1)
        val db = newDb(failing)
        val gate = DatabaseWriteGate()
        val repo = SqlDelightImportPersistenceRepository(db, gate)
        val dryRun = issueDryRun(repo)

        val result = ImportCommitExecutor.commit(db, gate, repo, token(dryRun), listOf(categoriesInput(listOf("Beverages", null))), "tester", 2000L)
        assertIs<ImportResult.Failure>(result)
        assertNull(db.catalogQueries.selectCategoryByExactName(1L, "Beverages").executeAsOneOrNull())
        assertNull(repo.getDryRun(dryRun.id, dryRun.companyId)?.consumedAtEpochMillis)
    }

    @Test
    fun aRealFailureDuringTheFinalProvenanceWriteRollsBackAllEntityInsertsToo() = runTest {
        val delegate = JdbcSqliteDriver(JdbcSqliteDriver.IN_MEMORY)
        val failing = FailingOnStatementSqlDriver(delegate, "INSERT INTO import_provenance", failOnOccurrence = 1)
        val db = newDb(failing)
        val gate = DatabaseWriteGate()
        val repo = SqlDelightImportPersistenceRepository(db, gate)
        val dryRun = issueDryRun(repo)

        val result = ImportCommitExecutor.commit(
            db, gate, repo, token(dryRun),
            listOf(categoriesInput(listOf("Beverages", null)), suppliersInput(listOf("ABC Supply", null, null, null))),
            "tester", 2000L,
        )
        assertIs<ImportResult.Failure>(result)
        assertNull(db.catalogQueries.selectCategoryByExactName(1L, "Beverages").executeAsOneOrNull(), "real business rows must roll back even though the failure happened at the very end, in the provenance write")
        assertNull(db.partiesQueries.selectSupplierByExactName(1L, "ABC Supply").executeAsOneOrNull())
        assertNull(repo.getDryRun(dryRun.id, dryRun.companyId)?.consumedAtEpochMillis)
    }

    @Test
    fun anUnexpectedIdempotencyKeyCollisionAbortsAndRollsBackRatherThanMisreportingSuccess() = runTest {
        val db = newDb()
        val gate = DatabaseWriteGate()
        val repo = SqlDelightImportPersistenceRepository(db, gate)
        val dryRun = issueDryRun(repo)
        // Real, pre-existing provenance row under the SAME idempotency key
        // this commit will use, from an unrelated prior import -- an
        // anomaly the executor must detect and abort on, never silently
        // treat as "already done."
        repo.saveProvenanceIdempotent(
            ImportProvenance(
                importId = "unrelated-import", sourceHash = "other-hash", safeFileName = "other.csv", format = ImportFormat.CSV,
                companyId = 1L, branchId = null, entityTypes = emptyList(), mappingVersion = ImportMappingVersion.CURRENT,
                dryRunId = dryRun.id, actorId = "someone-else", startedAtEpochMillis = 1L, completedAtEpochMillis = 1L,
                outcome = ImportOutcome.COMMITTED, insertedCounts = emptyMap(), updatedCounts = emptyMap(), skippedCount = 0L, warningCount = 0L, schemaVersion = 1,
            ),
            "idem-1",
        )

        val result = ImportCommitExecutor.commit(db, gate, repo, token(dryRun, idempotencyKey = "idem-1"), listOf(categoriesInput(listOf("Beverages", null))), "tester", 2000L)
        assertIs<ImportResult.Failure>(result)
        assertNull(db.catalogQueries.selectCategoryByExactName(1L, "Beverages").executeAsOneOrNull(), "the real category insert attempted in this doomed transaction must be rolled back")
        assertNull(repo.getDryRun(dryRun.id, dryRun.companyId)?.consumedAtEpochMillis)
    }

    @Test
    fun twoConcurrentCommitsAgainstTheSameDatabaseAreRealSerializedByTheGateNeverInterleaved() = runTest {
        val db = newDb()
        val gate = DatabaseWriteGate()
        val repo = SqlDelightImportPersistenceRepository(db, gate)
        val dryRunA = issueDryRun(repo, id = "dr-a", companyId = 1L, sourceHash = "hash-a")
        val dryRunB = issueDryRun(repo, id = "dr-b", companyId = 2L, sourceHash = "hash-b")

        val deferredA = async {
            ImportCommitExecutor.commit(db, gate, repo, token(dryRunA, "idem-a"), listOf(categoriesInput(listOf("A-Cat", null))), "tester", 2000L)
        }
        val deferredB = async {
            ImportCommitExecutor.commit(db, gate, repo, token(dryRunB, "idem-b"), listOf(categoriesInput(listOf("B-Cat", null))), "tester", 2000L)
        }
        val resultA = deferredA.await()
        val resultB = deferredB.await()

        assertIs<ImportResult.Success<com.actionaura.retail.importing.ImportCommitResult>>(resultA)
        assertIs<ImportResult.Success<com.actionaura.retail.importing.ImportCommitResult>>(resultB)
        assertNotNull(db.catalogQueries.selectCategoryByExactName(1L, "A-Cat").executeAsOneOrNull())
        assertNotNull(db.catalogQueries.selectCategoryByExactName(2L, "B-Cat").executeAsOneOrNull())
    }
}
