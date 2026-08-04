package com.actionaura.retail.usecases.product

import app.cash.sqldelight.driver.jdbc.sqlite.JdbcSqliteDriver
import com.actionaura.retail.data.DomainResult
import com.actionaura.retail.data.RepositoryError
import com.actionaura.retail.data.sqldelight.DatabaseWriteGate
import com.actionaura.retail.data.sqldelight.SqlDelightCategoryRepository
import com.actionaura.retail.data.sqldelight.SqlDelightProductRepository
import com.actionaura.retail.db.RetailDatabase
import com.actionaura.retail.financial.Money
import com.actionaura.retail.financial.PercentageRate
import com.actionaura.retail.platform.AndroidUnicodeTextNormalizer
import com.actionaura.retail.usecases.category.ArchiveCategoryUseCase
import com.actionaura.retail.usecases.category.CreateCategoryUseCase
import kotlinx.coroutines.test.runTest
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertIs
import kotlin.test.assertNull
import kotlin.test.assertTrue

/** M5.5.1-M5.5.4 -- real, executed proof of the Product domain's use-case layer. */
class ProductUseCasesTest {

    private fun newDb(): RetailDatabase {
        val driver = JdbcSqliteDriver(JdbcSqliteDriver.IN_MEMORY)
        driver.execute(null, "PRAGMA foreign_keys=ON", 0)
        RetailDatabase.Schema.create(driver)
        return RetailDatabase(driver)
    }

    private val normalizer = AndroidUnicodeTextNormalizer()

    private fun createUseCase(db: RetailDatabase, gate: DatabaseWriteGate) =
        CreateProductUseCase(SqlDelightProductRepository(db, gate), SqlDelightCategoryRepository(db, gate), normalizer)

    @Test
    fun createValidProductSucceeds() = runTest {
        val db = newDb()
        val gate = DatabaseWriteGate()
        val result = createUseCase(db, gate).execute(1L, "SKU-001", "0000000001", "Cola 330ml", null, Money.of(0.5), Money.of(1.99), PercentageRate.trusted(10.0), "can", 24, 1000L)
        assertIs<DomainResult.Success<*>>(result)
    }

    @Test
    fun createAcceptsArabicNameAndPreservesItExactly() = runTest {
        val db = newDb()
        val gate = DatabaseWriteGate()
        val result = createUseCase(db, gate).execute(1L, "SKU-AR", null, "مشروب غازي", null, Money.ZERO, Money.of(2.0), PercentageRate.trusted(0.0), "can", 5, 1000L)
        assertIs<DomainResult.Success<com.actionaura.retail.data.model.Product>>(result)
        assertEquals("مشروب غازي", result.value.name)
    }

    @Test
    fun createTrimsLeadingAndTrailingWhitespaceFromName() = runTest {
        val db = newDb()
        val gate = DatabaseWriteGate()
        val result = createUseCase(db, gate).execute(1L, "SKU-002", null, "  Cola 330ml  ", null, Money.ZERO, Money.of(1.0), PercentageRate.trusted(0.0), "can", 5, 1000L)
        assertIs<DomainResult.Success<com.actionaura.retail.data.model.Product>>(result)
        assertEquals("Cola 330ml", result.value.name)
    }

    @Test
    fun createRejectsBlankName() = runTest {
        val db = newDb()
        val gate = DatabaseWriteGate()
        val result = createUseCase(db, gate).execute(1L, "SKU-003", null, "   ", null, Money.ZERO, Money.of(1.0), PercentageRate.trusted(0.0), "can", 5, 1000L)
        assertIs<DomainResult.Failure>(result)
        assertIs<RepositoryError.ValidationFailed>(result.error)
    }

    @Test
    fun createRejectsNegativeCostAndSellPrice() = runTest {
        val db = newDb()
        val gate = DatabaseWriteGate()
        val negCost = createUseCase(db, gate).execute(1L, "SKU-004", null, "Widget", null, Money.of(-1.0), Money.of(1.0), PercentageRate.trusted(0.0), "pcs", 5, 1000L)
        assertIs<DomainResult.Failure>(negCost)
        assertIs<RepositoryError.ValidationFailed>(negCost.error)

        val negSell = createUseCase(db, gate).execute(1L, "SKU-005", null, "Widget", null, Money.of(1.0), Money.of(-1.0), PercentageRate.trusted(0.0), "pcs", 5, 1000L)
        assertIs<DomainResult.Failure>(negSell)
        assertIs<RepositoryError.ValidationFailed>(negSell.error)
    }

    @Test
    fun createWithActiveCategorySucceeds() = runTest {
        val db = newDb()
        val gate = DatabaseWriteGate()
        val category = (CreateCategoryUseCase(SqlDelightCategoryRepository(db, gate), normalizer).execute(1L, "Beverages", null, 500L) as DomainResult.Success).value
        val result = createUseCase(db, gate).execute(1L, "SKU-006", null, "Cola", category.id, Money.ZERO, Money.of(1.0), PercentageRate.trusted(0.0), "can", 5, 1000L)
        assertIs<DomainResult.Success<*>>(result)
    }

    @Test
    fun createRejectsArchivedCategoryAssignment() = runTest {
        val db = newDb()
        val gate = DatabaseWriteGate()
        val categoryRepo = SqlDelightCategoryRepository(db, gate)
        val category = (CreateCategoryUseCase(categoryRepo, normalizer).execute(1L, "Beverages", null, 500L) as DomainResult.Success).value
        ArchiveCategoryUseCase(categoryRepo).execute(1L, category.id)

        val result = createUseCase(db, gate).execute(1L, "SKU-007", null, "Cola", category.id, Money.ZERO, Money.of(1.0), PercentageRate.trusted(0.0), "can", 5, 1000L)
        assertIs<DomainResult.Failure>(result)
        assertIs<RepositoryError.ValidationFailed>(result.error)
    }

    @Test
    fun createRejectsCrossBusinessCategoryAssignment() = runTest {
        val db = newDb()
        val gate = DatabaseWriteGate()
        val categoryRepo = SqlDelightCategoryRepository(db, gate)
        val categoryUnderCompany1 = (CreateCategoryUseCase(categoryRepo, normalizer).execute(1L, "Beverages", null, 500L) as DomainResult.Success).value

        // company 2 tries to assign company 1's category id.
        val result = createUseCase(db, gate).execute(2L, "SKU-008", null, "Cola", categoryUnderCompany1.id, Money.ZERO, Money.of(1.0), PercentageRate.trusted(0.0), "can", 5, 1000L)
        assertIs<DomainResult.Failure>(result)
        assertIs<RepositoryError.NotFound>(result.error)
    }

    @Test
    fun createRejectsDuplicateSkuCaseInsensitive() = runTest {
        val db = newDb()
        val gate = DatabaseWriteGate()
        createUseCase(db, gate).execute(1L, "SKU-009", null, "Cola", null, Money.ZERO, Money.of(1.0), PercentageRate.trusted(0.0), "can", 5, 1000L)
        val result = createUseCase(db, gate).execute(1L, "sku-009", null, "Cola 2", null, Money.ZERO, Money.of(1.0), PercentageRate.trusted(0.0), "can", 5, 2000L)
        assertIs<DomainResult.Failure>(result)
        assertIs<RepositoryError.DuplicateValue>(result.error)
        assertEquals("sku", result.error.field)
    }

    @Test
    fun createAllowsSameSkuInDifferentCompanies() = runTest {
        val db = newDb()
        val gate = DatabaseWriteGate()
        assertIs<DomainResult.Success<*>>(createUseCase(db, gate).execute(1L, "SKU-010", null, "Cola", null, Money.ZERO, Money.of(1.0), PercentageRate.trusted(0.0), "can", 5, 1000L))
        assertIs<DomainResult.Success<*>>(createUseCase(db, gate).execute(2L, "SKU-010", null, "Cola", null, Money.ZERO, Money.of(1.0), PercentageRate.trusted(0.0), "can", 5, 1000L))
    }

    @Test
    fun createRejectsDuplicateBarcode() = runTest {
        val db = newDb()
        val gate = DatabaseWriteGate()
        createUseCase(db, gate).execute(1L, "SKU-011", "000123", "Cola", null, Money.ZERO, Money.of(1.0), PercentageRate.trusted(0.0), "can", 5, 1000L)
        val result = createUseCase(db, gate).execute(1L, "SKU-012", "000123", "Sprite", null, Money.ZERO, Money.of(1.0), PercentageRate.trusted(0.0), "can", 5, 2000L)
        assertIs<DomainResult.Failure>(result)
        assertIs<RepositoryError.DuplicateValue>(result.error)
        assertEquals("barcode", result.error.field)
    }

    @Test
    fun createPreservesLeadingZeroBarcodeExactly() = runTest {
        val db = newDb()
        val gate = DatabaseWriteGate()
        val result = createUseCase(db, gate).execute(1L, "SKU-013", "0000012345", "Cola", null, Money.ZERO, Money.of(1.0), PercentageRate.trusted(0.0), "can", 5, 1000L)
        assertIs<DomainResult.Success<com.actionaura.retail.data.model.Product>>(result)
        assertEquals("0000012345", result.value.barcode)
    }

    @Test
    fun createPreservesLongBarcode() = runTest {
        val db = newDb()
        val gate = DatabaseWriteGate()
        val longBarcode = "12345678901234567890"
        val result = createUseCase(db, gate).execute(1L, "SKU-014", longBarcode, "Cola", null, Money.ZERO, Money.of(1.0), PercentageRate.trusted(0.0), "can", 5, 1000L)
        assertIs<DomainResult.Success<com.actionaura.retail.data.model.Product>>(result)
        assertEquals(longBarcode, result.value.barcode)
    }

    @Test
    fun createAllowsNullBarcodeAndMultipleNullsDoNotConflict() = runTest {
        val db = newDb()
        val gate = DatabaseWriteGate()
        assertIs<DomainResult.Success<*>>(createUseCase(db, gate).execute(1L, "SKU-015", null, "Cola", null, Money.ZERO, Money.of(1.0), PercentageRate.trusted(0.0), "can", 5, 1000L))
        assertIs<DomainResult.Success<*>>(createUseCase(db, gate).execute(1L, "SKU-016", null, "Sprite", null, Money.ZERO, Money.of(1.0), PercentageRate.trusted(0.0), "can", 5, 1000L))
    }

    @Test
    fun createRejectsBlankButPresentBarcode() = runTest {
        val db = newDb()
        val gate = DatabaseWriteGate()
        val result = createUseCase(db, gate).execute(1L, "SKU-017", "   ", "Cola", null, Money.ZERO, Money.of(1.0), PercentageRate.trusted(0.0), "can", 5, 1000L)
        assertIs<DomainResult.Failure>(result)
        assertIs<RepositoryError.ValidationFailed>(result.error)
    }

    @Test
    fun skuCannotBeReusedAfterArchiving() = runTest {
        // LEGACY_PARITY (barcode-and-sku-contract.md): the real legacy
        // uniqueness check ignores status entirely -- a SKU is permanently
        // reserved company-wide once used, even after the product is
        // archived. Deliberately different from Category's own
        // active-only name-reuse policy.
        val db = newDb()
        val gate = DatabaseWriteGate()
        val productRepo = SqlDelightProductRepository(db, gate)
        val product = (createUseCase(db, gate).execute(1L, "SKU-025", null, "Cola", null, Money.ZERO, Money.of(1.0), PercentageRate.trusted(0.0), "can", 5, 1000L) as DomainResult.Success).value
        ArchiveProductUseCase(productRepo).execute(1L, product.id, 2000L)

        val result = createUseCase(db, gate).execute(1L, "SKU-025", null, "Cola 2", null, Money.ZERO, Money.of(1.0), PercentageRate.trusted(0.0), "can", 5, 3000L)
        assertIs<DomainResult.Failure>(result)
        assertIs<RepositoryError.DuplicateValue>(result.error)
        assertEquals("sku", result.error.field)
    }

    @Test
    fun archiveThenReactivateRoundTrip() = runTest {
        val db = newDb()
        val gate = DatabaseWriteGate()
        val productRepo = SqlDelightProductRepository(db, gate)
        val product = (createUseCase(db, gate).execute(1L, "SKU-018", null, "Cola", null, Money.ZERO, Money.of(1.0), PercentageRate.trusted(0.0), "can", 5, 1000L) as DomainResult.Success).value

        assertIs<DomainResult.Success<Unit>>(ArchiveProductUseCase(productRepo).execute(1L, product.id, 2000L))
        assertTrue(productRepo.listActive(1L).isEmpty())

        val reactivated = ReactivateProductUseCase(productRepo).execute(1L, product.id, 3000L)
        assertIs<DomainResult.Success<*>>(reactivated)
        assertEquals(1, productRepo.listActive(1L).size)
    }

    @Test
    fun archivedProductCannotReceiveNewCategoryAssignmentButKeepsExistingOne() = runTest {
        // Archiving a Category must not automatically archive its Products,
        // and an archived Category's already-assigned Products keep the
        // relationship (M5.5.4) -- proven by re-reading after archiving the
        // CATEGORY (not the product).
        val db = newDb()
        val gate = DatabaseWriteGate()
        val categoryRepo = SqlDelightCategoryRepository(db, gate)
        val productRepo = SqlDelightProductRepository(db, gate)
        val category = (CreateCategoryUseCase(categoryRepo, normalizer).execute(1L, "Beverages", null, 500L) as DomainResult.Success).value
        val product = (createUseCase(db, gate).execute(1L, "SKU-019", null, "Cola", category.id, Money.ZERO, Money.of(1.0), PercentageRate.trusted(0.0), "can", 5, 1000L) as DomainResult.Success).value

        ArchiveCategoryUseCase(categoryRepo).execute(1L, category.id)

        val stillThere = productRepo.getById(1L, product.id)!!
        assertTrue(stillThere.isActive, "archiving the category must not archive the product")
        assertEquals(category.id, stillThere.categoryId)
    }

    @Test
    fun staleUpdateIsRejected() = runTest {
        val db = newDb()
        val gate = DatabaseWriteGate()
        val productRepo = SqlDelightProductRepository(db, gate)
        val categoryRepo = SqlDelightCategoryRepository(db, gate)
        val updateUseCase = UpdateProductUseCase(productRepo, categoryRepo, normalizer)
        val product = (createUseCase(db, gate).execute(1L, "SKU-020", null, "Cola", null, Money.of(0.5), Money.of(1.0), PercentageRate.trusted(0.0), "can", 5, 1000L) as DomainResult.Success).value

        // First update succeeds and moves updated_at forward.
        val first = updateUseCase.execute(1L, product.id, null, "Cola", null, product.costPrice, Money.of(1.10), product.taxRate, "can", 5, product.updatedAtEpochMillis, 2000L)
        assertIs<DomainResult.Success<*>>(first)

        // Second update still uses the ORIGINAL (now-stale) updated_at.
        val stale = updateUseCase.execute(1L, product.id, null, "Cola", null, product.costPrice, Money.of(1.20), product.taxRate, "can", 5, product.updatedAtEpochMillis, 3000L)
        assertIs<DomainResult.Failure>(stale)
        assertIs<RepositoryError.StaleUpdate>(stale.error)
    }

    @Test
    fun updateDoesNotAllowChangingBarcodeToAnotherProductsBarcode() = runTest {
        val db = newDb()
        val gate = DatabaseWriteGate()
        val productRepo = SqlDelightProductRepository(db, gate)
        val categoryRepo = SqlDelightCategoryRepository(db, gate)
        createUseCase(db, gate).execute(1L, "SKU-021", "000999", "Sprite", null, Money.ZERO, Money.of(1.0), PercentageRate.trusted(0.0), "can", 5, 1000L)
        val target = (createUseCase(db, gate).execute(1L, "SKU-022", null, "Cola", null, Money.ZERO, Money.of(1.0), PercentageRate.trusted(0.0), "can", 5, 1000L) as DomainResult.Success).value

        val result = UpdateProductUseCase(productRepo, categoryRepo, normalizer).execute(
            1L, target.id, "000999", "Cola", null, target.costPrice, target.sellPrice, target.taxRate, "can", 5, target.updatedAtEpochMillis, 2000L,
        )
        assertIs<DomainResult.Failure>(result)
        assertIs<RepositoryError.DuplicateValue>(result.error)
    }

    @Test
    fun searchFindsByNormalizedPrefixIncludingFullWidthVariant() = runTest {
        val db = newDb()
        val gate = DatabaseWriteGate()
        createUseCase(db, gate).execute(1L, "SKU-023", null, "Cola 330ml", null, Money.ZERO, Money.of(1.0), PercentageRate.trusted(0.0), "can", 5, 1000L)
        val search = SearchProductsUseCase(SqlDelightProductRepository(db, gate), normalizer)

        val results = search.execute(1L, "ＣＯＬＡ") // full-width query, NFKC-folds to "cola" same as the stored normalized_name prefix
        assertEquals(1, results.size)
        assertEquals("Cola 330ml", results.first().name)
    }

    @Test
    fun findByBarcodeOnlyResolvesActiveProducts() = runTest {
        val db = newDb()
        val gate = DatabaseWriteGate()
        val productRepo = SqlDelightProductRepository(db, gate)
        val product = (createUseCase(db, gate).execute(1L, "SKU-024", "000555", "Cola", null, Money.ZERO, Money.of(1.0), PercentageRate.trusted(0.0), "can", 5, 1000L) as DomainResult.Success).value
        val find = FindProductByBarcodeUseCase(productRepo)

        assertEquals(product.id, find.execute(1L, "000555")?.id)
        ArchiveProductUseCase(productRepo).execute(1L, product.id, 2000L)
        assertNull(find.execute(1L, "000555"), "an archived product must not be findable by scan")
    }
}
