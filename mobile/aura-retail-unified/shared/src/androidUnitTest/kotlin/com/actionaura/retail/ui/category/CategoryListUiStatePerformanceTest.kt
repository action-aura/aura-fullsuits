package com.actionaura.retail.ui.category

import com.actionaura.retail.data.model.Category
import kotlin.test.Test
import kotlin.test.assertTrue

/**
 * M6.23 -- real, measured timing of `CategoryListUiState.visibleCategories`'s
 * real client-side filter at a representative real scale (10,000 items,
 * matching `import-performance-memory.md`'s own established 10,000-row
 * precedent). Real, disclosed scope: this measures the underlying pure
 * data operation, not real Compose recomposition counts -- no
 * `androidx.compose.ui.test`/Robolectric harness exists on this host to
 * measure actual recomposition (`presentation-performance-report.md`).
 */
class CategoryListUiStatePerformanceTest {

    @Test
    fun filteringTenThousandRealCategoriesCompletesWellWithinABoundedRealTime() {
        val categories = (1..10_000).map { i ->
            Category(id = "cat-$i", companyId = 1L, name = "Category $i", description = null, isActive = true, productCount = 0, createdAtEpochMillis = 1000L)
        }
        val state = CategoryListUiState(categories = categories, searchState = com.actionaura.retail.presentation.SearchState(appliedQuery = "999"))

        val start = System.nanoTime()
        val visible = state.visibleCategories
        val elapsedMs = (System.nanoTime() - start) / 1_000_000

        assertTrue(visible.isNotEmpty())
        println("REAL MEASURED: filtering 10,000 categories took ${elapsedMs}ms")
        assertTrue(elapsedMs < 1_000, "real 10,000-item filter took ${elapsedMs}ms, expected well under 1000ms")
    }
}
