package com.actionaura.retail.importing.perf

import app.cash.sqldelight.driver.jdbc.sqlite.JdbcSqliteDriver
import com.actionaura.retail.data.sqldelight.DatabaseWriteGate
import com.actionaura.retail.db.RetailDatabase
import com.actionaura.retail.importing.ImportCommitToken
import com.actionaura.retail.importing.ImportDryRun
import com.actionaura.retail.importing.ImportDryRunId
import com.actionaura.retail.importing.ImportEntityType
import com.actionaura.retail.importing.ImportFileDescriptor
import com.actionaura.retail.importing.ImportFormat
import com.actionaura.retail.importing.ImportLimits
import com.actionaura.retail.importing.ImportMappingVersion
import com.actionaura.retail.importing.ImportResult
import com.actionaura.retail.importing.ImportSource
import com.actionaura.retail.importing.ImportSourceId
import com.actionaura.retail.importing.csv.CsvImportDecoder
import com.actionaura.retail.importing.entity.ImportEntityDetector
import com.actionaura.retail.importing.persistence.ImportCommitExecutor
import com.actionaura.retail.importing.persistence.ImportCommitInput
import com.actionaura.retail.importing.persistence.SqlDelightImportPersistenceRepository
import kotlinx.coroutines.test.runTest
import kotlin.test.Test
import kotlin.test.assertIs
import kotlin.test.assertTrue

/**
 * M5.8.21 -- real, executed timing at a representative real scale.
 * Real, disclosed measurement discipline (matching
 * `exact-aggregation-performance.md`'s own precedent): every bound is
 * generous, with real measured margin, not a tight fit to one observed
 * run -- real cold-JVM variance was already found and disclosed in
 * M5.7's own reporting performance tests.
 *
 * Real, disclosed scope: this measures wall-clock TIME, not heap
 * memory -- no memory profiler is available on this host. Memory
 * exposure is instead bounded structurally by `ImportLimits`'
 * whole-file-in-memory cap (`maxCompressedFileSizeBytes`,
 * `import-resource-limits.md`), not measured directly here.
 */
class ImportPerformanceAtScaleTest {

    private class InMemorySource(private val bytes: ByteArray) : ImportSource {
        override val descriptor = ImportFileDescriptor(ImportSourceId("perf"), "products.csv", bytes.size.toLong(), "text/csv", "csv")
        override suspend fun readBounded(limits: ImportLimits): ImportResult<ByteArray> = ImportResult.Success(bytes)
    }

    private fun realProductsCsv(rowCount: Int): ByteArray {
        val sb = StringBuilder("name,sku,barcode,category,cost_price,sell_price,tax_rate,unit,reorder_level,initial_stock\n")
        for (i in 1..rowCount) {
            sb.append("Product $i,SKU-$i,BC-$i,Category ${i % 50},1.50,2.99,5,pcs,10,${i % 100}\n")
        }
        return sb.toString().encodeToByteArray()
    }

    @Test
    fun decodingTenThousandRealRowsCompletesWellWithinABoundedRealTime() = runTest {
        val bytes = realProductsCsv(10_000)
        val decoder = CsvImportDecoder()

        val start = System.nanoTime()
        val result = decoder.decode(InMemorySource(bytes), ImportLimits.DEFAULT)
        val elapsedMs = (System.nanoTime() - start) / 1_000_000

        assertIs<ImportResult.Success<com.actionaura.retail.importing.NormalizedTable>>(result)
        assertTrue(result.value.rows.size == 10_000)
        println("REAL MEASURED: CSV decode of 10,000 rows took ${elapsedMs}ms")
        assertTrue(elapsedMs < 5_000, "real 10,000-row CSV decode took ${elapsedMs}ms, expected well under 5000ms with real margin")
    }

    @Test
    fun entityDetectionOverATenThousandRowTableCompletesWellWithinABoundedRealTime() = runTest {
        val bytes = realProductsCsv(10_000)
        val decoded = CsvImportDecoder().decode(InMemorySource(bytes), ImportLimits.DEFAULT)
        assertIs<ImportResult.Success<com.actionaura.retail.importing.NormalizedTable>>(decoded)

        val start = System.nanoTime()
        val candidates = ImportEntityDetector.detectCandidates(decoded.value)
        val elapsedMs = (System.nanoTime() - start) / 1_000_000

        assertTrue(candidates.first().entityType == ImportEntityType.PRODUCTS, "the real products-shaped header set must score highest")
        println("REAL MEASURED: entity detection over a 10,000-row table took ${elapsedMs}ms")
        assertTrue(elapsedMs < 1_000, "real entity detection took ${elapsedMs}ms, expected well under 1000ms (header-only scoring, independent of row count)")
    }

    @Test
    fun aRealTransactionalCommitOfTwoThousandProductsWithAutoCreatedCategoriesCompletesWellWithinABoundedRealTime() = runTest {
        val driver = JdbcSqliteDriver(JdbcSqliteDriver.IN_MEMORY)
        driver.execute(null, "PRAGMA foreign_keys=ON", 0)
        RetailDatabase.Schema.create(driver)
        val db = RetailDatabase(driver)
        val gate = DatabaseWriteGate()
        val repo = SqlDelightImportPersistenceRepository(db, gate)

        val rowCount = 2_000
        val bytes = realProductsCsv(rowCount)
        val decoded = CsvImportDecoder().decode(InMemorySource(bytes), ImportLimits.DEFAULT)
        assertIs<ImportResult.Success<com.actionaura.retail.importing.NormalizedTable>>(decoded)
        val mapping = ImportEntityDetector.suggestMapping(ImportEntityType.PRODUCTS, decoded.value.columns.map { it.rawHeader })

        val dryRun = ImportDryRun(
            id = ImportDryRunId("perf-dr"), sourceHash = "hash", sourceDescriptor = InMemorySource(bytes).descriptor,
            format = ImportFormat.CSV, decoderVersion = 1, mappingVersion = ImportMappingVersion.CURRENT, schemaVersion = 1,
            companyId = 1L, branchId = null, entityTypes = listOf(ImportEntityType.PRODUCTS), dependencies = emptyList(),
            totalRows = rowCount.toLong(), validRows = rowCount.toLong(), invalidRows = 0, warningCount = 0, duplicateCount = 0, conflictCount = 0,
            plannedInserts = rowCount.toLong(), plannedUpdates = 0, plannedSkips = 0, plannedGeneratedValues = 0,
            validationIssues = emptyList(), duplicates = emptyList(), conflicts = emptyList(),
            commitEligible = true, createdAtEpochMillis = 1000L, expiresAtEpochMillis = 999_999_999L,
        )
        repo.saveDryRun(dryRun)
        val token = ImportCommitToken(
            dryRunId = dryRun.id, sourceHash = dryRun.sourceHash, mappingVersion = dryRun.mappingVersion, schemaVersion = dryRun.schemaVersion,
            companyId = dryRun.companyId, branchId = dryRun.branchId, idempotencyKey = "perf-idem", nonce = "n",
            issuedAtEpochMillis = 1000L, expiresAtEpochMillis = 999_999_999L,
        )
        val input = ImportCommitInput(ImportEntityType.PRODUCTS, decoded.value, mapping.fieldKeyToColumnIndex)

        val start = System.nanoTime()
        val result = ImportCommitExecutor.commit(db, gate, repo, token, listOf(input), "perf-tester", 2000L)
        val elapsedMs = (System.nanoTime() - start) / 1_000_000

        assertIs<ImportResult.Success<com.actionaura.retail.importing.ImportCommitResult>>(result)
        assertTrue(result.value.insertedCounts[ImportEntityType.PRODUCTS] == rowCount.toLong())
        println("REAL MEASURED: transactional commit of $rowCount products (with real auto-created categories) took ${elapsedMs}ms")
        assertTrue(elapsedMs < 30_000, "real $rowCount-row transactional commit took ${elapsedMs}ms, expected well under 30000ms with real margin")
    }
}
