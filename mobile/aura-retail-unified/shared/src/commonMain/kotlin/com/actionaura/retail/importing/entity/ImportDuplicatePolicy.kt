package com.actionaura.retail.importing.entity

import com.actionaura.retail.importing.ImportDuplicate
import com.actionaura.retail.importing.ImportDuplicateDecision
import com.actionaura.retail.importing.ImportDuplicateScope
import com.actionaura.retail.importing.ImportEntityType

/**
 * M5.8.11 -- real duplicate/conflict policy. Reuses the DECISION
 * DISCIPLINE the durable M5.5 `CatalogImporter` authority already
 * established (deterministic winner by row order, never a silent
 * merge, every non-trivial decision produces a real, auditable
 * `ImportDuplicate` record) -- this is not a second policy, it is the
 * same policy's real decision matrix applied to Import Center's own 5
 * entities, since M5.5's own dedup authority is scoped to the legacy-
 * database migration path (Product barcode/SKU only), not Import
 * Center's general file-based import.
 *
 * Real, per-entity dedup key, matching `import-handler-matrix.md`'s own
 * audited legacy behavior:
 */
object ImportDuplicatePolicy {

    fun dedupKeyField(entityType: ImportEntityType): String? = when (entityType) {
        ImportEntityType.PRODUCTS -> "sku"
        ImportEntityType.CUSTOMERS -> "email"
        ImportEntityType.SUPPLIERS, ImportEntityType.BRANCHES, ImportEntityType.CATEGORIES -> "name"
    }

    /**
     * Real, deterministic within-file duplicate detection: the FIRST row
     * to claim a key wins: every later row with the same normalized key
     * is a real, reported duplicate. `null`/blank keys never collide
     * with each other (matching Customers' own real legacy blank-email
     * behavior: no reliable dedup key -> never treated as a duplicate of
     * another blank-email row either).
     */
    fun classifyWithinFile(
        entityType: ImportEntityType,
        rowNumber: Long,
        normalizedKey: String?,
        seenKeys: MutableMap<String, Long>,
    ): ImportDuplicate? {
        if (normalizedKey.isNullOrEmpty()) return null
        val firstRow = seenKeys[normalizedKey]
        if (firstRow != null) {
            return ImportDuplicate(rowNumber, entityType, ImportDuplicateScope.WITHIN_FILE, dedupKeyField(entityType) ?: "name", normalizedKey, ImportDuplicateDecision.SKIP)
        }
        seenKeys[normalizedKey] = rowNumber
        return null
    }

    /**
     * Real, per-entity policy for a row that survived within-file dedup
     * and has a real existing-database match:
     *
     *  - Products: `UPDATE_EXISTING` (matches the real, tested legacy
     *    behavior -- `test_reimporting_same_sku_updates_not_duplicates`).
     *  - Customers: `UPDATE_EXISTING` when a non-blank email matched (a
     *    blank-email row never reaches this function at all -- see
     *    `import-duplicate-conflict-policy.md`'s own disclosed
     *    limitation note).
     *  - Suppliers/Branches/Categories: `SKIP` -- matches the real
     *    legacy behavior exactly (`import-handler-matrix.md`'s own
     *    audit: these three handlers never update on a name match), and
     *    is the deliberately SAFER default for a bulk import (never
     *    silently overwrites real existing Supplier/Branch/Category
     *    data from an uploaded file).
     */
    // `existingId` is only ever null-checked here, never compared/stored --
    // `Any?` (rather than `Long?`) lets every entity's own real id type
    // pass through unchanged, including Category's TEXT/UUID id
    // (M-sync's own categories.id migration) alongside every other
    // entity's still-`Long` id, without this function needing to know or
    // care which.
    fun classifyAgainstDatabase(entityType: ImportEntityType, rowNumber: Long, matchedKey: String, existingId: Any?): ImportDuplicate? {
        if (existingId == null) return null
        val decision = when (entityType) {
            ImportEntityType.PRODUCTS, ImportEntityType.CUSTOMERS -> ImportDuplicateDecision.UPDATE_EXISTING
            ImportEntityType.SUPPLIERS, ImportEntityType.BRANCHES, ImportEntityType.CATEGORIES -> ImportDuplicateDecision.SKIP
        }
        return ImportDuplicate(rowNumber, entityType, ImportDuplicateScope.AGAINST_DATABASE, dedupKeyField(entityType) ?: "name", matchedKey, decision)
    }
}
