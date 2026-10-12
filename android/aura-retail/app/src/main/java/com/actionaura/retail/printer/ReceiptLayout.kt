package com.actionaura.retail.printer

/**
 * PURE layout planning for the raster receipt (PRINT-2) -- a line-for-line port
 * of `layout()` in the desktop renderer `frontend/receipt-raster.js`, split from
 * the Canvas drawing ([ReceiptRasterizer]) so the rules that keep Arabic
 * receipts readable are unit-testable on the JVM without an emulator.
 *
 * The rules that matter (each one a measured defect on the desktop, 2026-10-12):
 *  * The row's BASE DIRECTION follows the RECEIPT (`rtl`), never the item
 *    name's script, so the quantity token lands on the same side on every item
 *    line whatever the name is written in.
 *  * The quantity ("2x") is its OWN draw, forced left-to-right, at the line-start
 *    edge; the name is a separate draw, inset by the quantity's width. Isolates
 *    alone did not keep "x2" in order beside Arabic.
 *  * Amounts (and a struck-through gross) are always LTR and sit at the line-end
 *    edge; they are measured FIRST so they are never clipped, and the name wraps
 *    in what is left.
 *  * Alignment is PHYSICAL here (left/right/center); the model's logical
 *    start/end are mirrored for an Arabic receipt.
 */
object ReceiptLayout {
    enum class Side { LEFT, RIGHT, CENTER }

    sealed class Op {
        data class Rule(val y: Int) : Op()
        data class Text(
            val text: String, val bold: Boolean, val sizePx: Int, val rtl: Boolean,
            val align: Side, val inset: Int, val baselineY: Int,
        ) : Op()
        /** [text] drawn [offset] dots in from [side], then a line [w] wide struck through it. */
        data class Strike(
            val text: String, val bold: Boolean, val sizePx: Int,
            val side: Side, val offset: Int, val baselineY: Int, val w: Int,
        ) : Op()
    }

    data class Plan(val ops: List<Op>, val height: Int, val pad: Int)

    /** Width in dots of [text] drawn in the receipt face at [sizePx] with base direction [rtl]. */
    fun interface Metrics { fun width(text: String, bold: Boolean, sizePx: Int, rtl: Boolean): Float }

    const val PAD = 8
    private const val GAP = 12
    private val ARABIC = Regex("[\\u0600-\\u06FF\\u0750-\\u077F\\u08A0-\\u08FF\\uFB50-\\uFDFF\\uFE70-\\uFEFF]")

    fun hasArabic(s: String?): Boolean = s != null && ARABIC.containsMatchIn(s)

    /** 576 dots (80 mm) vs 384 (58 mm): the narrow head gets slightly smaller type, never below 22 px. */
    fun sizeFor(widthDots: Int, size: String?): Int {
        val base = if (widthDots >= 576) 24 else 22
        return when (size) { "small" -> base - 3; "large" -> base + 8; else -> base }
    }

    /** Greedy word wrap by measured width; a single over-wide word is broken by characters. */
    fun wrapWords(text: String, maxW: Float, measure: (String) -> Float): List<String> {
        val words = text.split(Regex("\\s+")).filter { it.isNotEmpty() }
        val lines = ArrayList<String>()
        var cur = ""
        for (w in words) {
            val trial = if (cur.isNotEmpty()) "$cur $w" else w
            if (measure(trial) <= maxW) { cur = trial; continue }
            if (cur.isNotEmpty()) { lines.add(cur); cur = "" }
            if (measure(w) <= maxW) { cur = w; continue }
            var chunk = ""
            var i = 0
            while (i < w.length) {
                val cp = w.codePointAt(i)
                val ch = String(Character.toChars(cp))
                if (measure(chunk + ch) > maxW && chunk.isNotEmpty()) { lines.add(chunk); chunk = ch } else chunk += ch
                i += Character.charCount(cp)
            }
            cur = chunk
        }
        if (cur.isNotEmpty()) lines.add(cur)
        return if (lines.isEmpty()) listOf("") else lines
    }

    fun plan(model: ReceiptModel, widthDots: Int, metrics: Metrics): Plan {
        val rtl = model.rtl
        val maxW = (widthDots - 2 * PAD).toFloat()
        val ops = ArrayList<Op>()
        var y = 10
        val normal = sizeFor(widthDots, null)

        for (ln in model.lines) {
            when (ln.kind) {
                "blank" -> y += normal
                "qr" -> Unit                       // the printer draws the QR itself
                "rule" -> { ops.add(Op.Rule(y + 6)); y += 14 }
                "text" -> {
                    val size = sizeFor(widthDots, ln.size)
                    val bold = ln.bold || ln.size == "large"
                    val lh = Math.round(size * 1.4f)
                    val text = ln.text ?: ""
                    val dir = rtl || hasArabic(text)
                    val align = when (ln.align) {
                        "center" -> Side.CENTER
                        "start" -> if (rtl) Side.RIGHT else Side.LEFT
                        "end" -> if (rtl) Side.LEFT else Side.RIGHT
                        else -> Side.CENTER
                    }
                    for (piece in wrapWords(text, maxW) { metrics.width(it, bold, size, dir) }) {
                        ops.add(Op.Text(piece, bold, size, dir, align, 0, y + lh - Math.round(size * 0.3f)))
                        y += lh
                    }
                }
                "row" -> y = planRow(ln, widthDots, rtl, maxW, metrics, ops, y)
            }
        }
        return Plan(ops, y + 16, PAD)
    }

    private fun planRow(
        ln: ReceiptLine, widthDots: Int, rtl: Boolean, maxW: Float, metrics: Metrics,
        ops: MutableList<Op>, yStart: Int,
    ): Int {
        var y = yStart
        val size = sizeFor(widthDots, ln.size)
        val bold = ln.bold || ln.size == "large"
        val lh = Math.round(size * 1.4f)
        val end = ln.end ?: ""
        val strike = ln.strike
        val qty = ln.qty
        val endW = metrics.width(end, bold, size, false)
        val strikeW = if (strike != null) metrics.width(strike, bold, size, false) + GAP else 0f
        val qtyW = if (!qty.isNullOrEmpty()) metrics.width(qty, bold, size, false) + GAP else 0f
        var labelW = maxW - endW - strikeW - GAP - qtyW
        if (labelW < size * 4) labelW = maxOf(size * 4f, maxW / 2)
        // Base direction follows the RECEIPT, not the name's script (see class doc).
        val pieces = wrapWords(ln.start ?: "", labelW) { metrics.width(it, bold, size, rtl) }
        val labelSide = if (rtl) Side.RIGHT else Side.LEFT
        val endSide = if (rtl) Side.LEFT else Side.RIGHT
        pieces.forEachIndexed { idx, piece ->
            val base = y + lh - Math.round(size * 0.3f)
            if (idx == 0 && !qty.isNullOrEmpty()) {
                ops.add(Op.Text(qty, bold, size, false, labelSide, 0, base))
            }
            ops.add(Op.Text(piece, bold, size, rtl, labelSide, Math.ceil(qtyW.toDouble()).toInt(), base))
            if (idx == 0) {
                ops.add(Op.Text(end, bold, size, false, endSide, 0, base))
                if (strike != null) {
                    ops.add(Op.Strike(strike, bold, size, endSide,
                        Math.ceil((endW + GAP).toDouble()).toInt(), base, Math.round(strikeW - GAP)))
                }
            }
            y += lh
        }
        return y
    }
}
