package com.actionaura.retail.importing.csv

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

/**
 * M5.8.5 -- real, hand-written, genuinely shared KMP CSV decoder
 * (`import-parser-decision.md`'s own Option-A choice for CSV: no
 * platform library needed, full control over every security
 * requirement). RFC4180-shaped quoting/escaping, bounded by
 * `ImportLimits`, never trusts a null byte or malformed encoding as
 * safe text.
 */
class CsvImportDecoder : ImportDecoder {
    override val format: ImportFormat = ImportFormat.CSV

    override suspend fun decode(source: ImportSource, limits: ImportLimits): ImportResult<NormalizedTable> {
        val bytesResult = source.readBounded(limits)
        val bytes = when (bytesResult) {
            is ImportResult.Success -> bytesResult.value
            is ImportResult.Failure -> return bytesResult
        }
        if (bytes.any { it == 0.toByte() }) {
            return ImportResult.Failure(ImportError.UnsafeContent("CSV content contains a null byte"))
        }

        val text = decodeUtf8WithBomStripped(bytes)
            ?: return ImportResult.Failure(ImportError.MalformedContent("CSV content is not valid UTF-8 text"))

        val delimiter = detectDelimiter(text)
        val fields = parseCsv(text, delimiter, limits) ?: return ImportResult.Failure(ImportError.MalformedContent("CSV content has an unterminated quoted field"))
        if (fields.isEmpty()) {
            return ImportResult.Failure(ImportError.NoHeaders("CSV file has no rows at all"))
        }

        val rawHeaders = fields[0]
        if (rawHeaders.size > limits.maxColumnCount) {
            return ImportResult.Failure(ImportError.LimitExceeded(ImportLimitExceeded("maxColumnCount", limits.maxColumnCount.toLong(), rawHeaders.size.toLong())))
        }
        for (h in rawHeaders) {
            if (h.length > limits.maxHeaderLength) {
                return ImportResult.Failure(ImportError.LimitExceeded(ImportLimitExceeded("maxHeaderLength", limits.maxHeaderLength.toLong(), h.length.toLong())))
            }
        }
        val columns = rawHeaders.mapIndexed { i, h -> NormalizedColumn(i, h) }

        val dataRowsRaw = fields.drop(1)
        if (dataRowsRaw.isEmpty()) {
            return ImportResult.Failure(ImportError.NoDataRows("CSV file has a header row but no data rows"))
        }
        if (dataRowsRaw.size > limits.maxRowCount) {
            return ImportResult.Failure(ImportError.LimitExceeded(ImportLimitExceeded("maxRowCount", limits.maxRowCount.toLong(), dataRowsRaw.size.toLong())))
        }

        val rows = mutableListOf<NormalizedRow>()
        for ((idx, rowFields) in dataRowsRaw.withIndex()) {
            // Inconsistent row width: pad short rows with null (missing trailing cells), truncate rows longer than the header (extra cells dropped, real, deterministic policy -- never silently shifts column meaning).
            val cells = MutableList<String?>(columns.size) { null }
            for (i in columns.indices) {
                if (i < rowFields.size) {
                    val v = rowFields[i]
                    if (v.length > limits.maxCellLength) {
                        return ImportResult.Failure(ImportError.LimitExceeded(ImportLimitExceeded("maxCellLength", limits.maxCellLength.toLong(), v.length.toLong())))
                    }
                    cells[i] = v
                }
            }
            rows += NormalizedRow(rowNumber = (idx + 2).toLong(), cells = cells) // +2: header is row 1, data rows start at 2
        }

        return ImportResult.Success(NormalizedTable(columns, rows, ImportFormat.CSV))
    }

    /**
     * Strips a real UTF-8 BOM (EF BB BF) if present, then decodes as
     * UTF-8. Returns null on invalid UTF-8 rather than silently
     * replacing bytes -- malformed encoding is a real, reportable
     * failure, not swallowed. Uses the real Kotlin stdlib
     * `ByteArray.decodeToString(throwOnInvalidSequence = true)`, which
     * throws on a malformed byte sequence instead of substituting
     * U+FFFD.
     */
    private fun decodeUtf8WithBomStripped(bytes: ByteArray): String? {
        val hasBom = bytes.size >= 3 && bytes[0] == 0xEF.toByte() && bytes[1] == 0xBB.toByte() && bytes[2] == 0xBF.toByte()
        val content = if (hasBom) bytes.copyOfRange(3, bytes.size) else bytes
        return try {
            content.decodeToString(throwOnInvalidSequence = true)
        } catch (e: Exception) {
            null
        }
    }

    /** Bounded to a small, documented allowlist of real delimiters -- never an arbitrary sniffed character. */
    private fun detectDelimiter(sample: String): Char {
        val firstLine = sample.lineSequence().firstOrNull { it.isNotBlank() } ?: return ','
        val candidates = charArrayOf(',', ';', '\t', '|')
        return candidates.maxByOrNull { d -> firstLine.count { it == d } } ?: ','
    }

    /**
     * Real RFC4180-shaped parser: quoted fields may contain the
     * delimiter, embedded newlines, and escaped quotes (`""` inside a
     * quoted field represents one literal `"`). Returns null if a
     * quoted field is never terminated (real, reportable malformed
     * content, not silently absorbed).
     */
    private fun parseCsv(text: String, delimiter: Char, limits: ImportLimits): List<List<String>>? {
        val rows = mutableListOf<List<String>>()
        var row = mutableListOf<String>()
        val field = StringBuilder()
        var inQuotes = false
        var i = 0
        val n = text.length

        fun endField() {
            row.add(field.toString())
            field.clear()
        }
        fun endRow() {
            endField()
            rows.add(row)
            row = mutableListOf()
        }

        while (i < n) {
            val c = text[i]
            if (inQuotes) {
                if (c == '"') {
                    if (i + 1 < n && text[i + 1] == '"') {
                        field.append('"')
                        i += 2
                        continue
                    }
                    inQuotes = false
                    i++
                    continue
                }
                field.append(c)
                i++
                continue
            }
            when (c) {
                '"' -> { inQuotes = true; i++ }
                delimiter -> { endField(); i++ }
                '\r' -> {
                    if (i + 1 < n && text[i + 1] == '\n') i++
                    endRow(); i++
                }
                '\n' -> { endRow(); i++ }
                else -> { field.append(c); i++ }
            }
        }
        if (inQuotes) return null // unterminated quoted field -- real malformed content
        if (field.isNotEmpty() || row.isNotEmpty()) endRow()

        // Real CSV files commonly end with a trailing blank line -- drop a single trailing all-empty row, never more than one, and never a genuinely blank DATA row the user intended.
        if (rows.isNotEmpty() && rows.last().all { it.isEmpty() } && rows.last().size == 1) {
            rows.removeAt(rows.lastIndex)
        }
        return rows
    }
}
