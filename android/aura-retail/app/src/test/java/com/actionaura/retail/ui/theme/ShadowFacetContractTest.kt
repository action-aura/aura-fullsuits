package com.actionaura.retail.ui.theme

import com.google.common.truth.Truth.assertThat
import org.junit.Assume.assumeTrue
import org.junit.Test
import java.io.File

/**
 * MUTATION-PROVEN guard for `AuraBrand.ShadowFacet`'s surface-aware branch
 * (Action-Aura-Brand-Guide.md v2, 2026-09-20 "lifted navy" addition).
 *
 * WHY THIS FILE EXISTS
 * `AuraBrand.Navy` used to be a single fixed shadow-facet colour, read by
 * `AuraMark.kt`'s right facet and back-arc gradients on every theme.
 * Measured against this app's own dark `surfaceApp` tokens (CALM #0A111E,
 * NIGHT #070B12, DUSK #13111C) that fixed value (#16233D) came out at
 * 1.19-1.26:1 -- indistinguishable from the ground, the same failure the
 * desktop crew measured and fixed on `aura-mark-on-dark.svg` (see
 * `products/retail/frontend/brand/intro.html`'s header comment: "the A's
 * entire right leg dissolved into the background"). `AuraBrand.ShadowFacet`
 * replaces it with a getter that picks `AuraBrand.ShadowFacetOnDark`
 * (#22304A) on a dark palette and `AuraBrand.ShadowFacetOnLight` (#16233D,
 * the old Navy value) on a light one.
 *
 * Nothing in `ColorTokenContractTest` or `BrandMarkWiringContractTest`
 * proves the PICK itself is correct -- they can only prove the two
 * constants exist with the right hex values, and that `AuraMark.kt` reads
 * `AuraBrand.ShadowFacet` by name. A mutation that made the dark branch fall
 * back to the light value (i.e. `AuraBrand.ShadowFacet` always resolves to
 * #16233D) would pass both of those unchanged. This file exists to catch
 * exactly that regression, the same source-reading way the rest of this
 * module's `*ContractTest.kt` files do (no Compose test runner, no device,
 * in this run context).
 *
 * MUTATION-PROVEN 2026-09-20: `ShadowFacetOnDark` in the picker's TRUE
 * branch was changed to `ShadowFacetOnLight` (the dark surface silently
 * falls back to the light-surface value) --
 * `the_picker_branches_by_the_active_palettes_isDark_flag` went from PASS to
 * FAIL:
 *
 *     value of:
 *         getColorKt()
 *     expected to contain:
 *         val ShadowFacet: Color get() = if (AuraPalette.current.isDark) ShadowFacetOnDark else ShadowFacetOnLight
 *     but was:
 *         [... Color.kt's full source, containing instead:
 *          "val ShadowFacet: Color get() = if (AuraPalette.current.isDark) ShadowFacetOnLight else ShadowFacetOnLight" ...]
 *
 * Restored to the real picker, re-ran the same test: PASSED, 4 tests, 0
 * failed, 0 skipped (this file's whole suite).
 */
class ShadowFacetContractTest {

    private val moduleRoot = File(".")

    private fun source(relative: String): String {
        val file = File(moduleRoot, relative)
        assumeTrue("$relative not reachable from this run context", file.exists())
        return file.readText()
    }

    private val colorKt: String get() = source("src/main/java/com/actionaura/retail/ui/theme/Color.kt")
    private val auraMark: String get() = source("src/main/java/com/actionaura/retail/ui/brand/AuraMark.kt")

    @Test
    fun the_two_shadow_facet_constants_carry_the_brand_guides_own_v2_values() {
        // Navy on light surfaces, unchanged from the retired single-theme
        // constant; the NEW lifted value on dark ones -- both verbatim from
        // Action-Aura-Brand-Guide.md v2's own colour table.
        assertThat(colorKt).contains("val ShadowFacetOnLight: Color = Color(0xFF16233D)")
        assertThat(colorKt).contains("val ShadowFacetOnDark: Color = Color(0xFF22304A)")
    }

    @Test
    fun the_picker_branches_by_the_active_palettes_isDark_flag() {
        // The exact structural text, not just "isDark appears somewhere" --
        // this is precisely what a dark-falls-back-to-light mutation changes,
        // and precisely what a light-falls-back-to-dark mutation (the other
        // direction of the same bug) would also change.
        assertThat(colorKt).contains(
            "val ShadowFacet: Color get() = if (AuraPalette.current.isDark) ShadowFacetOnDark else ShadowFacetOnLight",
        )
    }

    @Test
    fun aura_mark_reads_the_picker_not_either_fixed_constant_directly() {
        // Reading ShadowFacetOnLight or ShadowFacetOnDark directly in
        // AuraMark.kt would re-introduce a single-theme-fixed facet under a
        // different name -- the whole point of this change is that the mark
        // asks for whichever one is ACTIVE.
        assertThat(auraMark).doesNotContain("AuraBrand.ShadowFacetOnLight")
        assertThat(auraMark).doesNotContain("AuraBrand.ShadowFacetOnDark")
        // Both named consumers: the right facet's shadow stop and the
        // back-arc's starting stop (AuraMark.kt's own header/inline comments
        // name both).
        assertThat(Regex("""AuraBrand\.ShadowFacet\.copy\(alpha = drawnFraction\)""").findAll(auraMark).count())
            .isEqualTo(2)
    }

    @Test
    fun the_retired_single_theme_navy_constant_is_gone() {
        // AuraBrand.Navy must not come back under its old name -- its
        // reintroduction would hand AuraMark.kt a second shadow-facet colour
        // to choose from, one of them still theme-blind, with nothing
        // forcing a choice between the two.
        assertThat(colorKt).doesNotContain("val Navy: Color")
        assertThat(auraMark).doesNotContain("AuraBrand.Navy")
    }
}
