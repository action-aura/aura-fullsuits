package com.actionaura.retail.printer

import com.google.common.truth.Truth.assertThat
import com.google.gson.Gson
import org.junit.Assert.assertThrows
import org.junit.Test

/**
 * PRINT-2: the Kotlin encoder must emit EXACTLY the bytes Python's
 * `escpos_raster.wrap_raster` emits for the same bitmap. The golden files in
 * src/test/resources/escpos_golden/ were produced BY THE PYTHON MODULE
 * (products/retail/tests/tools/gen_escpos_golden.py); nothing here computes an
 * expected value from the code under test.
 *
 * Cases cover: single band, exactly 255 rows (no extra band), 256 rows (a 1-row
 * second band), two bands, with and without QR / cut / kick.
 *
 * Mutation proofs (run 2026-10-12, quoted in the PRINT-2 report): BAND_ROWS 255
 * -> 256 turns tall_576_two_bands_kick, band_plus_one_384_kick RED; moving the
 * kick before the cut turns every kick case RED.
 */
class EscPosRasterGoldenTest {
    private data class Params(val width: Int, val height: Int, val qr: String?, val cut: Boolean, val kick: Boolean)

    private fun res(name: String): ByteArray =
        (javaClass.classLoader!!.getResourceAsStream("escpos_golden/$name")
            ?: error("missing golden resource $name")).use { it.readBytes() }

    private fun check(name: String) {
        val p = Gson().fromJson(String(res("$name.json")), Params::class.java)
        val actual = EscPosRaster.wrap(res("$name.in.bin"), p.width, p.height, p.qr, p.cut, p.kick)
        val expected = res("$name.out.bin")
        // Compare with the first differing offset: a bare size/array mismatch
        // tells nobody which command drifted.
        val firstDiff = (0 until minOf(actual.size, expected.size)).firstOrNull { actual[it] != expected[it] }
        assertThat(firstDiff).isNull()
        assertThat(actual.size).isEqualTo(expected.size)
    }

    @Test fun white_576_plain() = check("white_576_plain")
    @Test fun checker_384_qr() = check("checker_384_qr")
    @Test fun tall_576_two_bands_kick() = check("tall_576_two_bands_kick")
    @Test fun tall_384_qr_kick_nocut() = check("tall_384_qr_kick_nocut")
    @Test fun exact_band_576() = check("exact_band_576")
    @Test fun band_plus_one_384_kick() = check("band_plus_one_384_kick")

    @Test fun kick_is_the_last_thing_in_the_job_and_follows_the_cut() {
        val out = EscPosRaster.wrap(ByteArray(48), 384, 1, null, cut = true, kick = true)
        val tail = out.takeLast(9).map { it.toInt() and 0xFF }
        assertThat(tail).containsExactly(0x1D, 'V'.code, 0x42, 0x03, 0x1B, 'p'.code, 0, 25, 100).inOrder()
    }

    @Test fun refuses_a_width_the_printer_was_not_told_about() {
        assertThrows(EscPosRaster.RasterException::class.java) { EscPosRaster.wrap(ByteArray(10), 80, 1) }
    }

    @Test fun refuses_a_bitmap_whose_length_does_not_match() {
        assertThrows(EscPosRaster.RasterException::class.java) { EscPosRaster.wrap(ByteArray(47), 384, 1) }
    }

    @Test fun packPixels_is_msb_first_and_thresholds_at_160() {
        val w = 8
        val px = IntArray(w) { 0xFFFFFFFF.toInt() }
        px[0] = 0xFF000000.toInt()          // black -> bit 7
        px[7] = 0xFF9F9F9F.toInt()          // luma 159 -> prints (bit 0)
        px[3] = 0xFFA0A0A0.toInt()          // luma 160 -> paper
        val packed = EscPosRaster.packPixels(px, w, 1)
        assertThat(packed[0].toInt() and 0xFF).isEqualTo(0b1000_0001)
    }

    @Test fun packPixels_treats_transparent_as_paper() {
        val packed = EscPosRaster.packPixels(IntArray(8) { 0x00000000 }, 8, 1)
        assertThat(packed[0].toInt()).isEqualTo(0)
    }
}
