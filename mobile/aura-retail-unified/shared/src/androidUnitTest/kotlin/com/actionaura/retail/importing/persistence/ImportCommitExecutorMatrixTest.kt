package com.actionaura.retail.importing.persistence

import app.cash.sqldelight.driver.jdbc.sqlite.JdbcSqliteDriver
import com.actionaura.retail.data.sqldelight.DatabaseWriteGate
import com.actionaura.retail.db.RetailDatabase
import com.actionaura.retail.importing.ImportCommitResult
import com.actionaura.retail.importing.ImportCommitToken
import com.actionaura.retail.importing.ImportDryRun
import com.actionaura.retail.importing.ImportDryRunId
import com.actionaura.retail.importing.ImportEntityType
import com.actionaura.retail.importing.ImportError
import com.actionaura.retail.importing.ImportFileDescriptor
import com.actionaura.retail.importing.ImportFormat
import com.actionaura.retail.importing.ImportMappingVersion
import com.actionaura.retail.importing.ImportResult
import com.actionaura.retail.importing.ImportSourceId
import com.actionaura.retail.importing.NormalizedColumn
import com.actionaura.retail.importing.NormalizedRow
import com.actionaura.retail.importing.NormalizedTable
import kotlinx.coroutines.test.runTest
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertIs
import kotlin.test.assertNotNull
import kotlin.test.assertNull

/**
 * M5.8.23/M5.8.24 -- fills the required per-entity test matrix gaps not
 * already covered by `ImportCommitExecutorTest.kt`'s own 15 tests:
 * missing-column (field unmapped, not merely blank), invalid-field for
 * the non-Product entities, within-file duplicates for
 * Categories/Suppliers/Branches/Customers, a real Category dependency
 * created in the SAME commit (not pre-existing, not auto-created from
 * a bare name -- an actual Categories row in the same `inputs` list),
 * archived-row matching (never re-created), cross-business isolation,
 * and a real idempotent-retry-after-success proof at the executor
 * level (not just the persistence layer).
 */
class ImportCommitExecutorMatrixTest {

    private fun newDb(): RetailDatabase {
        val driver = JdbcSqliteDriver(JdbcSqliteDriver.IN_MEMORY)
        driver.execute(null, "PRAGMA foreign_keys=ON", 0)
        RetailDatabase.Schema.create(driver)
        return RetailDatabase(driver)
    }

    private fun table(header: List<String>, vararg rows: List<String?>): NormalizedTable = NormalizedTable(
        columns = header.mapIndexed { i, h -> NormalizedColumn(i, h) },
        rows = rows.mapIndexed { i, cells -> NormalizedRow((i + 1).toLong(), cells) },
        sourceFormat = ImportFormat.CSV,
    )

    private fun mapping(keys: List<String>): Map<String, Int?> = keys.withIndex().associate { (i, k) -> k to i }

    private suspend fun issueDryRun(repo: ImportPersistenceRepository, id: String = "dr-1", companyId: Long = 1L): ImportDryRun {
        val dryRun = ImportDryRun(
            id = ImportDryRunId(id), sourceHash = "hash-$id",
            sourceDescriptor = ImportFileDescriptor(ImportSourceId("src"), "file.csv", 100L, "text/csv", "csv"),
            format = ImportFormat.CSV, decoderVersion = 1, mappingVersion = ImportMappingVersion.CURRENT, schemaVersion = 1,
            companyId = companyId, branchId = null, entityTypes = emptyList(), dependencies = emptyList(),
            totalRows = 1, validRows = 1, invalidRows = 0, warningCount = 0, duplicateCount = 0, conflictCount = 0,
            plannedInserts = 1, plannedUpdates = 0, plannedSkips = 0, plannedGeneratedValues = 0,
            validationIssues = emptyList(), duplicates = emptyList(), conflicts = emptyList(),
            commitEligible = true, createdAtEpochMillis = 1000L, expiresAtEpochMillis = 999_999_999L,
        )
        repo.saveDryRun(dryRun)
        return dryRun
    }

    private fun token(dryRun: ImportDryRun, idempotencyKey: String = "idem-1") = ImportCommitToken(
        dryRunId = dryRun.id, sourceHash = dryRun.sourceHash, mappingVersion = dryRun.mappingVersion, schemaVersion = dryRun.schemaVersion,
        companyId = dryRun.companyId, branchId = dryRun.branchId, idempotencyKey = idempotencyKey, nonce = "n1",
        issuedAtEpochMillis = 1000L, expiresAtEpochMillis = 999_999_999L,
    )

    // ---- missing-column (field never mapped at all, not merely blank) ----

    @Test
    fun aRequiredFieldThatIsNeverMappedAtAllIsSkippedJustLikeABlankValue() = runTest {
        val db = newDb()
        val gate = DatabaseWriteGate()
        val repo = SqlDelightImportPersistenceRepository(db, gate)
        val dryRun = issueDryRun(repo)

        // Real "missing column": the file has no "sell_price" column at
        // all, so its map entry is explicitly null (unmapped), never
        // even an index into a blank cell -- a real, distinct case from
        // "the mapped column's value happens to be blank."
        val input = ImportCommitInput(
            ImportEntityType.PRODUCTS,
            table(listOf("name", "sku"), listOf("Cola", "SKU-1")),
            mapOf("name" to 0, "sku" to 1, "sell_price" to null),
        )
        val result = ImportCommitExecutor.commit(db, gate, repo, token(dryRun), listOf(input), "tester", 2000L)

        assertIs<ImportResult.Success<ImportCommitResult>>(result)
        assertEquals(1L, result.value.skippedCount, "a required field with no real mapped column must skip the row, never crash or insert with a null price")
        assertNull(db.catalogQueries.selectProductBySku("SKU-1", 1L).executeAsOneOrNull())
    }

    // ---- invalid-field for non-Product entities ----

    @Test
    fun aMalformedCustomerEmailSkipsThatRowOnlyNeverTheWholeImport() = runTest {
        val db = newDb()
        val gate = DatabaseWriteGate()
        val repo = SqlDelightImportPersistenceRepository(db, gate)
        val dryRun = issueDryRun(repo)

        val input = ImportCommitInput(
            ImportEntityType.CUSTOMERS,
            table(
                listOf("name", "phone", "email", "address", "loyalty_points", "total_spent"),
                listOf("Bad Row", null, "not-an-email", null, null, null),
                listOf("Good Row", null, "good@example.com", null, null, null),
            ),
            mapping(listOf("name", "phone", "email", "address", "loyalty_points", "total_spent")),
        )
        val result = ImportCommitExecutor.commit(db, gate, repo, token(dryRun), listOf(input), "tester", 2000L)

        assertIs<ImportResult.Success<ImportCommitResult>>(result)
        assertEquals(1L, result.value.insertedCounts[ImportEntityType.CUSTOMERS])
        assertEquals(1L, result.value.skippedCount)
        assertNotNull(db.partiesQueries.selectCustomerByEmail(1L, "good@example.com").executeAsOneOrNull())
    }

    // ---- within-file duplicates for Categories/Suppliers/Branches/Customers ----

    @Test
    fun withinFileDuplicateCategoryNamesOnlyTheFirstIsInserted() = runTest {
        val db = newDb()
        val gate = DatabaseWriteGate()
        val repo = SqlDelightImportPersistenceRepository(db, gate)
        val dryRun = issueDryRun(repo)

        val input = ImportCommitInput(
            ImportEntityType.CATEGORIES,
            table(listOf("name", "description"), listOf("Beverages", "First"), listOf("Beverages", "Second")),
            mapping(listOf("name", "description")),
        )
        val result = ImportCommitExecutor.commit(db, gate, repo, token(dryRun), listOf(input), "tester", 2000L)

        assertIs<ImportResult.Success<ImportCommitResult>>(result)
        assertEquals(1L, result.value.insertedCounts[ImportEntityType.CATEGORIES])
        assertEquals(1L, result.value.skippedCount)
        assertEquals("First", db.catalogQueries.selectCategoryByExactName(1L, "Beverages").executeAsOne().description)
    }

    @Test
    fun withinFileDuplicateCustomerEmailsOnlyTheFirstIsInserted() = runTest {
        val db = newDb()
        val gate = DatabaseWriteGate()
        val repo = SqlDelightImportPersistenceRepository(db, gate)
        val dryRun = issueDryRun(repo)

        val input = ImportCommitInput(
            ImportEntityType.CUSTOMERS,
            table(
                listOf("name", "phone", "email", "address", "loyalty_points", "total_spent"),
                listOf("First Alice", null, "alice@example.com", null, null, null),
                listOf("Second Alice", null, "alice@example.com", null, null, null),
            ),
            mapping(listOf("name", "phone", "email", "address", "loyalty_points", "total_spent")),
        )
        val result = ImportCommitExecutor.commit(db, gate, repo, token(dryRun), listOf(input), "tester", 2000L)

        assertIs<ImportResult.Success<ImportCommitResult>>(result)
        assertEquals(1L, result.value.insertedCounts[ImportEntityType.CUSTOMERS])
        assertEquals(1L, result.value.skippedCount)
        assertEquals("First Alice", db.partiesQueries.selectCustomerByEmail(1L, "alice@example.com").executeAsOne().name)
    }

    // ---- dependency created in the SAME commit (not pre-existing, not auto-created from bare text) ----

    @Test
    fun aProductLinksToARealCategoryCreatedEarlierInTheSameCommitRatherThanDoubleCreatingIt() = runTest {
        val db = newDb()
        val gate = DatabaseWriteGate()
        val repo = SqlDelightImportPersistenceRepository(db, gate)
        val dryRun = issueDryRun(repo)

        val categoriesInput = ImportCommitInput(
            ImportEntityType.CATEGORIES,
            table(listOf("name", "description"), listOf("Beverages", "Real category from this same import")),
            mapping(listOf("name", "description")),
        )
        val productsInput = ImportCommitInput(
            ImportEntityType.PRODUCTS,
            table(
                listOf("name", "sku", "barcode", "category", "cost_price", "sell_price", "tax_rate", "unit", "reorder_level", "initial_stock"),
                listOf("Cola", "SKU-1", null, "Beverages", "1.00", "2.00", "0", "pcs", "0", null),
            ),
            mapping(listOf("name", "sku", "barcode", "category", "cost_price", "sell_price", "tax_rate", "unit", "reorder_level", "initial_stock")),
        )
        val result = ImportCommitExecutor.commit(db, gate, repo, token(dryRun), listOf(categoriesInput, productsInput), "tester", 2000L)

        assertIs<ImportResult.Success<ImportCommitResult>>(result)
        assertEquals(1L, result.value.insertedCounts[ImportEntityType.CATEGORIES], "exactly one real Category row, never two")
        val category = db.catalogQueries.selectCategoryByExactName(1L, "Beverages").executeAsOne()
        assertEquals("Real category from this same import", category.description, "the product's own resolution must link to the REAL row this import itself created, not a second, bare auto-created one")
        val product = db.catalogQueries.selectProductBySku("SKU-1", 1L).executeAsOne()
        assertEquals(category.id, product.category_id)
    }

    // ---- archived rows are still matched, never re-created ----

    @Test
    fun anArchivedCategoryIsStillMatchedByNameNeverRecreated() = runTest {
        val db = newDb()
        val gate = DatabaseWriteGate()
        val repo = SqlDelightImportPersistenceRepository(db, gate)
        db.catalogQueries.insertCategory(1L, "Discontinued", "old", 500L)
        db.catalogQueries.updateCategoryStatus("archived", db.catalogQueries.selectCategoryByExactName(1L, "Discontinued").executeAsOne().id, 1L)
        val dryRun = issueDryRun(repo)

        val input = ImportCommitInput(
            ImportEntityType.CATEGORIES,
            table(listOf("name", "description"), listOf("Discontinued", "new")),
            mapping(listOf("name", "description")),
        )
        val result = ImportCommitExecutor.commit(db, gate, repo, token(dryRun), listOf(input), "tester", 2000L)

        assertIs<ImportResult.Success<ImportCommitResult>>(result)
        assertEquals(1L, result.value.skippedCount, "an archived Category with a real name match must still be found and skipped, never silently re-created as a duplicate")
        assertNull(result.value.insertedCounts[ImportEntityType.CATEGORIES])
    }

    // ---- cross-business isolation ----

    @Test
    fun importingForOneCompanyNeverMatchesOrTouchesAnotherCompanysRowWithTheSameName() = runTest {
        val db = newDb()
        val gate = DatabaseWriteGate()
        val repo = SqlDelightImportPersistenceRepository(db, gate)
        db.catalogQueries.insertCategory(2L, "Beverages", "company 2's own row", 500L)
        val dryRun = issueDryRun(repo, companyId = 1L)

        val input = ImportCommitInput(
            ImportEntityType.CATEGORIES,
            table(listOf("name", "description"), listOf("Beverages", "company 1's own row")),
            mapping(listOf("name", "description")),
        )
        val result = ImportCommitExecutor.commit(db, gate, repo, token(dryRun), listOf(input), "tester", 2000L)

        assertIs<ImportResult.Success<ImportCommitResult>>(result)
        assertEquals(1L, result.value.insertedCounts[ImportEntityType.CATEGORIES], "company 2's identically-named row must never be treated as a real match for company 1's import")
        assertEquals("company 2's own row", db.catalogQueries.selectCategoryByExactName(2L, "Beverages").executeAsOne().description, "company 2's real row must remain completely untouched")
        assertEquals("company 1's own row", db.catalogQueries.selectCategoryByExactName(1L, "Beverages").executeAsOne().description)
    }

    // ---- idempotent retry after a real success, at the executor level ----

    @Test
    fun retryingTheSameCommitAfterARealSuccessIsRejectedNeverDoubleApplied() = runTest {
        val db = newDb()
        val gate = DatabaseWriteGate()
        val repo = SqlDelightImportPersistenceRepository(db, gate)
        val dryRun = issueDryRun(repo)
        val input = ImportCommitInput(
            ImportEntityType.CATEGORIES,
            table(listOf("name", "description"), listOf("Beverages", null)),
            mapping(listOf("name", "description")),
        )
        val theToken = token(dryRun)

        val first = ImportCommitExecutor.commit(db, gate, repo, theToken, listOf(input), "tester", 2000L)
        assertIs<ImportResult.Success<ImportCommitResult>>(first)

        // Real retry: the exact same token, as a real client would resend
        // after losing the first response (e.g. a dropped connection).
        val retry = ImportCommitExecutor.commit(db, gate, repo, theToken, listOf(input), "tester", 2100L)

        assertIs<ImportResult.Failure>(retry)
        assertIs<ImportError.CommitConflict>(retry.error)
        // Real proof of no double-apply: exactly one real Category row exists, not two.
        val all = db.catalogQueries.selectActiveCategories(1L).executeAsList()
        assertEquals(1, all.count { it.name == "Beverages" })
    }
}
