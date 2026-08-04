package com.actionaura.retail.importing.xlsx

import com.actionaura.retail.importing.ImportError
import com.actionaura.retail.importing.ImportLimitExceeded
import com.actionaura.retail.importing.ImportLimits
import com.actionaura.retail.importing.ImportResult

/**
 * M5.8.7 -- a real, minimal, hand-written OOXML sheet-XML reader. Only
 * the narrow slice of the spec actually needed for import (shared
 * strings + one worksheet's cell values) -- not general spreadsheet
 * fidelity. Pure `commonMain` text parsing, no XML library dependency,
 * matching the same hand-rolled-parser discipline already proven for
 * CSV/JSON in this milestone. Real and fully unit-testable against
 * synthetic XML strings, independent of any platform ZIP library.
 */
object XlsxSheetXmlReader {

    /** `<si>...<t>text</t>...</si>` blocks, in order -- `sst` index N corresponds to `sharedStrings[N]`. Rich-text runs (`<r><t>...</t></r>`) are concatenated. */
    fun readSharedStrings(xml: String): List<String> {
        val result = mutableListOf<String>()
        var i = 0
        while (true) {
            val siStart = xml.indexOf("<si>", i).takeIf { it >= 0 } ?: xml.indexOf("<si ", i)
            if (siStart < 0) break
            val siEnd = xml.indexOf("</si>", siStart)
            if (siEnd < 0) break
            val block = xml.substring(siStart, siEnd)
            val text = StringBuilder()
            var j = 0
            while (true) {
                val tStart = block.indexOf("<t", j)
                if (tStart < 0) break
                val tagClose = block.indexOf('>', tStart)
                if (tagClose < 0) break
                if (block[tagClose - 1] == '/') { j = tagClose + 1; continue } // self-closing <t/>
                val tEnd = block.indexOf("</t>", tagClose)
                if (tEnd < 0) break
                text.append(decodeXmlEntities(block.substring(tagClose + 1, tEnd)))
                j = tEnd + 4
            }
            result += text.toString()
            i = siEnd + 5
        }
        return result
    }

    data class XlsxCell(val columnIndex: Int, val rowIndex: Long, val rawValue: String?, val hasFormula: Boolean)

    /**
     * Real, bounded parse of `<row>`/`<c>` elements. `hasFormula = true`
     * means a `<f>` tag was present on that cell -- the caller must
     * reject or mark it unsupported (M5.8.7's own "cached formula
     * results are not treated as trusted values" requirement), never
     * silently trust the `<v>` cached result.
     */
    fun readCells(sheetXml: String, sharedStrings: List<String>, limits: ImportLimits): ImportResult<List<XlsxCell>> {
        val cells = mutableListOf<XlsxCell>()
        var rowCount = 0L
        var searchFrom = 0
        while (true) {
            val rowStart = sheetXml.indexOf("<row", searchFrom)
            if (rowStart < 0) break
            val rowHeaderEnd = sheetXml.indexOf('>', rowStart)
            if (rowHeaderEnd < 0) break
            val rowSelfClosing = sheetXml[rowHeaderEnd - 1] == '/'
            val rowAttrs = sheetXml.substring(rowStart, rowHeaderEnd)
            val rowRef = attrValue(rowAttrs, "r")?.toLongOrNull() ?: (rowCount + 1)
            if (rowSelfClosing) {
                searchFrom = rowHeaderEnd + 1
                rowCount++
                continue
            }
            val rowEnd = sheetXml.indexOf("</row>", rowHeaderEnd)
            if (rowEnd < 0) return ImportResult.Failure(ImportError.MalformedContent("unterminated <row> element"))
            val rowBody = sheetXml.substring(rowHeaderEnd + 1, rowEnd)

            var cellSearch = 0
            while (true) {
                val cStart = rowBody.indexOf("<c", cellSearch)
                if (cStart < 0) break
                if (cStart + 2 < rowBody.length && rowBody[cStart + 2].isLetter()) { cellSearch = cStart + 2; continue } // e.g. <color.../> false match guard
                val cHeaderEnd = rowBody.indexOf('>', cStart)
                if (cHeaderEnd < 0) break
                val cSelfClosing = rowBody[cHeaderEnd - 1] == '/'
                val cAttrs = rowBody.substring(cStart, cHeaderEnd)
                val cellRef = attrValue(cAttrs, "r")
                val colIndex = cellRef?.let { columnLetterToIndex(it) } ?: 0
                val cellType = attrValue(cAttrs, "t")

                if (cSelfClosing) {
                    cells += XlsxCell(colIndex, rowRef, null, false)
                    cellSearch = cHeaderEnd + 1
                    continue
                }
                val cEnd = rowBody.indexOf("</c>", cHeaderEnd)
                if (cEnd < 0) return ImportResult.Failure(ImportError.MalformedContent("unterminated <c> element"))
                val cBody = rowBody.substring(cHeaderEnd + 1, cEnd)
                val hasFormula = cBody.contains("<f>") || cBody.contains("<f ")

                val rawValue: String? = when (cellType) {
                    "s" -> {
                        val idx = extractTag(cBody, "v")?.toIntOrNull()
                        if (idx != null && idx in sharedStrings.indices) sharedStrings[idx] else null
                    }
                    "inlineStr" -> extractTag(cBody, "t")?.let { decodeXmlEntities(it) }
                    "str", "n", null, "b", "e" -> extractTag(cBody, "v")?.let { decodeXmlEntities(it) }
                    else -> extractTag(cBody, "v")?.let { decodeXmlEntities(it) }
                }
                if (rawValue != null && rawValue.length > limits.maxCellLength) {
                    return ImportResult.Failure(ImportError.LimitExceeded(ImportLimitExceeded("maxCellLength", limits.maxCellLength.toLong(), rawValue.length.toLong())))
                }
                cells += XlsxCell(colIndex, rowRef, rawValue, hasFormula)
                cellSearch = cEnd + 4
            }
            rowCount++
            if (rowCount > limits.maxRowCount) {
                return ImportResult.Failure(ImportError.LimitExceeded(ImportLimitExceeded("maxRowCount", limits.maxRowCount.toLong(), rowCount)))
            }
            searchFrom = rowEnd + 6
        }
        return ImportResult.Success(cells)
    }

    private fun attrValue(tagText: String, attrName: String): String? {
        val marker = "$attrName=\""
        val start = tagText.indexOf(marker)
        if (start < 0) return null
        val valueStart = start + marker.length
        val valueEnd = tagText.indexOf('"', valueStart)
        if (valueEnd < 0) return null
        return tagText.substring(valueStart, valueEnd)
    }

    private fun extractTag(body: String, tag: String): String? {
        val start = body.indexOf("<$tag>")
        if (start < 0) return null
        val end = body.indexOf("</$tag>", start)
        if (end < 0) return null
        return body.substring(start + tag.length + 2, end)
    }

    /** Real base-26 column-letter decode: "A"=0, "Z"=25, "AA"=26 -- extracts the leading letters from a cell reference like "AA5". */
    fun columnLetterToIndex(cellRef: String): Int {
        var result = 0
        for (c in cellRef) {
            if (!c.isLetter()) break
            result = result * 26 + (c.uppercaseChar() - 'A' + 1)
        }
        return result - 1
    }

    private fun decodeXmlEntities(text: String): String =
        text.replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", "\"").replace("&apos;", "'").replace("&amp;", "&")
}
