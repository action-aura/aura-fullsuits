package com.actionaura.retail.ui.theme

import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.compose.ui.graphics.Color

// ─────────────────────────────────────────────────────────────────────────────
// OPERATIONAL CALM — the phone edition of the desktop till's token layer.
//
// The desktop (products/retail/frontend/css/main.css, [design-tokens]) went
// through a deliberate, contrast-TESTED redesign: one accent used semantically,
// calm cool-grey surfaces, colour that carries meaning (money in, money out,
// warning, refusal) — and it explicitly rejected the "near-black HUD + neon
// accents" look as fatiguing and untrustworthy for software that moves money.
// This file used to be exactly that rejected look (an "Aurora" nebula: teal
// glow constants that had silently become rose, tinted shadows, gradient
// text). Worse, its names had drifted from their values: AuroraTeal was rose,
// AuroraCyan was rose-400, AuroraViolet was rose-300 — a theme file that lied
// to whoever read it.
//
// These values are the desktop's OWN palettes (each theme's block solved by
// the same WCAG math as retail_design_contrast_test.js: every text token AA
// 4.5:1 against every surface it can land on, accent label 4.5:1 on the
// accent fill). Owner's requirement, verbatim: "the desktop retail and the
// phone should look close like basically they supposed to be known for
// eachother". Same surfaces, same accent, same state colours = same product.
//
// NAMING RULE (inherited from the desktop token layer): a token is named for
// WHAT IT IS FOR, never for what it looks like. If the palette is ever
// re-themed, these names stay true.
//
// FIVE PALETTES, ONE ACTIVE (owner request, 2026-09: "night mode back" +
// "themes for both mobile and desktop"). The phone used to hold ONE fixed
// dark palette as top-level `val`s; it now mirrors the desktop's five themes
// (Day, Sand, Calm, Night, Dusk) as five [AuraColors] instances under
// [AuraPalette], with [AuraPalette.current] naming which one is live. Every
// token NAME the app already reads (`SurfaceApp`, `TextPrimary`, ...) is kept
// as a top-level `val`, unchanged at every one of its ~150 call sites, but is
// now a GETTER over the active palette rather than a literal — so a call
// site that reads `SurfaceApp` inside a composable recomposes automatically
// the moment [AuraPalette.current] changes, exactly the way it already
// recomposes when [com.actionaura.retail.ui.i18n.AppLocale.lang] changes.
// ─────────────────────────────────────────────────────────────────────────────

/**
 * One theme's full token set. Every field is a literal [Color] — never a
 * reference to another palette or to [AuraPalette.current] — so the parity
 * test in ui/theme/DesktopTokenParityContractTest.kt can compare a named
 * instance (e.g. [AuraPalette.NIGHT]) against the desktop's CSS block for
 * that theme without the comparison depending on which palette happens to be
 * active.
 */
class AuraColors(
    /** Matches the SharedPreferences value AppTheme persists ("dark", "light", "night", "dusk", "sand"). */
    val name: String,
    /** Picks darkColorScheme vs lightColorScheme in Theme.kt's schemeFor(). */
    val isDark: Boolean,
    // ── SURFACES — ordered by elevation, named by job ────────────────────────
    val surfaceApp: Color,
    val surfaceSunken: Color,
    val surfacePanel: Color,
    val surfaceRaised: Color,
    val surfaceTill: Color,
    val surfaceHover: Color,
    val surfaceActive: Color,
    // ── TEXT — three weights of emphasis, all AA+ on every surface above ─────
    val textPrimary: Color,
    val textSecondary: Color,
    val textTertiary: Color,
    // ── ACCENT — ONE accent, and it means "this is the action you take" ──────
    val accentAction: Color,
    val onAccent: Color,
    val accentSoft: Color,
    /** Secondary warm accent (owner brief, 2026-09-08): the amber half of
     *  "lapis/indigo paired with a warm amber" -- see main.css's
     *  --accent-highlight note. Named for its job (emphasis), not its hue.
     *  Nothing consumes it yet on either client. */
    val accentHighlight: Color,
    // ── BORDERS — named by weight of separation ───────────────────────────────
    val borderDefault: Color,
    val borderHairline: Color,
    // ── SEMANTIC STATE — one meaning per colour ───────────────────────────────
    val success: Color,
    val warning: Color,
    val danger: Color,
    val info: Color,
    val successContainer: Color,
    val dangerContainer: Color,
)

object AuraPalette {
    // Operational Calm, dark — the ORIGINAL phone palette, and the desktop's
    // own `html[data-theme="dark"]` block. Values unchanged from this file's
    // previous single-palette form; see DesktopTokenParityContractTest for
    // the token-by-token parity proof against main.css.
    val CALM = AuraColors(
        name = "dark",
        isDark = true,
        surfaceApp = Color(0xFF0F1319),
        surfaceSunken = Color(0xFF0B0F15),
        surfacePanel = Color(0xFF151A23),
        surfaceRaised = Color(0xFF171D27),
        surfaceTill = Color(0xFF1A212C),
        surfaceHover = Color(0xFF212936),
        surfaceActive = Color(0xFF273140),
        textPrimary = Color(0xFFEDF2F8),
        textSecondary = Color(0xFFC3CDDB),
        textTertiary = Color(0xFF9FADC0),
        accentAction = Color(0xFF98ADF4),
        onAccent = Color(0xFF0F1834),
        accentSoft = Color(0xFF202947),
        accentHighlight = Color(0xFFF0BF4C),
        borderDefault = Color(0xFF2E3947),
        borderHairline = Color(0xFF232B37),
        success = Color(0xFF7BD9A2),
        warning = Color(0xFFE6C67A),
        danger = Color(0xFFFF9D94),
        info = Color(0xFF8AB5F8),
        successContainer = Color(0xFF12301F),
        dangerContainer = Color(0xFF3D1713),
    )

    // Day — the desktop's LIGHT `:root` block (main.css [design-tokens]),
    // light-first, high contrast, one accent used semantically.
    val DAY = AuraColors(
        name = "light",
        isDark = false,
        surfaceApp = Color(0xFFEAEEF3),
        surfaceSunken = Color(0xFFF2F5F8),
        surfacePanel = Color(0xFFFFFFFF),
        surfaceRaised = Color(0xFFF8FAFC),
        surfaceTill = Color(0xFFFFFFFF),
        surfaceHover = Color(0xFFEEF2F7),
        surfaceActive = Color(0xFFE6EAF0),
        textPrimary = Color(0xFF141A24),
        textSecondary = Color(0xFF3D4859),
        textTertiary = Color(0xFF566071),
        // #2F47E1 was this file's first re-grounding pass: MORE saturated
        // than the original, not deeper -- a generic SaaS-primary blue, the
        // exact register the owner's brief was leaving. Corrected into the
        // owner's named band (#1E3A8A-#24409B); a darker ink clears every
        // guard with MORE margin than the original #1745A9 ever had.
        accentAction = Color(0xFF213C90),
        onAccent = Color(0xFFFFFFFF),
        accentSoft = Color(0xFFEAEDF9),
        accentHighlight = Color(0xFFC69010),
        borderDefault = Color(0xFFD3DAE3),
        borderHairline = Color(0xFFE3E8EF),
        success = Color(0xFF0A5832),
        warning = Color(0xFF6E4300),
        danger = Color(0xFF98170F),
        info = Color(0xFF1745A9),
        successContainer = Color(0xFFE7F4ED),
        dangerContainer = Color(0xFFFDECEA),
    )

    // Night — desktop `html[data-theme="night"]`: deeper ink ground than Calm.
    // Carried an aurora-teal accent until the 2026-09-08 re-grounding (owner
    // brief: move off developer-tool cyan/teal). A first pass landed at hue
    // 243 (#A29EFE) -- inside the lapis/indigo family, every contrast guard
    // green -- but only 7.02 ΔE76 from Dusk's lavender (#B9A6FF, hue 253):
    // two of five theme-picker entries reading as the same colour, caught
    // by looking at both themes side by side, not by any ratio. Corrected
    // to hue 208 (#59AEF8), a genuinely BLUER indigo -- Calm and Day sit
    // around hue 225, Dusk is the violet counterpart at 253, Night is now
    // the coldest/bluest member -- 29.33 ΔE76 from Dusk, 15.04 from Calm,
    // both pinned by retail_design_contrast_test.js's
    // testThemeAccentsAreDistinguishable.
    val NIGHT = AuraColors(
        name = "night",
        isDark = true,
        surfaceApp = Color(0xFF070B12),
        surfaceSunken = Color(0xFF04070C),
        surfacePanel = Color(0xFF0B111B),
        surfaceRaised = Color(0xFF0E1520),
        surfaceTill = Color(0xFF111A27),
        surfaceHover = Color(0xFF172233),
        surfaceActive = Color(0xFF1D2B3F),
        textPrimary = Color(0xFFE9F1FB),
        textSecondary = Color(0xFFBFCBDB),
        textTertiary = Color(0xFF9AAABD),
        accentAction = Color(0xFF59AEF8),
        onAccent = Color(0xFF0C1B28),
        accentSoft = Color(0xFF13283A),
        accentHighlight = Color(0xFFF1C255),
        borderDefault = Color(0xFF26344A),
        borderHairline = Color(0xFF1A2432),
        success = Color(0xFF7FDFA9),
        warning = Color(0xFFE9C97E),
        danger = Color(0xFFFF9B92),
        info = Color(0xFF8FBAFF),
        successContainer = Color(0xFF0F2D1F),
        dangerContainer = Color(0xFF3B1512),
    )

    // Dusk — desktop `html[data-theme="dusk"]`: violet-charcoal ground,
    // lavender accent, the calm-dark family at its warmest hue.
    val DUSK = AuraColors(
        name = "dusk",
        isDark = true,
        surfaceApp = Color(0xFF13111C),
        surfaceSunken = Color(0xFF0E0C16),
        surfacePanel = Color(0xFF191626),
        surfaceRaised = Color(0xFF1C192A),
        surfaceTill = Color(0xFF211D31),
        surfaceHover = Color(0xFF29253D),
        surfaceActive = Color(0xFF312C49),
        textPrimary = Color(0xFFF0EDF9),
        textSecondary = Color(0xFFC9C3DC),
        textTertiary = Color(0xFFA49DBD),
        accentAction = Color(0xFFB9A6FF),
        onAccent = Color(0xFF150F2E),
        accentSoft = Color(0xFF2A2350),
        accentHighlight = Color(0xFFF1C150),
        borderDefault = Color(0xFF34304C),
        borderHairline = Color(0xFF26223A),
        success = Color(0xFF86DFA8),
        warning = Color(0xFFEBC97F),
        danger = Color(0xFFFF9D94),
        info = Color(0xFF9DBCFF),
        successContainer = Color(0xFF132D22),
        dangerContainer = Color(0xFF3E1717),
    )

    // Sand — desktop `html[data-theme="sand"]`: warm paper ground, terracotta/
    // sienna ink. A LIGHT theme, not a member of the dark family. A first
    // 2026-09-08 pass moved the accent toward the new --accent-highlight
    // amber's hue to "relate" the two -- and rendered as a muddy OLIVE on
    // the "Open the till" button, reading like a disabled control on the
    // theme being promoted to the brand's public face. Reverted to the
    // ORIGINAL sienna (#9A4F12, unchanged): the relationship to warmth the
    // brief asked for is carried by accentHighlight, not by dragging this
    // theme's own accent toward it.
    val SAND = AuraColors(
        name = "sand",
        isDark = false,
        surfaceApp = Color(0xFFEFE8DC),
        surfaceSunken = Color(0xFFF4EEE4),
        surfacePanel = Color(0xFFFBF7F0),
        surfaceRaised = Color(0xFFFAF6EE),
        surfaceTill = Color(0xFFFFFDF8),
        surfaceHover = Color(0xFFF1EADF),
        surfaceActive = Color(0xFFE8E0D2),
        textPrimary = Color(0xFF2A2119),
        textSecondary = Color(0xFF4D4034),
        textTertiary = Color(0xFF63564A),
        accentAction = Color(0xFF9A4F12),
        onAccent = Color(0xFFFFFFFF),
        accentSoft = Color(0xFFF6E7D3),
        accentHighlight = Color(0xFFE6A819),
        borderDefault = Color(0xFFD4CABB),
        borderHairline = Color(0xFFE4DCCF),
        success = Color(0xFF1D5A34),
        warning = Color(0xFF6E4300),
        danger = Color(0xFF9A1F14),
        info = Color(0xFF1C4A9E),
        successContainer = Color(0xFFE5F1E6),
        dangerContainer = Color(0xFFF9E6E1),
    )

    /** The picker's order: two light, then three dark, day-to-night by feel. */
    val ALL = listOf(DAY, SAND, CALM, NIGHT, DUSK)   // the picker's order

    /** The active palette. Reading this inside a composable makes it recompose on change. */
    var current by mutableStateOf(CALM)

    /** Falls back to [CALM] (this app's original, still-shipping default) for an unknown or missing name. */
    fun byName(n: String?) = ALL.firstOrNull { it.name == n } ?: CALM
}

// ── TOKEN GETTERS — same names every call site already uses, now reading the
// active palette instead of a literal, so every screen recomposes on a theme
// change without any call site changing. ────────────────────────────────────

val SurfaceApp: Color get() = AuraPalette.current.surfaceApp
val SurfaceSunken: Color get() = AuraPalette.current.surfaceSunken
val SurfacePanel: Color get() = AuraPalette.current.surfacePanel
val SurfaceRaised: Color get() = AuraPalette.current.surfaceRaised
val SurfaceTill: Color get() = AuraPalette.current.surfaceTill
val SurfaceHover: Color get() = AuraPalette.current.surfaceHover
val SurfaceActive: Color get() = AuraPalette.current.surfaceActive

val TextPrimary: Color get() = AuraPalette.current.textPrimary
val TextSecondary: Color get() = AuraPalette.current.textSecondary
val TextTertiary: Color get() = AuraPalette.current.textTertiary

val AccentAction: Color get() = AuraPalette.current.accentAction
val OnAccent: Color get() = AuraPalette.current.onAccent
val AccentSoft: Color get() = AuraPalette.current.accentSoft
val AccentHighlight: Color get() = AuraPalette.current.accentHighlight

val BorderDefault: Color get() = AuraPalette.current.borderDefault
val BorderHairline: Color get() = AuraPalette.current.borderHairline

val Success: Color get() = AuraPalette.current.success
val Warning: Color get() = AuraPalette.current.warning
val Danger: Color get() = AuraPalette.current.danger
val Info: Color get() = AuraPalette.current.info
val SuccessContainer: Color get() = AuraPalette.current.successContainer
val DangerContainer: Color get() = AuraPalette.current.dangerContainer

// ── IDENTITY PALETTES — decorative, not semantic ─────────────────────────────
// Eight hues that give avatars (initials circles) and product categories a
// stable, distinguishable colour from a hash of their name. They are NOT
// meaning-bearing like the semantic set above, and they were the last colours
// painted outside this file (Components.kt / RetailScreens.kt, 2026-09-06)
// -- which meant the parity guard could see every token and nothing painted
// around them. Now every Color(0x...) in the app lives here, which
// ColorTokenContractTest pins with no exemptions. Deliberately NOT re-themed
// per palette: an avatar's colour must stay the same person's colour whatever
// theme the till is running.
//
// Each is used as an 18%-alpha backdrop with the SAME full-strength colour as
// the text on top, so the pairing owes a WCAG check against the worst-case
// (tinted) surface. Four of the original eight failed it when measured --
// indigo 6366F1 3.11:1, pink EC4899 3.90:1, purple A855F7 3.33:1, red EF4444
// 3.60:1 -- and were lightened ONE BY ONE, minimally (same hue and saturation,
// HSL lightness nudged up just far enough to clear 4.5:1) rather than
// replaced: the point of these hues is that they differ, and after the nudge
// they still do. The other four were already compliant and are untouched.
//
// Two orders are kept on purpose: an avatar's colour is
// `palette[hash % size]`, so reordering would silently recolour every
// customer's initials and every category chip. Values identical, orders
// identical to where they came from.
val AvatarPalette: List<Color> = listOf(
    Color(0xFF14B8A6), // teal, unchanged (4.78:1 worst-case)
    Color(0xFF9597F5), // indigo, lightened from 6366F1 (was 3.11:1, now 4.53:1 worst-case)
    Color(0xFFF073B1), // pink, lightened from EC4899 (was 3.90:1, now 4.50:1 worst-case)
    Color(0xFFF59E0B), // amber, unchanged (5.38:1 worst-case)
    Color(0xFF10B981), // emerald, unchanged (4.74:1 worst-case)
    Color(0xFF38BDF8), // sky, unchanged (5.31:1 worst-case)
    Color(0xFFC085F9), // purple, lightened from A855F7 (was 3.33:1, now 4.52:1 worst-case)
    Color(0xFFF37777), // red, lightened from EF4444 (was 3.60:1, now 4.52:1 worst-case)
)
val CategoryPalette: List<Color> = listOf(
    Color(0xFF9597F5), // indigo, lightened from 6366F1 (was 3.11:1, now 4.53:1 worst-case)
    Color(0xFF14B8A6), // teal, unchanged (4.78:1 worst-case)
    Color(0xFFF59E0B), // amber, unchanged (5.38:1 worst-case)
    Color(0xFFF073B1), // pink, lightened from EC4899 (was 3.73:1, now 4.50:1 worst-case)
    Color(0xFF10B981), // emerald, unchanged (4.74:1 worst-case)
    Color(0xFF38BDF8), // sky, unchanged (5.31:1 worst-case)
    Color(0xFFC085F9), // purple, lightened from A855F7 (was 3.33:1, now 4.52:1 worst-case)
    Color(0xFFF37777), // red, lightened from EF4444 (was 3.60:1, now 4.52:1 worst-case)
)

// ── Brand: fixed, NOT theme tokens ───────────────────────────────────────
// The Aura mark's own colours (DESIGN.md §3): the ring's gradient. They
// never move with the theme -- a Day till and a Night till show the same
// ring, the same values as products/retail/frontend/brand/aura-mark.svg --
// which is exactly why they are in no AuraColors palette above. They are
// named HERE rather than in ui/brand/AuraMark.kt because
// ColorTokenContractTest allows no colour literal outside this file and
// deliberately has no allowlist to widen; AuraMark.kt is their only
// consumer. The letter A is deliberately absent: it takes the current text
// colour (TextPrimary by default) so it stays visible on every ground, the
// way the desktop's inline mark uses currentColor.
//
// SparkHalo/SparkFade (the old soft radial-gradient spark's glow + core)
// were removed 2026-09-08: the mark evolution pass replaced that spark with
// a flat geometric diamond ("beacon") drawn in the same ink as the A --
// AuraMark.kt's only consumer of either constant -- so neither has a
// reader anywhere in the app any more (confirmed by search before
// deletion). See BrandMarkWiringContractTest's
// aura_mark_beacon_is_a_flat_diamond_following_ink_not_a_gradient_spark.
object AuraBrand {
    val RingStart: Color = Color(0xFF1745A9)
    val RingMid: Color = Color(0xFF3F7BE6)
    val RingEnd: Color = Color(0xFF5FE3D0)

    /**
     * Second pass on the sign-in redesign (2026-09-08): the button's fill
     * became the ring's own RingMid -> RingEnd gradient instead of a flat
     * `AccentAction` slab (LoginScreen.kt's `SignInButton`), and that
     * gradient is light at BOTH ends -- so the label needs a fixed dark ink,
     * not `OnAccent` (which assumes a single, per-theme accent fill) and not
     * white (both stops are already close to white). Fixed to the Night
     * ground itself (DESIGN.md §3, "Night ground `#070B12`, the intro's and
     * app icon's ground") -- the same deep ink the mark already sits on in
     * `ic_launcher_background.xml` -- rather than inventing a new literal.
     *
     * `RingStart` was tried first, since it is already the darkest of the
     * three ring stops, and rejected: measured WCAG contrast is 2.11:1
     * against RingMid (both are the same blue family, so they sit too close
     * in lightness) against a required 4.5:1 -- it would have read as
     * legible on the RingEnd half of the button and nearly invisible on the
     * RingMid half. `OnBrand` measures 4.87:1 against RingMid and 12.56:1
     * against RingEnd, so it clears AA at both ends of the gradient.
     */
    val OnBrand: Color = Color(0xFF070B12)
}
