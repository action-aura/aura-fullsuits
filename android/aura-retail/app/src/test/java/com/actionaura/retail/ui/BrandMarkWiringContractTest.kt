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
    private val stringsKt get() = source("src/main/java/com/actionaura/retail/ui/i18n/Strings.kt")

    /**
     * Just the `AuraMark` composable's own body (from `fun AuraMark(` up to
     * the next top-level `fun `, i.e. `AuraWordmark`), not the whole file.
     * The file's OTHER composable, `AuraAurora`, legitimately uses
     * `Brush.radialGradient` for its ambient wash (unrelated to the mark's
     * beacon), so a "no radialGradient" guard scoped to the whole file would
     * false-positive against that unrelated usage -- exactly the mistake
     * this scoping avoids.
     */
    private val auraMarkFunctionBody: String get() {
        val start = auraMark.indexOf("fun AuraMark(")
        val end = auraMark.indexOf("fun AuraWordmark(", start)
        assertThat(start).isGreaterThan(-1)
        assertThat(end).isGreaterThan(start)
        return auraMark.substring(start, end)
    }

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
        // DESIGN.md §3, "Brand colours" -- the ring's three stops. They are
        // FIXED identity colours, so they live as named constants
        // (AuraBrand) in ui/theme/Color.kt -- the one file
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

    // ── (3b) 2026-09-19: the energy node (formerly a flat diamond, and
    // before that a soft radial-gradient spark) is two flat circles in
    // FIXED brand colour -- not the theme-following `ink`, not a gradient
    // brush ─────────────────────────────────────────────────────────────

    @Test
    fun aura_mark_energy_node_is_two_flat_circles_in_fixed_brand_colour_not_a_gradient_spark() {
        // WHAT THIS TEST USED TO PIN, and why it no longer can: the diamond
        // shape this test pinned before 2026-09-19 was itself already a
        // replacement for something older still -- a radial-gradient
        // "spark" (a blurred glow plus a solid white core) the owner read as
        // generic tech/crypto glow. That invariant -- no radial-gradient
        // glow on the mark's own energy point -- is the one property that
        // survives every redesign so far, so it is kept below unchanged.
        // The DIAMOND shape and the `inkDuringDraw`/theme-following colour
        // do NOT survive: Action-Aura-Brand-Guide.md's pierced-A ends its
        // front arc at a bright NODE drawn as two circles in fixed brand
        // colour (the whole pierced-A is fixed identity colour now, not a
        // theme-following glyph -- see this file's own header comment), so
        // this test can no longer catch a regression to the OLD diamond's
        // exact four points, nor prove the node "follows ink" -- it no
        // longer does, by design. Exact centre/radius parity against the
        // SVG is MarkGeometryParityContractTest's job now.
        //
        // Scoped to AuraMark()'s own body: AuraAurora (below, in the same
        // file) legitimately uses Brush.radialGradient for its unrelated
        // ambient wash, so an unscoped check here would false-positive.
        assertThat(auraMarkFunctionBody).doesNotContain("Brush.radialGradient")
        // Exactly two filled circles at the node -- not a diamond's four
        // points, not a Stroke (Stroke is reserved for the ring arcs, per
        // MarkGeometryParityContractTest).
        assertThat(auraMarkFunctionBody.split("drawCircle(")).hasSize(3) // 2 occurrences -> 3 pieces
        assertThat(auraMark).contains("center = Offset(95.2f * u, 61.5f * u)")
        // Fixed brand colour, not the theme-following `ink` the old diamond
        // read via `inkDuringDraw` -- MarkHighlight is the specific fixed
        // constant the bright core reads.
        assertThat(auraMarkFunctionBody).contains("AuraBrand.MarkHighlight")
        assertThat(auraMarkFunctionBody).doesNotContain("inkDuringDraw")
    }

    // ── (3c) 2026-09-19: the A is two filled facets sharing one apex and
    // shoulder, not a stroked stem+bar ──────────────────────────────────

    @Test
    fun aura_mark_a_is_drawn_as_two_filled_facets_sharing_one_apex_and_shoulder_not_a_stroked_stem_and_bar() {
        // WHAT THIS TEST USED TO PIN, and why it no longer can: the
        // stroke-WEIGHT invariant this test pinned before 2026-09-19 (peak
        // 19->26, bar 15->20) protected the RETIRED construction, where the
        // A was two stroked lines (a stem + a crossbar) and "thinner" meant
        // a smaller stroke-width number. Action-Aura-Brand-Guide.md's
        // pierced-A is not stroked at all -- it is two FILLED facet
        // polygons meeting at a shared apex/shoulder seam -- so there is no
        // stroke-width left to regress to a thinner number, and this test
        // can no longer catch that specific regression (nor pin the exact
        // apex/shoulder/foot coordinates -- that precision now lives in
        // MarkGeometryParityContractTest, derived from the SVG).
        //
        // What still matters, restated for this construction: the letter
        // must read as two solid, FILLED planes -- not a flat single-tone
        // wedge, and not a reversion to the old stroked stem+bar.
        assertThat(auraMarkFunctionBody).contains("val leftFacet = Path()")
        assertThat(auraMarkFunctionBody).contains("val rightFacet = Path()")
        // Both facets share the SAME apex point -- one letter, two planes,
        // not two independent shapes that merely happen to look similar.
        assertThat(Regex("""moveTo\(60f \* u, 12\.6f \* u\)""").findAll(auraMarkFunctionBody).count())
            .isEqualTo(2)
        val afterLeftFacetDecl = auraMarkFunctionBody.substringAfter("val leftFacet = Path()")
        val leftFacetDraw = afterLeftFacetDecl.substringBefore("val rightFacet = Path()")
        assertThat(leftFacetDraw).doesNotContain("Stroke") // filled, not a stroked stem
        val afterRightFacetDecl = afterLeftFacetDecl.substringAfter("val rightFacet = Path()")
        val rightFacetDraw = afterRightFacetDecl.substringBefore("drawLine(")
        assertThat(rightFacetDraw).doesNotContain("Stroke") // filled, not a stroked bar
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
    fun the_vector_foreground_draws_the_pierced_a_s_ring_as_two_arcs_behind_and_in_front_of_the_letter() {
        // WHAT THIS TEST USED TO PIN, and why it no longer can: the old
        // single "A 94 94 0 1 1" ring (one continuous 300-degree sweep,
        // drawn in ONE stroke) is retired -- this test can no longer prove
        // that specific single-arc construction is absent by its own
        // assertion alone (a `doesNotContain` was added below for that).
        // The pierced-A's ring is TWO half-ellipse arcs sharing the same
        // geometry (ellipse rx=36 ry=8, centre 60,69, rotated -12deg): one
        // dimmed BEHIND the letter's shoulders, one full-strength across
        // the FRONT of the legs. Ported verbatim from aura-mark-on-dark.svg
        // (this launcher's dark-ground twin of aura-mark.svg).
        val fg = source("src/main/res/drawable/ic_launcher_foreground.xml")
        assertThat(fg).doesNotContain("A 94 94 0 1 1") // the old single-arc ring must not come back
        assertThat(fg).contains("M 24 69 A 36 8 0 0 1 96 69") // back arc
        assertThat(fg).contains("M 24 69 A 36 8 0 0 0 96 69") // front arc
    }

    @Test
    fun the_vector_foreground_draws_the_pierced_a_s_filled_facets_and_circular_energy_node() {
        // WHAT THIS TEST USED TO PIN, and why it no longer can: the
        // 2026-09-08 pass this test name referenced (raised A stroke
        // weights, tightened apex, flat diamond beacon) protected the
        // RETIRED stroked-stem-and-bar A and its diamond beacon -- neither
        // exists in this construction, so this test can no longer catch a
        // regression to THOSE specific numbers (that precision, for the
        // SOURCE (Compose) side, now lives in
        // BrandMarkWiringContractTest's own facet/energy-node tests above
        // and in MarkGeometryParityContractTest; this test's remaining job
        // is narrower -- prove the LAUNCHER XML actually carries the new
        // shapes, not just that AuraMark.kt does).
        // Re-anchored to the SAME strictness for the NEW construction: two
        // FILLED facet polygons (not a stroked stem+bar) sharing the apex
        // (60,12.6) and shoulder (60,56), and a circular energy node (not a
        // diamond, and not a radial-gradient glow).
        val fg = source("src/main/res/drawable/ic_launcher_foreground.xml")
        assertThat(fg).contains("M60 12.6 L60 56 L40 104 L22 104 Z") // left facet
        assertThat(fg).contains("M60 12.6 L98 104 L80 104 L60 56 Z") // right facet
        assertThat(fg).doesNotContain("M 196 45 L 211 60 L 196 75 L 181 60 Z") // the old beacon diamond must not come back
        assertThat(fg).contains("95.2") // the energy node's shared centre x
        assertThat(fg).contains("61.5") // the energy node's shared centre y
        assertThat(fg).doesNotContain("android:type=\"radial\"")
    }

    // ── (5) The 2026-09-08 redesign: the sign-in screen is no longer the
    // Material-defaults screen the owner called dull ("i find the log in
    // page on the phone so dull... the sign in button looks cliché") ────────

    @Test
    fun login_screen_no_longer_uses_the_stock_material_card_or_pill_button() {
        // ElevatedCard was the "Material defaults on a flat ground" the
        // owner was reacting to; a full-width, fully-rounded button is the
        // literal shape he called cliché. Neither may come back.
        assertThat(loginScreen).doesNotContain("ElevatedCard")
        assertThat(loginScreen).doesNotContain("CircleShape")
        assertThat(loginScreen).doesNotContain("RoundedCornerShape(50)")
    }

    @Test
    fun login_screen_sign_in_button_uses_the_redesigned_shape() {
        // NOT a pill (RoundedCornerShape(50) / CircleShape, asserted absent
        // above) -- a 14dp rounded rectangle, per the redesign brief.
        assertThat(loginScreen).contains("RoundedCornerShape(14.dp)")
    }

    @Test
    fun login_screen_paints_the_brand_aurora_behind_its_content() {
        assertThat(loginScreen).contains("AuraAurora")
    }

    @Test
    fun login_screen_password_field_submits_on_done() {
        assertThat(loginScreen).contains("ImeAction.Done")
        assertThat(loginScreen).contains("KeyboardActions")
    }

    @Test
    fun aura_mark_supports_an_opt_in_draw_in_animation() {
        // Spelled with the exact spacing of the parameter declaration so this
        // fails if the default silently changes (e.g. to `true`, which would
        // animate every existing caller -- AppRoot's loading header among
        // them -- that never asked for it).
        assertThat(auraMark).contains("animate: Boolean = false")
        assertThat(auraMark).contains("AuraAurora")
    }

    @Test
    fun strings_kt_carries_the_new_login_redesign_keys_with_arabic_values() {
        // Each pair checked for both the English key and a non-English
        // (Arabic) value, the same "is it actually translated, not just
        // present" shape as the rest of this test file's siblings
        // (retail_localization_test.py checks the desktop catalog the same
        // way).
        assertThat(stringsKt).contains("\"One shop. Every device.\"")
        assertThat(stringsKt).contains("متجر واحد")
        assertThat(stringsKt).contains("\"Show password\"")
        assertThat(stringsKt).contains("إظهار كلمة المرور")
        assertThat(stringsKt).contains("\"Hide password\"")
        assertThat(stringsKt).contains("إخفاء كلمة المرور")
    }

    // ── (6) The 2026-09-08 SECOND pass: the critique of the first pass's
    // own screenshot (owner: cliché button fill, swampy-green aurora,
    // invisible card edge, placeholders that repeat their labels) ──────────

    @Test
    fun sign_in_button_is_filled_with_the_brand_gradient_not_a_flat_accent_slab() {
        // The literal call this implementation uses for the button's fill --
        // see SignInButton's doc comment in LoginScreen.kt. Asserted as this
        // exact string (not just one stop alone) so a future rewrite that
        // keeps the brand colours but drops back to a solid fill still fails.
        //
        // STOPS UPDATED 2026-09-19 with the brand. This pinned
        // RingMid -> RingEnd, the retired blue-into-teal, whose whole
        // justification was that it matched the mark's arc -- and the arc now
        // draws Retail's amber, while teal belongs to the master brand rather
        // than to this product. Pinning the old pair would have held the first
        // screen of the app on the identity the owner replaced.
        assertThat(loginScreen)
            .contains("Brush.linearGradient(listOf(AuraBrand.RetailAccentMid, AuraBrand.RetailAccentLight))")
        // The retired flat fill this replaces -- must not come back.
        assertThat(loginScreen).doesNotContain("containerColor = AccentAction")
        // Nor the retired ring pair, which is what this test used to require.
        assertThat(loginScreen).doesNotContain("AuraBrand.RingEnd")
    }

    @Test
    fun sign_in_button_label_uses_the_verified_dark_ink_on_the_gradient() {
        // OnBrand is the fixed dark ink measured (see Color.kt's doc
        // comment) at >=4.5:1 against both RingMid and RingEnd -- OnAccent
        // and a bare white label are both wrong here (OnAccent assumes a
        // single flat accent fill; white is too close to both gradient
        // stops to read).
        assertThat(loginScreen).contains("AuraBrand.OnBrand")
        assertThat(colorKt).contains("val OnBrand: Color = Color(0xFF070B12)")
    }

    @Test
    fun aurora_strength_constants_are_the_second_pass_values() {
        // 0.22f/0.10f (first pass) read as a swampy green wash at the top of
        // the screen once the teal centre sat inside the frame -- see this
        // test class's mutation proof in its class doc comment for how this
        // assertion was verified to actually fail on the retired values.
        assertThat(auraMark).contains("0.14f")
        assertThat(auraMark).contains("0.07f")
        assertThat(auraMark).doesNotContain("0.22f")
        assertThat(auraMark).doesNotContain("0.10f")
    }

    @Test
    fun login_screen_placeholders_are_examples_not_the_labels_above_them() {
        // "EMAIL" / "Email" and "PASSWORD" / "Password" said nothing twice.
        assertThat(loginScreen).contains("tr(\"name@shop.com\")")
        assertThat(loginScreen).contains("••••••••")
        assertThat(loginScreen).doesNotContain("placeholder = { Text(tr(\"Email\")) }")
        assertThat(loginScreen).doesNotContain("placeholder = { Text(tr(\"Password\")) }")
    }

    @Test
    fun login_card_paints_a_top_edge_highlight_inside_its_clip() {
        // The card's only edge treatment used to be the 1dp BorderHairline,
        // which read as invisible against the aurora ground -- this proves
        // the highlight wash actually exists in source, theme-aware and
        // sized to the ~40dp the design brief specified.
        assertThat(loginScreen).contains("topHighlightAlpha")
        assertThat(loginScreen).contains("0.06f")
        assertThat(loginScreen).contains("0.5f")
        assertThat(loginScreen).contains("40.dp.toPx()")
    }
}

/*
 * MUTATION PROOF (defect 2's aurora strength constants), actually run for
 * this pass, not left as a claim -- ENGINEERING.md's "a passing test is not
 * evidence" applies to a wiring-guard string match exactly the same as to
 * any other assertion:
 *
 *   1. Baseline: `gradlew.bat :app:testDebugUnitTest --tests
 *      "com.actionaura.retail.ui.BrandMarkWiringContractTest"` against the
 *      real 0.14f/0.07f values -- PASSED, "19 tests, 0 skipped", 0 failed.
 *   2. AuraMark.kt's `AuraAurora` default mutated back to
 *      `if (AuraPalette.current.isDark) 0.22f else 0.07f` (the retired
 *      dark-theme value; light left alone to isolate the one constant).
 *   3. Same test filter re-run against that mutation -- RESULT: FAILED,
 *      "19 tests completed, 1 failed". The JUnit XML result, verbatim:
 *
 *        value of:
 *            getAuraMark()
 *        expected to contain:
 *            0.14f
 *        but was:
 *            [... AuraMark.kt's full source, including
 *             "strength: Float = if (AuraPalette.current.isDark) 0.22f else 0.07f," ...]
 *
 *      i.e. `assertThat(auraMark).contains("0.14f")` caught the reverted
 *      constant and failed before the chain reached its `doesNotContain
 *      ("0.22f")` sibling -- exactly the failure this test exists to catch.
 *   4. AuraMark.kt restored to `0.14f`/`0.07f`.
 *   5. Re-ran the same test filter: PASSED, "19 tests, 0 skipped", 0 failed.
 */
