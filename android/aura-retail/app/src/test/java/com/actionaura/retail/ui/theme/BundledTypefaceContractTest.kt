package com.actionaura.retail.ui.theme

import com.google.common.truth.Truth.assertThat
import org.junit.Assume.assumeTrue
import org.junit.Test
import java.io.File

/**
 * The bundled typeface must cover BOTH scripts this product ships in.
 *
 * ── The failure this exists to prevent ───────────────────────────────────────
 * Type.kt used the platform sans-serif, which renders Arabic and Latin alike
 * because Android resolves missing glyphs through a system fallback chain.
 * An app-supplied typeface gets no such chain: whatever the file does not
 * contain renders as tofu, permanently, with no error anywhere.
 *
 * The two source faces are strictly disjoint -- measured before bundling:
 * Plus Jakarta Sans carries 0 Arabic glyphs, IBM Plex Sans Arabic carries 0
 * Latin. The desktop resolves that per character in a CSS font stack; a
 * Compose FontFamily cannot, because it selects a face by weight and style
 * and never by script. So each res/font file is a MERGE of the two, and the
 * whole safety of that decision rests on the merge having actually worked.
 *
 * If someone later re-exports these files, subsets them for APK size, swaps
 * in a plain Plus Jakarta Sans build, or regenerates them with a tool that
 * quietly drops the Arabic half, nothing else in this module notices. The app
 * still builds, the tests still pass, and an Arabic-locale till renders every
 * label as empty boxes. That is the exact shape of regression this catches.
 *
 * ── Why it resolves glyph ids rather than scanning cmap ranges ───────────────
 * The cheap version of this check reads the cmap segments and asserts the
 * Arabic range is present. That version passes on a font that lists the range
 * and maps every character in it to glyph 0 -- which is precisely what a
 * badly subset font looks like. So this walks the real lookup (format 4 and
 * format 12) and requires a NON-ZERO glyph id, because glyph 0 IS tofu.
 */
class BundledTypefaceContractTest {

    // Same shape as DesktopTokenParityContractTest: a root File(".") and a
    // helper taking the relative path as a String, so this guard's skips stay
    // visible to CompiledTestSuiteRollCallTest's scan.
    private val moduleRoot = File(".")

    private fun resource(relative: String): File {
        val file = File(moduleRoot, relative)
        assumeTrue("$relative not reachable from this run context", file.exists())
        return file
    }

    // Every path below is spelled out as ONE string literal at its call site,
    // never built by interpolation. CompiledTestSuiteRollCallTest resolves the
    // argument of each [resource] call and asserts the file is really there,
    // so that a whole module's worth of assumeTrue-skipping guards cannot go
    // silent together. An interpolated path defeats that scan: it reads the
    // literal text back, and a path like the dollar-brace form never exists on
    // disk, so the guard reports as unreachable. Found the hard way -- this
    // file failed that roll call on its first run for exactly that reason.
    private fun bundledFonts(): List<Pair<String, File>> = listOf(
        "aura_sans_regular" to resource("src/main/res/font/aura_sans_regular.ttf"),
        "aura_sans_medium" to resource("src/main/res/font/aura_sans_medium.ttf"),
        "aura_sans_semibold" to resource("src/main/res/font/aura_sans_semibold.ttf"),
        "aura_sans_bold" to resource("src/main/res/font/aura_sans_bold.ttf"),
    )

    // Held separately from the paths above so the two can DISAGREE: this list
    // is what Type.kt is checked against, and res/font is listed from disk, so
    // a renamed file with a stale list fails rather than quietly agreeing with
    // itself.
    private val bundledWeights = listOf(
        "aura_sans_regular",
        "aura_sans_medium",
        "aura_sans_semibold",
        "aura_sans_bold",
    )

    // Latin the desktop shell uses everywhere, plus the punctuation a money
    // string needs. A space is included deliberately: the Arabic face alone
    // has no space glyph, so its presence is a cheap tell that the Latin half
    // of the merge is really there.
    private val latinSamples = listOf(
        'A'.code, 'z'.code, '0'.code, '9'.code, '.'.code, ','.code, ' '.code,
    )

    // Arabic letters that appear in ordinary UI copy, and the full
    // Arabic-Indic digit range -- a till that cannot draw its own numerals is
    // useless regardless of how the letters look.
    private val arabicSamples =
        listOf(0x0627, 0x0628, 0x062C, 0x0645, 0x064A, 0x0631) + (0x0660..0x0669).toList()

    private fun u8(b: ByteArray, i: Int) = b[i].toInt() and 0xFF
    private fun u16(b: ByteArray, i: Int) = (u8(b, i) shl 8) or u8(b, i + 1)
    private fun u32(b: ByteArray, i: Int): Long =
        (u16(b, i).toLong() shl 16) or u16(b, i + 2).toLong()

    private fun tableOffset(b: ByteArray, wanted: String): Int {
        val numTables = u16(b, 4)
        for (i in 0 until numTables) {
            val rec = 12 + i * 16
            val tag = String(b, rec, 4, Charsets.US_ASCII)
            if (tag == wanted) return u32(b, rec + 8).toInt()
        }
        return -1
    }

    /** Picks the best cmap subtable: a full-repertoire format 12 if present, else format 4. */
    private fun cmapSubtable(b: ByteArray, cmap: Int): Pair<Int, Int> {
        val n = u16(b, cmap + 2)
        var best = -1
        var bestFormat = -1
        for (i in 0 until n) {
            val rec = cmap + 4 + i * 8
            val platform = u16(b, rec)
            val encoding = u16(b, rec + 2)
            val offset = cmap + u32(b, rec + 4).toInt()
            val format = u16(b, offset)
            val usable = (platform == 3 && (encoding == 1 || encoding == 10)) || platform == 0
            if (!usable) continue
            if (format == 12) return offset to 12
            if (format == 4 && bestFormat != 12) {
                best = offset
                bestFormat = 4
            }
        }
        return best to bestFormat
    }

    private fun glyphForFormat4(b: ByteArray, sub: Int, code: Int): Int {
        if (code > 0xFFFF) return 0
        val segX2 = u16(b, sub + 6)
        val ends = sub + 14
        val starts = ends + segX2 + 2
        val deltas = starts + segX2
        val rangeOffsets = deltas + segX2
        for (s in 0 until segX2 / 2) {
            val end = u16(b, ends + s * 2)
            if (code > end) continue
            val start = u16(b, starts + s * 2)
            if (code < start) return 0
            val delta = u16(b, deltas + s * 2)
            val ro = u16(b, rangeOffsets + s * 2)
            if (ro == 0) return (code + delta) and 0xFFFF
            val at = rangeOffsets + s * 2 + ro + (code - start) * 2
            if (at + 1 >= b.size) return 0
            val g = u16(b, at)
            return if (g == 0) 0 else (g + delta) and 0xFFFF
        }
        return 0
    }

    private fun glyphForFormat12(b: ByteArray, sub: Int, code: Int): Int {
        val groups = u32(b, sub + 12).toInt()
        for (g in 0 until groups) {
            val rec = sub + 16 + g * 12
            val start = u32(b, rec).toInt()
            val end = u32(b, rec + 4).toInt()
            if (code in start..end) return u32(b, rec + 8).toInt() + (code - start)
        }
        return 0
    }

    private fun glyphId(b: ByteArray, code: Int): Int {
        val cmap = tableOffset(b, "cmap")
        assertThat(cmap).isGreaterThan(0)
        val (sub, format) = cmapSubtable(b, cmap)
        assertThat(format).isAnyOf(4, 12)
        return if (format == 12) glyphForFormat12(b, sub, code) else glyphForFormat4(b, sub, code)
    }

    @Test
    fun everyBundledWeightIsARealTrueTypeFile() {
        for ((name, f) in bundledFonts()) {
            val b = f.readBytes()
            assertThat(b.size).isGreaterThan(20_000)
            // 0x00010000 is TrueType outlines. 'OTTO' would be CFF, which is
            // legal in a font but is NOT what these were built as, so a file
            // that arrives as OTTO means something re-exported them.
            assertThat(u32(b, 0)).isEqualTo(0x00010000L)
            assertThat(name).isNotEmpty()
        }
    }

    @Test
    fun everyBundledWeightCoversLatin() {
        for ((_, f) in bundledFonts()) {
            val b = f.readBytes()
            for (cp in latinSamples) {
                assertThat(glyphId(b, cp)).isNotEqualTo(0)
            }
        }
    }

    @Test
    fun everyBundledWeightCoversArabic() {
        for ((_, f) in bundledFonts()) {
            val b = f.readBytes()
            for (cp in arabicSamples) {
                // A zero here means this weight renders that character as a
                // tofu box on every Arabic-locale device. Named explicitly
                // because "expected not to be 0" alone would not say which
                // font or which character.
                assertThat(glyphId(b, cp)).isNotEqualTo(0)
            }
        }
    }

    @Test
    fun everyBundledWeightCarriesAGlyphSubstitutionTable() {
        // Arabic is cursive: without GSUB the letters render as isolated
        // forms and the text is unreadable even though no glyph is missing,
        // so coverage alone is not enough.
        //
        // Be clear about what this DOES NOT prove. Measured while
        // mutation-proving this file: swapping in a Latin-only Plus Jakarta
        // Sans leaves this check green, because that face carries its own
        // GSUB for Latin features. A present GSUB table is therefore evidence
        // that nothing stripped the tables wholesale -- not evidence that
        // Arabic joining works. everyBundledWeightCoversArabic is the check
        // that actually caught that mutation, and the real joining proof was
        // done out of band with HarfBuzz before these files were committed
        // (initial/medial/final forms plus a feh-yeh ligature), which no
        // dependency available to this module can repeat.
        for ((_, f) in bundledFonts()) {
            assertThat(tableOffset(f.readBytes(), "GSUB")).isGreaterThan(0)
        }
    }

    @Test
    fun typeKtReferencesExactlyTheBundledWeights() {
        val src = resource("src/main/java/com/actionaura/retail/ui/theme/Type.kt").readText()
        val referenced = Regex("R\\.font\\.([a-z0-9_]+)")
            .findAll(src).map { it.groupValues[1] }.toSortedSet()
        assertThat(referenced).isEqualTo(bundledWeights.toSortedSet())

        // And nothing sits in res/font that no style asks for -- an unused
        // weight is dead APK weight nobody would otherwise notice.
        val onDisk = File(moduleRoot, "src/main/res/font").listFiles()
            ?.filter { it.extension == "ttf" }
            ?.map { it.nameWithoutExtension }
            ?.toSortedSet()
        assumeTrue("res/font not reachable from this run context", onDisk != null)
        assertThat(onDisk).isEqualTo(bundledWeights.toSortedSet())
    }

    @Test
    fun theOpenFontLicencesShipWithTheApk() {
        // Both source faces are OFL, which requires the licence to accompany
        // the font. These are packaged in res/raw so they travel inside the
        // APK rather than living only in the repository.
        val jakarta = resource("src/main/res/raw/ofl_plus_jakarta_sans.txt")
        val plex = resource("src/main/res/raw/ofl_ibm_plex_sans_arabic.txt")
        for (f in listOf(jakarta, plex)) {
            assertThat(f.length()).isGreaterThan(1_000L)
            assertThat(f.readText()).contains("SIL OPEN FONT LICENSE")
        }
        val notice = resource("src/main/res/raw/aura_sans_notice.txt").readText()
        assertThat(notice).contains("IBM Plex Sans Arabic")
        assertThat(notice).contains("Plus Jakarta Sans")
    }
}
