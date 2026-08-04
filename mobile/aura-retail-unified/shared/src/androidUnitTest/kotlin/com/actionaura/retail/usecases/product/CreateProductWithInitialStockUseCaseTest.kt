package com.actionaura.retail.usecases.product

import app.cash.sqldelight.driver.jdbc.sqlite.JdbcSqliteDriver
import com.actionaura.retail.data.DomainResult
import com.actionaura.retail.data.RepositoryError
import com.actionaura.retail.data.sqldelight.SqlDelightBranchRepository
import com.actionaura.retail.data.sqldelight.SqlDelightCategoryRepository
import com.actionaura.retail.data.sqldelight.SqlDelightInventoryRepository
import com.actionaura.retail.data.sqldelight.SqlDelightProductRepository
import com.actionaura.retail.db.RetailDatabase
import com.actionaura.retail.financial.Money
import com.actionaura.retail.financial.PercentageRate
import com.actionaura.retail.financial.Quantity
import com.actionaura.retail.platform.AndroidUnicodeTextNormalizer
import com.actionaura.retail.usecases.branch.DeactivateBranchUseCase
import kotlinx.coroutines.test.runTest
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertIs
import kotlin.test.assertNull
import kotlin.test.assertTrue

/** M5.5.6 -- real, executed proof that product creation + opening stock is one atomic transaction. */
class CreateProductWithInitialStockUseCaseTest {

    private fun newDb(): RetailDatabase {
        val driver = JdbcSqliteDriver(JdbcSqliteDriver.IN_MEMORY)
        driver.execute(null, "PRAGMA foreign_keys=ON", 0)
        RetailDatabase.Schema.create(driver)
        return RetailDatabase(driver)
    }

    private val normalizer = AndroidUnicodeTextNormalizer()

    @Test
    fun createWithInitialStockPersistsProductAndOpeningStockTogether() = runTest {
        val db = newDb()
        val branchRepo = SqlDelightBranchRepository(db)
        val branch = branchRepo.insert(1L, "Main", null, null, 500L)
        val useCase = CreateProductWithInitialStockUseCase(SqlDelightProductRepository(db), SqlDelightCategoryRepository(db), branchRepo, normalizer)

        val result = useCase.execute(
            1L, "SKU-100", null, "Cola", null, Money.of(0.5), Money.of(1.5), PercentageRate.trusted(0.0), "can", 5,
            branch.id, Quantity.parse("100").getOrNull()!!, "tester", null, 1000L,
        )
        assertIs<DomainResult.Success<*>>(result)
        val product = (result as DomainResult.Success).value

        val inventory = SqlDelightInventoryRepository(db)
        assertEquals(Quantity.zeroOrMore("100")!!, inventory.getStockOnHand(1L, product.id, branch.id))
        val history = inventory.listMovementHistory(1L, product.id, 10)
        assertEquals(1, history.size)
        assertEquals("INITIAL_STOCK", history.first().movementType)
        assertEquals(Quantity.ZERO, history.first().quantityBefore)
        assertEquals(Quantity.zeroOrMore("100")!!, history.first().quantityAfter)
    }

    @Test
    fun zeroInitialStockCreatesProductButNoMovementRow() = runTest {
        // LEGACY_PARITY: matches create_product()'s own "only if
        // initial_stock>0" behavior (product-inventory-authority-audit.md #2).
        val db = newDb()
        val branchRepo = SqlDelightBranchRepository(db)
        val branch = branchRepo.insert(1L, "Main", null, null, 500L)
        val useCase = CreateProductWithInitialStockUseCase(SqlDelightProductRepository(db), SqlDelightCategoryRepository(db), branchRepo, normalizer)

        val result = useCase.execute(
            1L, "SKU-101", null, "Cola", null, Money.ZERO, Money.of(1.0), PercentageRate.trusted(0.0), "can", 5,
            branch.id, Quantity.ZERO, "tester", null, 1000L,
        )
        assertIs<DomainResult.Success<*>>(result)
        val product = (result as DomainResult.Success).value
        assertTrue(SqlDelightInventoryRepository(db).listMovementHistory(1L, product.id, 10).isEmpty())
    }

    @Test
    fun archivedBranchRejectsCreationAndPersistsNoProductEither() = runTest {
        // Proves real atomicity, not just "the stock write failed" -- the
        // PRODUCT row must not exist either after a rejected attempt.
        val db = newDb()
        val branchRepo = SqlDelightBranchRepository(db)
        val branch = branchRepo.insert(1L, "Main", null, null, 500L)
        branchRepo.insert(1L, "Second", null, null, 600L) // so "Main" isn't the last active branch
        DeactivateBranchUseCase(branchRepo).execute(1L, branch.id)

        val productRepo = SqlDelightProductRepository(db)
        val useCase = CreateProductWithInitialStockUseCase(productRepo, SqlDelightCategoryRepository(db), branchRepo, normalizer)
        val result = useCase.execute(
            1L, "SKU-102", null, "Cola", null, Money.ZERO, Money.of(1.0), PercentageRate.trusted(0.0), "can", 5,
            branch.id, Quantity.parse("10").getOrNull()!!, "tester", null, 1000L,
        )
        assertIs<DomainResult.Failure>(result)
        assertIs<RepositoryError.ValidationFailed>(result.error)
        assertNull(productRepo.getBySku(1L, "SKU-102"), "rejected creation must leave no product row -- real atomicity, not a partial write")
    }

    @Test
    fun retryWithSameIdempotencyKeyReturnsOriginalProductNotADuplicate() = runTest {
        val db = newDb()
        val branchRepo = SqlDelightBranchRepository(db)
        val branch = branchRepo.insert(1L, "Main", null, null, 500L)
        val useCase = CreateProductWithInitialStockUseCase(SqlDelightProductRepository(db), SqlDelightCategoryRepository(db), branchRepo, normalizer)

        val first = useCase.execute(
            1L, "SKU-103", null, "Cola", null, Money.ZERO, Money.of(1.0), PercentageRate.trusted(0.0), "can", 5,
            branch.id, Quantity.parse("20").getOrNull()!!, "tester", "retry-key-1", 1000L,
        )
        assertIs<DomainResult.Success<*>>(first)
        val firstProduct = (first as DomainResult.Success).value

        val retry = useCase.execute(
            1L, "SKU-103", null, "Cola", null, Money.ZERO, Money.of(1.0), PercentageRate.trusted(0.0), "can", 5,
            branch.id, Quantity.parse("20").getOrNull()!!, "tester", "retry-key-1", 2000L,
        )
        assertIs<DomainResult.Success<*>>(retry)
        assertEquals(firstProduct.id, (retry as DomainResult.Success).value.id)

        // Exactly one movement -- the retry did not double-apply the opening stock.
        assertEquals(1, SqlDelightInventoryRepository(db).listMovementHistory(1L, firstProduct.id, 10).size)
    }
}
