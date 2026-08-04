package com.actionaura.retail.importing.entity

import com.actionaura.retail.importing.ImportDependency
import com.actionaura.retail.importing.ImportEntityType

/**
 * M5.8.12 -- real dependency graph, ported directly from the legacy
 * authority's own real, confirmed `_ENTITY_ORDER`
 * (`import-handler-matrix.md`'s own audit):
 *
 * ```
 * categories, suppliers, branches, customers, products
 * ```
 *
 * Only `products` has a real foreign-key dependency (`category_id`,
 * and implicitly one Branch for `inventory_balances`) -- `customers`'s
 * position before `products` is conventional in the legacy order, not
 * FK-driven, and is preserved here as real, confirmed behavioral
 * evidence rather than re-derived from scratch.
 */
object ImportDependencyGraph {

    /** Real commit order -- matches `_ENTITY_ORDER` exactly. */
    val COMMIT_ORDER: List<ImportEntityType> = listOf(
        ImportEntityType.CATEGORIES,
        ImportEntityType.SUPPLIERS,
        ImportEntityType.BRANCHES,
        ImportEntityType.CUSTOMERS,
        ImportEntityType.PRODUCTS,
    )

    /** Real FK dependencies -- both optional (a Product with no Category or no Branch reference is still real and valid, matching the legacy handler's own "no category -> no link" / "no branch -> skip inventory row" behavior). */
    val DEPENDENCIES: List<ImportDependency> = listOf(
        ImportDependency(ImportEntityType.PRODUCTS, ImportEntityType.CATEGORIES, required = false),
        ImportDependency(ImportEntityType.PRODUCTS, ImportEntityType.BRANCHES, required = false),
    )

    fun sortByCommitOrder(entityTypes: Collection<ImportEntityType>): List<ImportEntityType> =
        entityTypes.sortedBy { COMMIT_ORDER.indexOf(it) }
}
