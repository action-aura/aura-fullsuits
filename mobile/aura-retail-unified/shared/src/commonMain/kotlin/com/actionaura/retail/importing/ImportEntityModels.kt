package com.actionaura.retail.importing

/**
 * M5.8.1/M5.8.9 -- real, confirmed entity list from `import-authority-audit.md`'s
 * audit of the legacy `_HANDLERS` dict. Not Sales/Invoice, not a
 * standalone Inventory entity.
 */
enum class ImportEntityType { CATEGORIES, SUPPLIERS, BRANCHES, CUSTOMERS, PRODUCTS }

/** How a mapped column's raw text is parsed into a domain value -- always a canonical M3/M5 parser, never import-only financial parsing (M5.8.10). */
enum class ImportFieldParser { TEXT, MONEY, PERCENTAGE_RATE, QUANTITY, QUANTITY_ZERO_OR_MORE, INTEGER, EMAIL, STATUS }

data class ImportFieldDefinition(
    val key: String,
    val label: String,
    val required: Boolean,
    val parser: ImportFieldParser,
)

data class ImportMappingVersion(val value: Int) {
    companion object {
        val CURRENT = ImportMappingVersion(1)
    }
}

/** `fieldKeyToColumnIndex[key] == null` means that field is explicitly unmapped (not "not yet decided"). */
data class ImportColumnMapping(
    val entityType: ImportEntityType,
    val mappingVersion: ImportMappingVersion,
    val fieldKeyToColumnIndex: Map<String, Int?>,
)

/** M5.8.9 -- one entity's real detection result against one `NormalizedTable`. */
data class ImportEntityCandidate(
    val entityType: ImportEntityType,
    val confidence: Double,
    val supportingColumns: List<String>,
    val missingRequiredColumns: List<String>,
    val conflictingColumns: List<String>,
    val suggestedMapping: ImportColumnMapping,
)
