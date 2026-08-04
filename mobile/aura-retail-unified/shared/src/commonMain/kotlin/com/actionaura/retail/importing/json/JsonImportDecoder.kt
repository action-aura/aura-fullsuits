package com.actionaura.retail.importing.json

import com.actionaura.retail.importing.ImportDecoder
import com.actionaura.retail.importing.ImportError
import com.actionaura.retail.importing.ImportFormat
import com.actionaura.retail.importing.ImportLimitExceeded
import com.actionaura.retail.importing.ImportLimits
import com.actionaura.retail.importing.ImportResult
import com.actionaura.retail.importing.ImportSource
import com.actionaura.retail.importing.NormalizedColumn
import com.actionaura.retail.importing.NormalizedRow
import com.actionaura.retail.importing.NormalizedTable
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive

/**
 * M5.8.6 -- real, genuinely shared KMP JSON decoder
 * (`import-parser-decision.md`'s Option-A choice: `kotlinx.serialization.json`
 * is already a real `commonMain` dependency). Supported real shapes,
 * per `import-authority-audit.md`'s own confirmation of the legacy
 * authority's actual JSON behavior (`isinstance(parsed, list)` -- a
 * flat array of row objects, nothing else):
 *
 *  1. a root JSON array of flat row objects (the real, legacy-confirmed shape);
 *  2. a root JSON object containing exactly one array-valued key whose
 *     elements are row objects (a real, intentional extension beyond the
 *     legacy authority, common in JSON export tools -- documented, not
 *     silently invented).
 *
 * A "versioned multi-entity package" shape is explicitly NOT built this
 * milestone -- no real caller requirement exists for it yet
 * (`import-json-security.md`'s own disclosed scope decision, matching
 * `RepositoryBoundaries.kt`'s established "an empty marker is the
 * honest boundary" discipline for undesigned shapes).
 */
class JsonImportDecoder : ImportDecoder {
    override val format: ImportFormat = ImportFormat.JSON

    override suspend fun decode(source: ImportSource, limits: ImportLimits): ImportResult<NormalizedTable> {
        val bytesResult = source.readBounded(limits)
        val bytes = when (bytesResult) {
            is ImportResult.Success -> bytesResult.value
            is ImportResult.Failure -> return bytesResult
        }
        if (bytes.any { it == 0.toByte() }) {
            return ImportResult.Failure(ImportError.UnsafeContent("JSON content contains a null byte"))
        }
        val text = try {
            bytes.decodeToString(throwOnInvalidSequence = true)
        } catch (e: Exception) {
            return ImportResult.Failure(ImportError.MalformedContent("JSON content is not valid UTF-8 text"))
        }

        // Real, pre-parse structural scan -- depth and duplicate-key
        // detection BEFORE handing the text to kotlinx.serialization,
        // because JsonObject silently resolves duplicate keys to the
        // LAST value once parsed (the ambiguity is already gone by
        // then) -- this scan is the only place that ambiguity is still
        // observable.
        val scanResult = JsonStructuralScanner.scan(text, limits)
        if (scanResult is ImportResult.Failure) return scanResult

        val root: JsonElement = try {
            STRICT_JSON.parseToJsonElement(text)
        } catch (e: Exception) {
            return ImportResult.Failure(ImportError.MalformedContent("JSON could not be parsed: ${e.message}"))
        }

        val rowsArray: JsonArray = when (root) {
            is JsonArray -> root
            is JsonObject -> {
                val arrayEntries = root.entries.filter { it.value is JsonArray }
                if (arrayEntries.size != 1) {
                    return ImportResult.Failure(ImportError.MalformedContent("a root JSON object must contain exactly one array-valued key (found ${arrayEntries.size})"))
                }
                arrayEntries.first().value as JsonArray
            }
            else -> return ImportResult.Failure(ImportError.MalformedContent("JSON root must be an array of row objects, or an object containing exactly one such array"))
        }

        if (rowsArray.isEmpty()) return ImportResult.Failure(ImportError.NoDataRows("JSON contains zero row objects"))
        if (rowsArray.size > limits.maxJsonArrayLength) {
            return ImportResult.Failure(ImportError.LimitExceeded(ImportLimitExceeded("maxJsonArrayLength", limits.maxJsonArrayLength.toLong(), rowsArray.size.toLong())))
        }
        if (rowsArray.size > limits.maxRowCount) {
            return ImportResult.Failure(ImportError.LimitExceeded(ImportLimitExceeded("maxRowCount", limits.maxRowCount.toLong(), rowsArray.size.toLong())))
        }

        val firstRow = rowsArray[0]
        if (firstRow !is JsonObject) return ImportResult.Failure(ImportError.MalformedContent("every element of the row array must be a JSON object"))
        val headerOrder = firstRow.keys.toList()
        if (headerOrder.isEmpty()) return ImportResult.Failure(ImportError.NoHeaders("the first row object has no keys"))
        if (headerOrder.size > limits.maxColumnCount) {
            return ImportResult.Failure(ImportError.LimitExceeded(ImportLimitExceeded("maxColumnCount", limits.maxColumnCount.toLong(), headerOrder.size.toLong())))
        }
        for (h in headerOrder) {
            if (h.length > limits.maxHeaderLength) {
                return ImportResult.Failure(ImportError.LimitExceeded(ImportLimitExceeded("maxHeaderLength", limits.maxHeaderLength.toLong(), h.length.toLong())))
            }
        }
        val columns = headerOrder.mapIndexed { i, h -> NormalizedColumn(i, h) }

        val rows = mutableListOf<NormalizedRow>()
        for ((idx, element) in rowsArray.withIndex()) {
            if (element !is JsonObject) return ImportResult.Failure(ImportError.MalformedContent("row ${idx + 1} is not a JSON object"))
            val cells = MutableList<String?>(columns.size) { null }
            for ((i, key) in headerOrder.withIndex()) {
                val v = element[key] ?: continue // key absent from this row -- stays null
                if (v is JsonObject || v is JsonArray) {
                    return ImportResult.Failure(ImportError.UnsafeContent("row ${idx + 1}, field '$key': nested objects/arrays are not a supported cell value"))
                }
                if (v is JsonPrimitive && !v.isString && v.content in NON_FINITE_LITERALS) {
                    // Real, executed finding: kotlinx.serialization.json parses
                    // bare NaN/Infinity/-Infinity tokens successfully even with
                    // `isLenient = false` -- that flag does not govern this.
                    // Checked directly against the raw literal text here
                    // instead of trusting `doubleOrNull` (which returns null
                    // for these tokens, not a real Double.NaN/Infinity,
                    // making it an ineffective backstop).
                    return ImportResult.Failure(ImportError.UnsafeContent("row ${idx + 1}, field '$key': non-finite numeric literal \"${v.content}\" is not a supported value"))
                }
                val text2 = jsonScalarToText(v) // null here means a real JSON null value, not an error
                if (text2 != null && text2.length > limits.maxCellLength) {
                    return ImportResult.Failure(ImportError.LimitExceeded(ImportLimitExceeded("maxCellLength", limits.maxCellLength.toLong(), text2.length.toLong())))
                }
                cells[i] = text2
            }
            rows += NormalizedRow((idx + 2).toLong(), cells)
        }

        return ImportResult.Success(NormalizedTable(columns, rows, ImportFormat.JSON))
    }

    /**
     * Real, exact textual representation -- a JSON number is converted
     * to its own canonical text form via `contentOrNull` when it IS
     * text-backed (kotlinx.serialization retains the original literal
     * for numbers), never round-tripped through `Double`
     * (M5.8.10's own "never parse JSON numbers through Double and then
     * convert to Money" instruction) -- money/quantity parsing consumes
     * this raw text through the real M3 parsers, not this decoder.
     */
    private fun jsonScalarToText(element: JsonElement): String? {
        return when (element) {
            is JsonNull -> null
            is JsonPrimitive -> element.content
            else -> null // JsonObject/JsonArray as a cell value -- not a supported flat row shape
        }
    }

    companion object {
        // isLenient=false does NOT reject bare NaN/Infinity/-Infinity tokens
        // (real, executed finding) -- those are rejected explicitly above via
        // NON_FINITE_LITERALS. Duplicate keys are handled by the pre-parse
        // JsonStructuralScanner (see decode() above), not by this parser.
        private val STRICT_JSON = Json {
            isLenient = false
            ignoreUnknownKeys = true
        }
        private val NON_FINITE_LITERALS = setOf("NaN", "Infinity", "-Infinity", "+Infinity")
    }
}
