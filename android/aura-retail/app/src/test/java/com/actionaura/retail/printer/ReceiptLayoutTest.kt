package com.actionaura.retail.printer

import com.actionaura.retail.printer.ReceiptLayout.Op
import com.actionaura.retail.printer.ReceiptLayout.Side
import com.google.common.truth.Truth.assertThat
import org.junit.Test

/**
 * The layout RULES of the raster receipt, tested without a Canvas (no
 * Robolectric in this module): fixed-width fake metrics (12 dots per character,
 * whatever the script or direction) so every assertion is about WHERE the plan
 * puts things and in WHICH direction, not about glyph shapes.
 *
 * These are the rules whose breakage was measured on the desktop's real canvas
 * on 2026-10-12 (the quantity token flipping side/glyph order beside an Arabic
 * name). What this cannot prove: that Android's StaticLayout then shapes the
 * Arabic and honours the isolates -- that needs a device (see ReceiptRasterizer).
 *
 * Mutation proofs are quoted in the PRINT-2 report: making the name's direction
 * follow its script, or the quantity RTL, turns the marked tests RED.
 */
class ReceiptLayoutTest {
    private val metrics = ReceiptLayout.Metrics { text, _, _, _ -> text.length * 12f }

    private fun row(name: String, qty: String? = "2×", end: String = "5.000", strike: String? = null) =
        ReceiptLine(kind = "row", start = name, end = end, qty = qty, strike = strike)

    private fun plan(rtl: Boolean, vararg lines: ReceiptLine, width: Int = 576) =
        ReceiptLayout.plan(ReceiptModel(rtl = rtl, lines = lines.toList()), width, metrics)

    private fun texts(p: ReceiptLayout.Plan) = p.ops.filterIsInstance<Op.Text>()

    private val arabicName = "شاي نعناع"

    @Test fun quantity_is_its_own_ltr_draw_at_the_line_start_edge_on_an_arabic_receipt() {
        val t = texts(plan(true, row(arabicName)))
        val qty = t.first { it.text == "2×" }
        assertThat(qty.rtl).isFalse()                    // always left-to-right
        assertThat(qty.align).isEqualTo(Side.RIGHT)      // line START on an RTL receipt
        assertThat(qty.inset).isEqualTo(0)
    }

    @Test fun name_is_a_separate_draw_inset_by_the_quantity_width() {
        val t = texts(plan(true, row(arabicName)))
        val name = t.first { it.text == arabicName }
        // "2x" = 2 chars -> 24 dots + the 12 dot gap
        assertThat(name.inset).isEqualTo(36)
        assertThat(name.align).isEqualTo(Side.RIGHT)
    }

    @Test fun the_names_direction_follows_the_receipt_not_the_names_script() {
        // Latin name on an Arabic receipt, Arabic name on an English receipt:
        // both rows must keep the RECEIPT's base direction so the quantity and
        // the name sit in the same place on every line.
        val latinOnArabic = texts(plan(true, row("Mint tea"))).first { it.text == "Mint tea" }
        val arabicOnEnglish = texts(plan(false, row(arabicName))).first { it.text == arabicName }
        assertThat(latinOnArabic.rtl).isTrue()
        assertThat(arabicOnEnglish.rtl).isFalse()
    }

    @Test fun quantity_lands_on_the_same_side_whatever_script_the_name_is_in() {
        val p = plan(true, row(arabicName), row("Mint tea"))
        val qtys = texts(p).filter { it.text == "2×" }
        assertThat(qtys).hasSize(2)
        assertThat(qtys.map { it.align }.toSet()).containsExactly(Side.RIGHT)
        assertThat(qtys.map { it.inset }.toSet()).containsExactly(0)
    }

    @Test fun english_receipt_puts_quantity_left_and_amount_right() {
        val t = texts(plan(false, row("Mint tea")))
        assertThat(t.first { it.text == "2×" }.align).isEqualTo(Side.LEFT)
        assertThat(t.first { it.text == "5.000" }.align).isEqualTo(Side.RIGHT)
    }

    @Test fun amounts_are_always_ltr_and_sit_at_the_line_end_edge_even_when_the_receipt_is_arabic() {
        val amount = texts(plan(true, row(arabicName))).first { it.text == "5.000" }
        assertThat(amount.rtl).isFalse()
        assertThat(amount.align).isEqualTo(Side.LEFT)    // line END on an RTL receipt
    }

    @Test fun a_long_name_wraps_in_what_is_left_and_the_amount_is_drawn_once_on_the_first_piece() {
        val long = List(12) { "wordword" }.joinToString(" ")
        val p = plan(false, row(long, end = "12.500"))
        val t = texts(p)
        val namePieces = t.filter { it.text.startsWith("wordword") }
        assertThat(namePieces.size).isGreaterThan(1)
        // available label width = 576-16 (pad) - amount(72) - gap(12) - qty(24+12) = 440
        namePieces.forEach { assertThat(it.text.length * 12f).isAtMost(440f) }
        assertThat(t.count { it.text == "12.500" }).isEqualTo(1)
        assertThat(t.count { it.text == "2×" }).isEqualTo(1)
        // the amount shares the first piece's baseline
        assertThat(t.first { it.text == "12.500" }.baselineY).isEqualTo(namePieces.first().baselineY)
    }

    @Test fun struck_gross_sits_beside_the_amount_and_the_label_makes_room_for_it() {
        val p = plan(false, row("Mint tea", end = "2.500", strike = "3.000"))
        val strike = p.ops.filterIsInstance<Op.Strike>().single()
        assertThat(strike.side).isEqualTo(Side.RIGHT)
        assertThat(strike.offset).isEqualTo(5 * 12 + 12)     // endWidth + gap
        assertThat(strike.w).isEqualTo(5 * 12)               // the bar covers the gross only
    }

    @Test fun logical_start_alignment_is_mirrored_for_an_arabic_receipt() {
        val line = ReceiptLine(kind = "text", text = "x", align = "start")
        assertThat(texts(plan(true, line)).single().align).isEqualTo(Side.RIGHT)
        assertThat(texts(plan(false, line)).single().align).isEqualTo(Side.LEFT)
        val end = ReceiptLine(kind = "text", text = "x", align = "end")
        assertThat(texts(plan(true, end)).single().align).isEqualTo(Side.LEFT)
    }

    @Test fun an_arabic_run_on_an_english_receipt_is_drawn_rtl_and_latin_stays_ltr() {
        val a = ReceiptLine(kind = "text", text = arabicName, align = "center")
        val l = ReceiptLine(kind = "text", text = "Thank you", align = "center")
        val t = texts(plan(false, a, l))
        assertThat(t[0].rtl).isTrue()
        assertThat(t[1].rtl).isFalse()
    }

    @Test fun qr_lines_draw_nothing_and_rules_advance_14() {
        val p = plan(false, ReceiptLine(kind = "rule"), ReceiptLine(kind = "qr", payload = "abc"), ReceiptLine(kind = "rule"))
        assertThat(p.ops.filterIsInstance<Op.Rule>().map { it.y }).containsExactly(16, 30).inOrder()
        assertThat(p.height).isEqualTo(10 + 14 + 14 + 16)
    }

    @Test fun narrow_paper_uses_smaller_type_but_never_below_22() {
        assertThat(ReceiptLayout.sizeFor(384, null)).isEqualTo(22)
        assertThat(ReceiptLayout.sizeFor(384, "small")).isEqualTo(19)
        assertThat(ReceiptLayout.sizeFor(576, null)).isEqualTo(24)
        assertThat(ReceiptLayout.sizeFor(576, "large")).isEqualTo(32)
    }

    @Test fun an_unbreakable_word_is_split_by_characters_instead_of_overflowing() {
        val pieces = ReceiptLayout.wrapWords("A".repeat(100), 120f) { it.length * 12f }
        assertThat(pieces.size).isGreaterThan(1)
        pieces.forEach { assertThat(it.length * 12f).isAtMost(120f) }
        assertThat(pieces.joinToString("")).isEqualTo("A".repeat(100))
    }

    @Test fun the_sample_receipt_plans_without_error_in_both_languages_and_widths() {
        for (rtl in listOf(false, true)) for (w in listOf(384, 576)) {
            val p = ReceiptLayout.plan(SampleReceipt.model(rtl), w, metrics)
            assertThat(p.ops).isNotEmpty()
            assertThat(p.height).isGreaterThan(100)
        }
    }
}
