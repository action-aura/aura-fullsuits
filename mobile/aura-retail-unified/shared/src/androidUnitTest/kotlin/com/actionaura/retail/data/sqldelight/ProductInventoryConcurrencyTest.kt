package com.actionaura.retail.data.sqldelight

import app.cash.sqldelight.driver.jdbc.sqlite.JdbcSqliteDriver
import com.actionaura.retail.data.DomainResult
import com.actionaura.retail.data.RepositoryError
import com.actionaura.retail.data.StockMovementDirection
import com.actionaura.retail.data.StockMovementReason
import com.actionaura.retail.data.migration.CatalogImporter
import com.actionaura.retail.db.RetailDatabase
import com.actionaura.retail.financial.Money
import com.actionaura.retail.financial.PercentageRate
import com.actionaura.retail.financial.Quantity
import com.actionaura.retail.usecases.product.CreateProductUseCase
import com.actionaura.retail.platform.AndroidUnicodeTextNormalizer
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.async
import kotlinx.coroutines.awaitAll
import kotlinx.coroutines.test.runTest
import kotlin.test.Test
import kotlin.test.assertEquals

/**
 * M5.5.14 -- REAL concurrency proof, genuinely parallel OS threads
 * (`Dispatchers.Default`), not `runTest`'s cooperative single-threaded
 * test dispatcher. This distinction matters: `SqlDelightXxxRepository`'s
 * `db.transactionWithResult { ... }` blocks run to completion
 * synchronously with no internal suspension point, so launching
 * "concurrent" coroutines on `runTest`'s own virtual-time dispatcher would
 * execute them fully serialized (no interleaving ever occurs, since there
 * is nothing to suspend on mid-transaction) -- a real race requires actual
 * parallel threads contending for the same underlying JDBC connection,
 * exactly like M3's `FinancialSecurityTest` proved for the in-memory
 * Mutex-guarded repository, but here proving SQLite's OWN transaction
 * serialization is the real backstop (stock-concurrency-report.md).
 */
class ProductInventoryConcurrencyTest {

    private fun newDb(): RetailDatabase {
        val driver = JdbcSqliteDriver(JdbcSqliteDriver.IN_MEMORY)
        driver.execute(null, "PRAGMA foreign_keys=ON", 0)
        RetailDatabase.Schema.create(driver)
        return RetailDatabase(driver)
    }

    private val normalizer = AndroidUnicodeTextNormalizer()

    @Test
    fun concurrentDuplicateSkuCreationResolvesToExactlyOneWinner() = runTest {
        val db = newDb()
        val gate = DatabaseWriteGate()
        val productRepo = SqlDelightProductRepository(db, gate)
        val categoryRepo = SqlDelightCategoryRepository(db, gate)
        val useCase = CreateProductUseCase(productRepo, categoryRepo, normalizer)

        val results = (1..20).map {
            async(Dispatchers.Default) {
                useCase.execute(1L, "SKU-RACE-1", null, "Cola $it", null, Money.ZERO, Money.of(1.0), PercentageRate.trusted(0.0), "can", 5, 1000L)
            }
        }.awaitAll()

        val succeeded = results.count { it is DomainResult.Success }
        val duplicateRejected = results.count { it is DomainResult.Failure && it.error is RepositoryError.DuplicateValue }
        assertEquals(1, succeeded, "exactly one of 20 concurrent same-SKU creations must win")
        assertEquals(19, duplicateRejected, "every other attempt must be rejected as a real duplicate, not silently lost or duplicated")
        assertEquals(1, productRepo.listActive(1L).size, "exactly one product row must exist after the race")
    }

    @Test
    fun concurrentDuplicateBarcodeCreationResolvesToExactlyOneWinner() = runTest {
        val db = newDb()
        val gate = DatabaseWriteGate()
        val productRepo = SqlDelightProductRepository(db, gate)
        val categoryRepo = SqlDelightCategoryRepository(db, gate)
        val useCase = CreateProductUseCase(productRepo, categoryRepo, normalizer)

        val results = (1..20).map { i ->
            async(Dispatchers.Default) {
                useCase.execute(1L, "SKU-RACE-BC-$i", "000777", "Cola", null, Money.ZERO, Money.of(1.0), PercentageRate.trusted(0.0), "can", 5, 1000L)
            }
        }.awaitAll()

        val succeeded = results.count { it is DomainResult.Success }
        val duplicateRejected = results.count { it is DomainResult.Failure && it.error is RepositoryError.DuplicateValue }
        assertEquals(1, succeeded, "exactly one of 20 concurrent same-barcode creations must win")
        assertEquals(19, duplicateRejected)
        assertEquals(1, productRepo.listActive(1L).size)
    }

    @Test
    fun finalUnitSaleRaceResolvesExactlyOneWinnerAndStockNeverGoesNegative() = runTest {
        val db = newDb()
        val gate = DatabaseWriteGate()
        val productRepo = SqlDelightProductRepository(db, gate)
        val branchRepo = SqlDelightBranchRepository(db, gate)
        val inventoryRepo = SqlDelightInventoryRepository(db, gate)
        val branch = branchRepo.insert(1L, "Main", null, null, 500L)
        val product = (productRepo.insert(1L, "SKU-RACE-2", null, "Cola", "cola", null, Money.ZERO, Money.of(1.0), PercentageRate.trusted(0.0), "can", 5, 1000L) as DomainResult.Success).value
        inventoryRepo.ensureOpeningStock(1L, product.id, branch.id, Quantity.zeroOrMore("1")!!)

        // 10 concurrent "sale" attempts race for the last 1 unit of stock.
        val results = (1..10).map { i ->
            async(Dispatchers.Default) {
                inventoryRepo.adjustStock(
                    1L, product.id, branch.id, StockMovementDirection.DECREASE, Quantity.parse("1").getOrNull()!!,
                    StockMovementReason.SALE, null, i.toLong(), null, "final-unit-race-$i", null, "cashier", 2000L,
                )
            }
        }.awaitAll()

        val succeeded = results.count { it is DomainResult.Success }
        val insufficientStock = results.count { it is DomainResult.Failure && it.error is RepositoryError.InsufficientStock }
        assertEquals(1, succeeded, "exactly one of 10 concurrent buyers should win the race for the last unit")
        assertEquals(9, insufficientStock)
        assertEquals(Quantity.ZERO, inventoryRepo.getStockOnHand(1L, product.id, branch.id), "stock must be exactly zero, never negative")

        val history = inventoryRepo.listMovementHistory(1L, product.id, 20)
        assertEquals(1, history.size, "exactly one movement row -- no partial/duplicate movement from a losing attempt")
    }

    @Test
    fun concurrentStockIncreasesAllApplyWithNoLostUpdates() = runTest {
        val db = newDb()
        val gate = DatabaseWriteGate()
        val productRepo = SqlDelightProductRepository(db, gate)
        val branchRepo = SqlDelightBranchRepository(db, gate)
        val inventoryRepo = SqlDelightInventoryRepository(db, gate)
        val branch = branchRepo.insert(1L, "Main", null, null, 500L)
        val product = (productRepo.insert(1L, "SKU-RACE-3", null, "Cola", "cola", null, Money.ZERO, Money.of(1.0), PercentageRate.trusted(0.0), "can", 5, 1000L) as DomainResult.Success).value

        val results = (1..15).map { i ->
            async(Dispatchers.Default) {
                inventoryRepo.adjustStock(
                    1L, product.id, branch.id, StockMovementDirection.INCREASE, Quantity.parse("1").getOrNull()!!,
                    StockMovementReason.MANUAL_RECEIPT, null, null, null, "concurrent-increase-$i", null, "tester", 2000L,
                )
            }
        }.awaitAll()

        assertEquals(15, results.count { it is DomainResult.Success })
        assertEquals(Quantity.zeroOrMore("15")!!, inventoryRepo.getStockOnHand(1L, product.id, branch.id), "every concurrent increase must apply -- real proof no read-modify-write update was silently lost")
        assertEquals(15, inventoryRepo.listMovementHistory(1L, product.id, 20).size)
    }

    // ------------------------------------------------------------------
    // M5.5 mandatory follow-up -- real construction-rule proof
    // (stock-concurrency-report.md): every scenario below shares ONE
    // `DatabaseWriteGate` across the SAME `db`, exactly the real
    // construction discipline the checkpoint required be proven, not just
    // asserted. Report-read-vs-sale-commit and backup-vs-write-transaction
    // are NOT included here -- no real `ReportingRepository` implementation
    // exists yet (M5.6 is the milestone that builds it) and no real backup
    // implementation exists yet (Milestone 16) -- both are explicitly
    // deferred, not silently skipped, and will get their own real
    // concurrency proof when those real implementations exist
    // (reporting-concurrency-report.md, M5.6.11).
    // ------------------------------------------------------------------

    @Test
    fun twoSeparateProductRepositoryInstancesSharingOneGateStillMutuallyExclude() = runTest {
        // The real gap DatabaseWriteGate fixes: TWO different repository
        // OBJECTS (not the same instance racing against itself, like every
        // test above) contending for the same underlying connection. Only
        // safe because both are constructed with the SAME gate -- proves
        // the real construction rule, not the old per-instance Mutex
        // (which could never have protected this).
        val db = newDb()
        val gate = DatabaseWriteGate()
        val categoryRepo = SqlDelightCategoryRepository(db, gate)
        val repoA = SqlDelightProductRepository(db, gate)
        val repoB = SqlDelightProductRepository(db, gate)
        val useCaseA = CreateProductUseCase(repoA, categoryRepo, normalizer)
        val useCaseB = CreateProductUseCase(repoB, categoryRepo, normalizer)

        val results = (1..20).map { i ->
            async(Dispatchers.Default) {
                val useCase = if (i % 2 == 0) useCaseA else useCaseB
                useCase.execute(1L, "SKU-TWO-REPOS", null, "Cola $i", null, Money.ZERO, Money.of(1.0), PercentageRate.trusted(0.0), "can", 5, 1000L)
            }
        }.awaitAll()

        val succeeded = results.count { it is DomainResult.Success }
        assertEquals(1, succeeded, "exactly one winner even when the 20 racing creates are split across two different ProductRepository instances")
        assertEquals(1, repoA.listActive(1L).size, "both instances see the same underlying data -- only one product row exists")
    }

    @Test
    fun concurrentProductAndInventoryOperationsAcrossRepositoryTypesDoNotCorruptEitherTable() = runTest {
        // Cross-REPOSITORY-TYPE contention (Product vs Inventory), not just
        // cross-instance-of-the-same-type -- both share the one gate.
        val db = newDb()
        val gate = DatabaseWriteGate()
        val productRepo = SqlDelightProductRepository(db, gate)
        val categoryRepo = SqlDelightCategoryRepository(db, gate)
        val branchRepo = SqlDelightBranchRepository(db, gate)
        val inventoryRepo = SqlDelightInventoryRepository(db, gate)
        val branch = branchRepo.insert(1L, "Main", null, null, 500L)
        val existingProduct = (productRepo.insert(1L, "SKU-XT-0", null, "Existing", "existing", null, Money.ZERO, Money.of(1.0), PercentageRate.trusted(0.0), "can", 5, 1000L) as DomainResult.Success).value
        inventoryRepo.ensureOpeningStock(1L, existingProduct.id, branch.id, Quantity.ZERO)

        val creates = (1..10).map { i ->
            async(Dispatchers.Default) {
                CreateProductUseCase(productRepo, categoryRepo, normalizer)
                    .execute(1L, "SKU-XT-$i", null, "New $i", null, Money.ZERO, Money.of(1.0), PercentageRate.trusted(0.0), "can", 5, 1000L)
            }
        }
        val increases = (1..10).map {
            async(Dispatchers.Default) {
                inventoryRepo.adjustStock(
                    1L, existingProduct.id, branch.id, StockMovementDirection.INCREASE, Quantity.parse("1").getOrNull()!!,
                    StockMovementReason.MANUAL_RECEIPT, null, null, null, null, null, "tester", 2000L,
                )
            }
        }
        val createResults = creates.awaitAll()
        val increaseResults = increases.awaitAll()

        assertEquals(10, createResults.count { it is DomainResult.Success }, "all 10 genuinely distinct product creates must succeed")
        assertEquals(11, productRepo.listActive(1L).size, "10 new + 1 pre-existing, none lost or duplicated")
        assertEquals(10, increaseResults.count { it is DomainResult.Success })
        assertEquals(Quantity.zeroOrMore("10")!!, inventoryRepo.getStockOnHand(1L, existingProduct.id, branch.id), "no lost stock updates while Product creates were racing on the same connection")
    }

    @Test
    fun saleFinalizationAndManualStockAdjustmentSerializeCorrectlyOnTheSameStock() = runTest {
        // Two DIFFERENT logical operations (a "sale" decrement and a
        // "manual correction" decrement) sharing the one real primitive
        // (adjustStock) must still serialize correctly against each other
        // -- the reason code must not bypass the gate.
        val db = newDb()
        val gate = DatabaseWriteGate()
        val productRepo = SqlDelightProductRepository(db, gate)
        val branchRepo = SqlDelightBranchRepository(db, gate)
        val inventoryRepo = SqlDelightInventoryRepository(db, gate)
        val branch = branchRepo.insert(1L, "Main", null, null, 500L)
        val product = (productRepo.insert(1L, "SKU-SALE-VS-ADJ", null, "Cola", "cola", null, Money.ZERO, Money.of(1.0), PercentageRate.trusted(0.0), "can", 5, 1000L) as DomainResult.Success).value
        inventoryRepo.ensureOpeningStock(1L, product.id, branch.id, Quantity.zeroOrMore("10")!!)

        val saleAttempt = async(Dispatchers.Default) {
            inventoryRepo.adjustStock(
                1L, product.id, branch.id, StockMovementDirection.DECREASE, Quantity.parse("6").getOrNull()!!,
                StockMovementReason.SALE, null, 1L, null, "sale-vs-adjust-sale", null, "cashier", 2000L,
            )
        }
        val manualAdjustAttempt = async(Dispatchers.Default) {
            inventoryRepo.adjustStock(
                1L, product.id, branch.id, StockMovementDirection.DECREASE, Quantity.parse("6").getOrNull()!!,
                StockMovementReason.MANUAL_CORRECTION_DECREASE, null, null, null, "sale-vs-adjust-manual", null, "tester", 2000L,
            )
        }
        val results = listOf(saleAttempt, manualAdjustAttempt).awaitAll()

        // 10 on hand, two competing 6-unit decrements -- exactly one can
        // succeed (10-6=4, but a second -6 would go negative).
        assertEquals(1, results.count { it is DomainResult.Success }, "only one of the two competing 6-unit decrements can be satisfied against 10 on hand")
        assertEquals(1, results.count { it is DomainResult.Failure && it.error is RepositoryError.InsufficientStock })
        assertEquals(Quantity.zeroOrMore("4")!!, inventoryRepo.getStockOnHand(1L, product.id, branch.id), "stock must reflect exactly one applied decrement, never both, never neither")
    }

    @Test
    fun returnRestorationAndManualReceiptSerializeCorrectlyWithNoLostUpdates() = runTest {
        // Return restoration (INCREASE, reason=RETURN) and a manual receipt
        // (INCREASE, reason=MANUAL_RECEIPT) are compatible directions (not
        // competing for a limited resource) -- both must fully apply, real
        // proof neither's read-modify-write silently overwrote the other's.
        val db = newDb()
        val gate = DatabaseWriteGate()
        val productRepo = SqlDelightProductRepository(db, gate)
        val branchRepo = SqlDelightBranchRepository(db, gate)
        val inventoryRepo = SqlDelightInventoryRepository(db, gate)
        val branch = branchRepo.insert(1L, "Main", null, null, 500L)
        val product = (productRepo.insert(1L, "SKU-RETURN-VS-RECEIPT", null, "Cola", "cola", null, Money.ZERO, Money.of(1.0), PercentageRate.trusted(0.0), "can", 5, 1000L) as DomainResult.Success).value
        inventoryRepo.ensureOpeningStock(1L, product.id, branch.id, Quantity.ZERO)

        val returnAttempt = async(Dispatchers.Default) {
            inventoryRepo.adjustStock(
                1L, product.id, branch.id, StockMovementDirection.INCREASE, Quantity.parse("3").getOrNull()!!,
                StockMovementReason.RETURN, null, null, 1L, "return-vs-receipt-return", null, "cashier", 2000L,
            )
        }
        val receiptAttempt = async(Dispatchers.Default) {
            inventoryRepo.adjustStock(
                1L, product.id, branch.id, StockMovementDirection.INCREASE, Quantity.parse("7").getOrNull()!!,
                StockMovementReason.MANUAL_RECEIPT, null, null, null, "return-vs-receipt-receipt", null, "tester", 2000L,
            )
        }
        val results = listOf(returnAttempt, receiptAttempt).awaitAll()

        assertEquals(2, results.count { it is DomainResult.Success }, "both compatible increases must apply")
        assertEquals(Quantity.zeroOrMore("10")!!, inventoryRepo.getStockOnHand(1L, product.id, branch.id), "3 (return) + 7 (receipt) = 10, no lost update from either side")
        assertEquals(2, inventoryRepo.listMovementHistory(1L, product.id, 10).size)
    }

    @Test
    fun importAndProductCreationDoNotRace() = runTest {
        // Real, additional gap found while designing this test: CatalogImporter
        // used to call newDb.transaction { ... } directly, completely
        // bypassing any DatabaseWriteGate -- fixed (CatalogImporter.kt's own
        // KDoc) so it now takes and honors the same gate a concurrent
        // repository operation on the same db uses.
        val legacyDriver = JdbcSqliteDriver(JdbcSqliteDriver.IN_MEMORY)
        legacyDriver.execute(null, "CREATE TABLE branches (id INTEGER PRIMARY KEY, company_id INTEGER DEFAULT 1, name TEXT NOT NULL, address TEXT, phone TEXT, status TEXT DEFAULT 'active', created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)", 0)
        legacyDriver.execute(null, "CREATE TABLE categories (id INTEGER PRIMARY KEY, company_id INTEGER DEFAULT 1, name TEXT NOT NULL, description TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)", 0)
        legacyDriver.execute(null, "CREATE TABLE products (id INTEGER PRIMARY KEY, company_id INTEGER DEFAULT 1, sku TEXT NOT NULL, barcode TEXT, name TEXT NOT NULL, category_id INTEGER, cost_price REAL DEFAULT 0, sell_price REAL DEFAULT 0, tax_rate REAL DEFAULT 0, unit TEXT DEFAULT 'pcs', reorder_level INTEGER DEFAULT 5, status TEXT DEFAULT 'active', created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)", 0)
        legacyDriver.execute(null, "INSERT INTO branches(id, company_id, name, status, created_at) VALUES (1, 1, 'Legacy Branch', 'active', '2026-01-15 10:00:00')", 0)
        legacyDriver.execute(null, "INSERT INTO products(id, company_id, sku, name, cost_price, sell_price, status, created_at) VALUES (1, 1, 'SKU-IMPORT-1', 'Legacy Product', 0.5, 1.0, 'active', '2026-01-15 10:00:00')", 0)

        val db = newDb()
        val gate = DatabaseWriteGate()
        val productRepo = SqlDelightProductRepository(db, gate)
        val categoryRepo = SqlDelightCategoryRepository(db, gate)

        val importAttempt = async(Dispatchers.Default) {
            CatalogImporter.import(legacyDriver, db, gate, normalizer)
        }
        val createAttempts = (1..5).map { i ->
            async(Dispatchers.Default) {
                CreateProductUseCase(productRepo, categoryRepo, normalizer)
                    .execute(1L, "SKU-MANUAL-$i", null, "Manual $i", null, Money.ZERO, Money.of(1.0), PercentageRate.trusted(0.0), "can", 5, 1000L)
            }
        }

        val importResult = importAttempt.await()
        val createResults = createAttempts.awaitAll()

        assertEquals(1, importResult.productsImported, "the import's own product must land intact")
        assertEquals(5, createResults.count { it is DomainResult.Success }, "all 5 manually-created products (genuinely distinct SKUs) must also land intact")
        assertEquals(6, productRepo.listActive(1L).size, "1 imported + 5 manually created, none lost or duplicated from racing on the same connection")
    }
}
