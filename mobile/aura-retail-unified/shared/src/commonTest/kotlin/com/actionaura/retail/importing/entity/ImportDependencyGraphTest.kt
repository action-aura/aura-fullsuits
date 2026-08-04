package com.actionaura.retail.importing.entity

import com.actionaura.retail.importing.ImportEntityType
import kotlin.test.Test
import kotlin.test.assertEquals

class ImportDependencyGraphTest {

    @Test
    fun commitOrderMatchesTheRealAuditedLegacyEntityOrder() {
        assertEquals(
            listOf(ImportEntityType.CATEGORIES, ImportEntityType.SUPPLIERS, ImportEntityType.BRANCHES, ImportEntityType.CUSTOMERS, ImportEntityType.PRODUCTS),
            ImportDependencyGraph.COMMIT_ORDER,
        )
    }

    @Test
    fun entitiesSelectedOutOfOrderAreSortedIntoTheRealCommitOrder() {
        val selected = listOf(ImportEntityType.PRODUCTS, ImportEntityType.CATEGORIES, ImportEntityType.CUSTOMERS)
        val sorted = ImportDependencyGraph.sortByCommitOrder(selected)
        assertEquals(listOf(ImportEntityType.CATEGORIES, ImportEntityType.CUSTOMERS, ImportEntityType.PRODUCTS), sorted)
    }

    @Test
    fun onlyProductsHasRealDeclaredDependencies() {
        val dependentTypes = ImportDependencyGraph.DEPENDENCIES.map { it.fromEntityType }.toSet()
        assertEquals(setOf(ImportEntityType.PRODUCTS), dependentTypes)
    }

    @Test
    fun productDependenciesOnCategoryAndBranchAreBothOptional() {
        val required = ImportDependencyGraph.DEPENDENCIES.filter { it.required }
        assertEquals(emptyList(), required, "a Product with no Category or no Branch reference is real and valid, matching the legacy handler's own behavior")
    }
}
