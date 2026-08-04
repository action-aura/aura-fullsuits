package com.actionaura.retail.usecases.category

import app.cash.sqldelight.driver.jdbc.sqlite.JdbcSqliteDriver
import com.actionaura.retail.data.DomainResult
import com.actionaura.retail.data.RepositoryError
import com.actionaura.retail.data.sqldelight.SqlDelightCategoryRepository
import com.actionaura.retail.data.sqldelight.SqlDelightProductRepository
import com.actionaura.retail.db.RetailDatabase
import com.actionaura.retail.financial.Money
import com.actionaura.retail.financial.PercentageRate
import com.actionaura.retail.platform.AndroidUnicodeTextNormalizer
import kotlinx.coroutines.test.runTest
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertIs
import kotlin.test.assertTrue

/**
 * M5.3 -- real, executed proof of the Category domain's use-case layer:
 * dedup (including real NFKC Arabic/full-width folding, not a hand-waved
 * "case-insensitive" check), archive/reactivate semantics, and historical-
 * relationship preservation. Uses AndroidUnicodeTextNormalizer's real
 * java.text.Normalizer -- not a fake/mocked normalizer -- because the
 * whole point of this milestone is proving the real platform NFKC
 * behavior, not an assumption about it.
 */
class CategoryUseCasesTest {

    private fun newDb(): RetailDatabase {
        val driver = JdbcSqliteDriver(JdbcSqliteDriver.IN_MEMORY)
        driver.execute(null, "PRAGMA foreign_keys=ON", 0)
        RetailDatabase.Schema.create(driver)
        return RetailDatabase(driver)
    }

    private val normalizer = AndroidUnicodeTextNormalizer()

    @Test
    fun createRejectsExactDuplicateNameCaseInsensitive() = runTest {
        val repo = SqlDelightCategoryRepository(newDb())
        val create = CreateCategoryUseCase(repo, normalizer)

        val first = create.execute(1L, "Beverages", null, 1000L)
        assertIs<DomainResult.Success<*>>(first)

        val second = create.execute(1L, "BEVERAGES", null, 2000L)
        assertIs<DomainResult.Failure>(second)
        assertIs<RepositoryError.DuplicateName>(second.error)
    }

    @Test
    fun createRejectsFullWidthVariantAsDuplicateViaRealNfkcFolding() = runTest {
        // Real NFKC compatibility folding: full-width Latin letters
        // (U+FF21-FF3A range) decompose to their standard-width
        // equivalents. "ＣＯＬＡ" is full-width "COLA".
        val repo = SqlDelightCategoryRepository(newDb())
        val create = CreateCategoryUseCase(repo, normalizer)

        val first = create.execute(1L, "Cola", null, 1000L)
        assertIs<DomainResult.Success<*>>(first)

        val fullWidth = create.execute(1L, "ＣＯＬＡ", null, 2000L)
        assertIs<DomainResult.Failure>(fullWidth)
        assertIs<RepositoryError.DuplicateName>(fullWidth.error)
    }

    @Test
    fun createRejectsArabicPresentationFormVariantAsDuplicateViaRealNfkcFolding() = runTest {
        // Real NFKC compatibility folding: U+FE8D (ARABIC LETTER ALEF
        // ISOLATED FORM) folds to U+0627 (ARABIC LETTER ALEF, the standard
        // codepoint). Two real, distinct Unicode codepoints an Arabic input
        // method could plausibly produce for what a user perceives as the
        // same letter.
        val repo = SqlDelightCategoryRepository(newDb())
        val create = CreateCategoryUseCase(repo, normalizer)

        val standard = create.execute(1L, "المشروبات", null, 1000L) // المشروبات (beverages)
        assertIs<DomainResult.Success<*>>(standard)

        val presentationForm = create.execute(1L, "ﺍلمشروبات", null, 2000L)
        assertIs<DomainResult.Failure>(presentationForm)
        assertIs<RepositoryError.DuplicateName>(presentationForm.error)
    }

    @Test
    fun createAllowsGenuinelyDifferentNames() = runTest {
        val repo = SqlDelightCategoryRepository(newDb())
        val create = CreateCategoryUseCase(repo, normalizer)

        assertIs<DomainResult.Success<*>>(create.execute(1L, "Beverages", null, 1000L))
        assertIs<DomainResult.Success<*>>(create.execute(1L, "Snacks", null, 2000L))
    }

    @Test
    fun createRejectsBlankName() = runTest {
        val repo = SqlDelightCategoryRepository(newDb())
        val create = CreateCategoryUseCase(repo, normalizer)

        val result = create.execute(1L, "   ", null, 1000L)
        assertIs<DomainResult.Failure>(result)
        assertIs<RepositoryError.ValidationFailed>(result.error)
    }

    @Test
    fun archiveThenReactivateRoundTrip() = runTest {
        val repo = SqlDelightCategoryRepository(newDb())
        val create = CreateCategoryUseCase(repo, normalizer)
        val archive = ArchiveCategoryUseCase(repo)
        val reactivate = ReactivateCategoryUseCase(repo, normalizer)

        val created = (create.execute(1L, "Beverages", null, 1000L) as DomainResult.Success).value
        assertIs<DomainResult.Success<Unit>>(archive.execute(1L, created.id))
        assertTrue(repo.listActive(1L).isEmpty())

        val reactivated = reactivate.execute(1L, created.id)
        assertIs<DomainResult.Success<*>>(reactivated)
        assertEquals(1, repo.listActive(1L).size)
    }

    @Test
    fun reactivateRejectedIfADuplicateWasCreatedWhileArchived() = runTest {
        val repo = SqlDelightCategoryRepository(newDb())
        val create = CreateCategoryUseCase(repo, normalizer)
        val archive = ArchiveCategoryUseCase(repo)
        val reactivate = ReactivateCategoryUseCase(repo, normalizer)

        val original = (create.execute(1L, "Beverages", null, 1000L) as DomainResult.Success).value
        archive.execute(1L, original.id)
        // Dedup only ever sees active categories, so a second "Beverages"
        // can be created while the first is archived.
        create.execute(1L, "Beverages", null, 2000L)

        val result = reactivate.execute(1L, original.id)
        assertIs<DomainResult.Failure>(result)
        assertIs<RepositoryError.DuplicateName>(result.error)
    }

    @Test
    fun archiveIsIdempotent() = runTest {
        val repo = SqlDelightCategoryRepository(newDb())
        val create = CreateCategoryUseCase(repo, normalizer)
        val archive = ArchiveCategoryUseCase(repo)

        val created = (create.execute(1L, "Beverages", null, 1000L) as DomainResult.Success).value
        assertIs<DomainResult.Success<Unit>>(archive.execute(1L, created.id))
        assertIs<DomainResult.Success<Unit>>(archive.execute(1L, created.id))
    }

    @Test
    fun archiveUnknownCategoryReturnsNotFound() = runTest {
        val repo = SqlDelightCategoryRepository(newDb())
        val result = ArchiveCategoryUseCase(repo).execute(1L, 999L)
        assertIs<DomainResult.Failure>(result)
        assertIs<RepositoryError.NotFound>(result.error)
    }

    @Test
    fun archivingCategoryPreservesExistingProductAssociation() = runTest {
        val db = newDb()
        val categoryRepo = SqlDelightCategoryRepository(db)
        val productRepo = SqlDelightProductRepository(db)

        val category = (CreateCategoryUseCase(categoryRepo, normalizer).execute(1L, "Beverages", null, 1000L) as DomainResult.Success).value
        val product = productRepo.insert(
            1L, "SKU-001", "0000000001", "Cola 330ml", category.id,
            Money.of(0.5), Money.of(1.99), PercentageRate.trusted(10.0), "can", 24, 2000L,
        )

        ArchiveCategoryUseCase(categoryRepo).execute(1L, category.id)

        val fetched = productRepo.getById(1L, product.id)!!
        assertEquals(category.id, fetched.categoryId, "an archived category must remain the product's category_id -- no orphaning, no cascading null-out")
        assertEquals("Beverages", fetched.categoryName, "the LEFT JOIN resolves an archived category's name exactly like an active one -- status is not a join filter")
    }
}
