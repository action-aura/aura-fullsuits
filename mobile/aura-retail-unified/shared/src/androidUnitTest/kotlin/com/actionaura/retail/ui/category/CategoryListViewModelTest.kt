package com.actionaura.retail.ui.category

import app.cash.sqldelight.driver.jdbc.sqlite.JdbcSqliteDriver
import com.actionaura.retail.db.RetailDatabase
import com.actionaura.retail.di.AuraAppContainer
import com.actionaura.retail.platform.AndroidUnicodeTextNormalizer
import com.actionaura.retail.platform.DatabaseDriverFactory
import com.actionaura.retail.presentation.LoadState
import kotlinx.coroutines.test.StandardTestDispatcher
import kotlinx.coroutines.test.advanceUntilIdle
import kotlinx.coroutines.test.runTest
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertTrue

/**
 * M6.16 -- real, executed proof that the Category vertical slice's
 * ViewModel drives the REAL M5.2/M5.3 use cases end-to-end against a
 * real in-memory SQLDelight database -- not a fake repository.
 */
class CategoryListViewModelTest {

    private fun newContainer(): AuraAppContainer {
        val factory = object : DatabaseDriverFactory {
            override fun createDriver(): app.cash.sqldelight.db.SqlDriver {
                val driver = JdbcSqliteDriver(JdbcSqliteDriver.IN_MEMORY)
                driver.execute(null, "PRAGMA foreign_keys=ON", 0)
                RetailDatabase.Schema.create(driver)
                return driver
            }
        }
        return AuraAppContainer(factory, AndroidUnicodeTextNormalizer(), com.actionaura.retail.securestorage.InMemorySecureBlobStore())
    }

    @Test
    fun loadingPopulatesRealCategoriesFromTheRealDatabase() = runTest {
        val container = newContainer()
        container.categoryRepository.insert(1L, "Beverages", null, 1000L)
        val viewModel = CategoryListViewModel(container, dispatcher = StandardTestDispatcher(testScheduler))

        viewModel.load()
        advanceUntilIdle()

        assertEquals(1, viewModel.state.value.categories.size)
        assertEquals(LoadState.Success, viewModel.state.value.loadState)
    }

    @Test
    fun searchFiltersTheRealVisibleListClientSide() = runTest {
        val container = newContainer()
        container.categoryRepository.insert(1L, "Beverages", null, 1000L)
        container.categoryRepository.insert(1L, "Snacks", null, 1000L)
        val viewModel = CategoryListViewModel(container, dispatcher = StandardTestDispatcher(testScheduler))
        viewModel.load()
        advanceUntilIdle()

        viewModel.onSearchQueryChange("bev")

        assertEquals(1, viewModel.state.value.visibleCategories.size)
        assertEquals("Beverages", viewModel.state.value.visibleCategories.first().name)
    }

    @Test
    fun archivingARealCategoryRemovesItFromTheRealActiveList() = runTest {
        val container = newContainer()
        val created = container.categoryRepository.insert(1L, "Beverages", null, 1000L)
        val viewModel = CategoryListViewModel(container, dispatcher = StandardTestDispatcher(testScheduler))
        viewModel.load()
        advanceUntilIdle()

        viewModel.onArchive(created.id)
        advanceUntilIdle()

        assertTrue(viewModel.state.value.categories.isEmpty())
    }

    @Test
    fun creatingADuplicateNamedCategoryThroughTheRealUseCaseSurfacesARealMessage() = runTest {
        val container = newContainer()
        container.categoryRepository.insert(1L, "Beverages", null, 1000L)
        val editViewModel = CategoryEditViewModel(container, dispatcher = StandardTestDispatcher(testScheduler))

        editViewModel.onNameChange("Beverages")
        editViewModel.onSave()
        advanceUntilIdle()

        assertEquals("error.duplicate_name", editViewModel.state.value.name.domainError?.messageKey)
    }
}
