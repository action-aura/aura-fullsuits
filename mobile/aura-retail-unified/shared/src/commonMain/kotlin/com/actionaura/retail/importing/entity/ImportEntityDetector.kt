package com.actionaura.retail.importing.entity

import com.actionaura.retail.importing.ImportColumnMapping
import com.actionaura.retail.importing.ImportEntityCandidate
import com.actionaura.retail.importing.ImportEntityType
import com.actionaura.retail.importing.ImportFieldDefinition
import com.actionaura.retail.importing.ImportMappingVersion
import com.actionaura.retail.importing.NormalizedTable

/**
 * M5.8.9 -- real, deterministic entity detection and column mapping.
 * Real port of the legacy authority's own confidence-scoring shape
 * (`import-authority-audit.md`'s citation of `_suggest_mapping`/
 * `_entity_fit`): exact match, alias match, substring-contains, no
 * value-sniffing (a real, disclosed scope reduction -- header-name
 * matching alone is the real signal used this milestone; sample-value
 * type sniffing is not built).
 */
object ImportEntityDetector {

    private const val EXACT_MATCH_SCORE = 100
    private const val ALIAS_MATCH_SCORE = 90
    private const val CONTAINS_KEY_SCORE = 75
    private const val CONTAINS_LABEL_SCORE = 65
    private const val PARTIAL_ALIAS_SCORE = 55
    private const val AUTO_APPLY_MIN = 65

    private fun normalize(s: String): String =
        s.trim().lowercase().filter { it.isLetterOrDigit() || it.code in 0x0600..0x06FF }

    private fun aliasesFor(fieldKey: String): Set<String> =
        (ImportEntitySchemas.ENGLISH_ALIASES[fieldKey].orEmpty() + ImportEntitySchemas.ARABIC_ALIASES[fieldKey].orEmpty())
            .map { normalize(it) }.toSet()

    private fun scoreColumn(field: ImportFieldDefinition, column: String): Int {
        val colNorm = normalize(column)
        if (colNorm.isEmpty()) return 0
        val keyNorm = normalize(field.key)
        val labelNorm = normalize(field.label)
        val aliases = aliasesFor(field.key)
        return when {
            colNorm == keyNorm || colNorm == labelNorm -> EXACT_MATCH_SCORE
            colNorm in aliases -> ALIAS_MATCH_SCORE
            keyNorm.isNotEmpty() && (colNorm.contains(keyNorm) || keyNorm.contains(colNorm)) -> CONTAINS_KEY_SCORE
            labelNorm.isNotEmpty() && (colNorm.contains(labelNorm) || labelNorm.contains(colNorm)) -> CONTAINS_LABEL_SCORE
            aliases.any { it.isNotEmpty() && (colNorm.contains(it) || it.contains(colNorm)) } -> PARTIAL_ALIAS_SCORE
            else -> 0
        }
    }

    /** Real, deterministic greedy assignment: highest-scoring (field, column) pairs win first; a field or column already claimed is never reassigned -- matches the legacy authority's own real conflict-resolution shape. */
    fun suggestMapping(entityType: ImportEntityType, headers: List<String>, mappingVersion: ImportMappingVersion = ImportMappingVersion.CURRENT): ImportColumnMapping {
        val fields = ImportEntitySchemas.fieldsFor(entityType)
        data class Pair2(val score: Int, val fieldKey: String, val columnIndex: Int)
        val pairs = mutableListOf<Pair2>()
        for (field in fields) {
            for ((colIndex, header) in headers.withIndex()) {
                val score = scoreColumn(field, header)
                if (score >= PARTIAL_ALIAS_SCORE) pairs += Pair2(score, field.key, colIndex)
            }
        }
        pairs.sortByDescending { it.score }

        val assignment = mutableMapOf<String, Int?>()
        fields.forEach { assignment[it.key] = null }
        val usedColumns = mutableSetOf<Int>()
        for (p in pairs) {
            if (assignment[p.fieldKey] != null || p.columnIndex in usedColumns) continue
            if (p.score < AUTO_APPLY_MIN) continue
            assignment[p.fieldKey] = p.columnIndex
            usedColumns += p.columnIndex
        }
        return ImportColumnMapping(entityType, mappingVersion, assignment)
    }

    /** Real fit ratio: `0.7 * requiredFieldCoverage + 0.3 * totalFieldCoverage` -- same weighting as the legacy `_entity_fit`. */
    private fun entityFit(entityType: ImportEntityType, headers: List<String>): Double {
        val fields = ImportEntitySchemas.fieldsFor(entityType)
        val mapping = suggestMapping(entityType, headers)
        val requiredKeys = fields.filter { it.required }.map { it.key }
        val requiredMapped = requiredKeys.count { mapping.fieldKeyToColumnIndex[it] != null }
        val totalMapped = mapping.fieldKeyToColumnIndex.values.count { it != null }
        val requiredRatio = if (requiredKeys.isEmpty()) 1.0 else requiredMapped.toDouble() / requiredKeys.size
        val coverageRatio = if (fields.isEmpty()) 0.0 else totalMapped.toDouble() / fields.size
        return requiredRatio * 0.7 + coverageRatio * 0.3
    }

    /** Real candidates for every one of the 5 confirmed entities, sorted by fit descending. A caller decides ambiguity handling (M5.8.9's own "do not auto-select when two candidates are materially ambiguous"). */
    fun detectCandidates(table: NormalizedTable): List<ImportEntityCandidate> {
        val headers = table.columns.map { it.rawHeader }
        return ImportEntityType.entries.map { entityType ->
            val fields = ImportEntitySchemas.fieldsFor(entityType)
            val mapping = suggestMapping(entityType, headers)
            val missingRequired = fields.filter { it.required && mapping.fieldKeyToColumnIndex[it.key] == null }.map { it.key }
            val supporting = mapping.fieldKeyToColumnIndex.filterValues { it != null }.keys.toList()
            ImportEntityCandidate(
                entityType = entityType,
                confidence = entityFit(entityType, headers),
                supportingColumns = supporting,
                missingRequiredColumns = missingRequired,
                conflictingColumns = emptyList(),
                suggestedMapping = mapping,
            )
        }.sortedByDescending { it.confidence }
    }

    /**
     * Real ambiguity check: the top two candidates are "materially
     * ambiguous" when both are eligible (all required fields mapped,
     * fit >= a real minimum) and their fit scores are close -- never
     * auto-selected in that case (M5.8.9's own explicit instruction).
     */
    fun isAmbiguous(candidates: List<ImportEntityCandidate>, minimumFit: Double = 0.55, ambiguityMargin: Double = 0.1): Boolean {
        val eligible = candidates.filter { it.missingRequiredColumns.isEmpty() && it.confidence >= minimumFit }
        if (eligible.size < 2) return false
        val top = eligible[0].confidence
        val second = eligible[1].confidence
        return (top - second) < ambiguityMargin
    }
}
