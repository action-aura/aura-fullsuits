package com.actionaura.retail.ui

import com.google.common.truth.Truth.assertThat
import org.junit.Assume.assumeTrue
import org.junit.Test
import java.io.File

/**
 * Wiring guard for the Aura brand mark (DESIGN.md §3, "Put the mark in the
 * product"). The sign-in screen and the first-run/loading header must
 * actually call AuraMark()/AuraWordmark() -- not the retired placeholder (a
 * circle with a bold "A" and the literal text "Action Aura") -- and the
 * adaptive launcher icon must point at the new vector layers, not the
 * deleted PNG on a flat #1A1A1A disc.
 *
 * Same shape and justification as BranchPinWiringContractTest and its
 * siblings: no Compose test runner, no Chaquopy and no device in this
 * environment, so "is the mark actually on the screen a user reaches" is
 * exactly the kind of regression that stays invisible until someone opens
 * the app and asks where the logo went -- which is literally how this task
 * started: the owner asking "shouldn't the logo be where it says aura
 * retail?" (2026-09-07).
 */
class BrandMarkWiringContractTest {

    private val moduleRoot = File(".")

    private fun source(relative: String): String {
        val file = File(moduleRoot, relative)
        assumeTrue("$relative not reachable from this run context", file.exists())
        return file.readText()
    }

    private val loginScreen get() = source("src/main/java/com/actionaura/retail/ui/screens/LoginScreen.kt")
    private val appRoot get() = source("src/main/java/com/actionaura/retail/ui/AppRoot.kt")
    private val auraMark get() = source("src/main/java/com/actionaura/retail/ui/brand/AuraMark.kt")
    private val colorKt get() = source("src/main/java/com/actionaura/retail/ui/theme/Color.kt")

    /**
     * Held in a variable rather than spelled as a quoted literal at the call
     * site below, the same way BranchPinWiringContractTest's `orphanPath` is:
     * this path is SUPPOSED to be gone, and CompiledTestSuiteRollCallTest's
     * roll call reads every two-argument file-opening construction it can
     * see as a source-reading guard whose target must exist -- correctly,
     * for every OTHER site in this module, but not for the one test whose
     * entire point is that the target no longer does. (Deliberately not
     * spelling that construction out here either -- see this module's own
     * roll-call test file for why prose that looks like the shape it scans
     * for gets read as a call site, not a comment.)
     */
    private val deletedRasterForegroundPath = "src/main/res/drawable-xxxhdpi/ic_launcher_foreground.png"

    // ── (1) The mark and wordmark are actually called on both screens ──────

    @Test
    fun login_screen_renders_the_mark_and_wordmark() {
        assertThat(loginScreen).contains("AuraMark(")
        assertThat(loginScreen).contains("AuraWordmark(")
    }

    @Test
    fun app_root_loading_header_renders_the_mark_and_wordmark() {
        assertThat(appRoot).contains("AuraMark(")
        assertThat(appRoot).contains("AuraWordmark(")
    }

    // ── (2) The retired circle-with-"A" placeholder is gone where replaced ──

    @Test
    fun login_screen_no_longer_shows_the_circle_a_or_the_action_aura_text() {
        assertThat(loginScreen).doesNotContain("Text(\"A\"")
        // Unlike AppRoot.kt below, LoginScreen.kt has no OTHER legitimate use
        // of "Action Aura" (git grep confirmed zero remaining occurrences
        // after the AuraWordmark() swap), so the bare phrase can be asserted
        // absent outright here, not just the Text(...) call.
        assertThat(loginScreen).doesNotContain("Action Aura")
    }

    @Test
    fun app_root_loading_header_no_longer_draws_the_circle_a() {
        assertThat(appRoot).doesNotContain("Text(\"A\"")
        // AppRoot.kt is NOT asserted free of the bare phrase "Action Aura":
        // git grep shows it still legitimately appears once, as the title
        // bar's fallback name for a route with no per-screen title
        // (`else -> tabs.firstOrNull { it.route == route }?.label ?: "Action
        // Aura"`), plus the comment explaining that fallback -- both
        // explicitly out of scope for this task (the brief names that block
        // as untouched). What must be gone is the specific Text(...) call the
        // loading header used to render, which this asserts directly.
        assertThat(appRoot).doesNotContain("Text(\"Action Aura\"")
    }

    // ── (3) The mark's fixed brand colours and theme-following ink ─────────

    @Test
    fun aura_mark_takes_its_brand_colours_from_color_kt_and_a_theme_following_ink() {
        // DESIGN.md §3, "Brand colours" -- the ring's three stops and the
        // spark. They are FIXED identity colours, so they live as named
        // constants (AuraBrand) in ui/theme/Color.kt -- the one file
        // ColorTokenContractTest lets a colour literal live in -- and the
        // mark reads them by name. A hex typed back into AuraMark.kt would
        // trip that guard AND this one.
        assertThat(colorKt).contains("object AuraBrand")
        assertThat(colorKt).contains("1745A9")
        assertThat(colorKt).contains("3F7BE6")
        assertThat(colorKt).contains("5FE3D0")
        assertThat(auraMark).doesNotContain("Color(0x")
        assertThat(auraMark).contains("AuraBrand.RingStart")
        assertThat(auraMark).contains("AuraBrand.RingEnd")
        // The A itself is not a fixed colour -- it follows the active
        // theme's text colour, exactly like the desktop's inline mark
        // follows currentColor.
        assertThat(auraMark).contains("ink: Color = TextPrimary")
    }

    // ── (4) Adaptive launcher icon points at vector layers, not the PNG ────

    @Test
    fun launcher_icons_reference_the_vector_background_and_foreground() {
        val icon = source("src/main/res/mipmap-anydpi-v26/ic_launcher.xml")
        val round = source("src/main/res/mipmap-anydpi-v26/ic_launcher_round.xml")
        for (xml in listOf(icon, round)) {
            assertThat(xml).contains("@drawable/ic_launcher_foreground")
            assertThat(xml).contains("@drawable/ic_launcher_background")
        }
    }

    @Test
    fun the_old_raster_foreground_png_is_gone() {
        // Reachability guard first: if this run context can't even see the
        // module's manifest, a "the PNG is missing" assertion would pass
        // vacuously for the wrong reason. Only trust the absence once we
        // know we are actually looking at the right res/ tree.
        assumeTrue(
            "module root not reachable from this run context",
            File(moduleRoot, "src/main/AndroidManifest.xml").exists(),
        )
        val png = File(moduleRoot, deletedRasterForegroundPath)
        assertThat(png.exists()).isFalse()
    }

    @Test
    fun the_vector_foreground_draws_the_rings_arc() {
        val fg = source("src/main/res/drawable/ic_launcher_foreground.xml")
        assertThat(fg).contains("A 94 94 0 1 1")
    }
}
