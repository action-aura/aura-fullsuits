package com.actionaura.retail.ui.brand

import android.content.Context
import android.provider.Settings
import androidx.compose.animation.core.Animatable
import androidx.compose.animation.core.LinearOutSlowInEasing
import androidx.compose.animation.core.tween
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.layout.size
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.remember
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.drawBehind
import androidx.compose.ui.geometry.CornerRadius
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Rect
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.graphics.drawscope.rotate
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.buildAnnotatedString
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.withStyle
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import com.actionaura.retail.ui.theme.AuraBrand
import com.actionaura.retail.ui.theme.AuraPalette
import com.actionaura.retail.ui.theme.TextPrimary

// ─────────────────────────────────────────────────────────────────────────────
// THE MARK -- REDESIGNED 2026-09-19. The owner supplied a finished
// replacement identity, Action-Aura-Brand-Guide.md, superseding the
// 2026-09-07/08 "ring + upward A + beacon diamond" mark this file used to
// draw (that construction's history is kept below, in this composable's own
// doc comment, since the stroke-weight/apex numbers it names still explain
// dimensions that this file no longer contains).
//
// The new mark is a faceted letter A read through the Greek delta ("pierced
// A"), pierced by a tilted orbit ring: the ring passes BEHIND the letter's
// shoulders (a dimmed back-arc), shows THROUGH the counter -- the small
// square sitting in the A's triangular window, Retail's own counter glyph,
// "the module on the shelf" -- and sweeps ACROSS THE FRONT of the legs to a
// bright energy node. Geometry and colour are copied verbatim, coordinate
// for coordinate, from products/retail/frontend/brand/aura-mark.svg, whose
// own viewBox is 120x120 -- this file keeps that SAME 120-unit design box
// (not the old mark's 256) and scales by `u = size.toPx() / 120f`, so every
// number below is the SVG's own number, not a rescaled derivative one more
// place for a typo to hide in.
//
// FIXED colour, not currentColor/`ink`. Per Action-Aura-Brand-Guide.md the
// pierced-A is full-colour brand artwork -- Retail's own accent trio
// (dark/mid/light) plus the shared brand Navy and a bright highlight ink --
// and, unlike the old ring+A, was never a single-ink glyph that could take
// the surrounding text colour; its colours are the identity and must not
// shift with the product theme (see icons.js's `mark()` for the identical
// reasoning on the desktop side, including why it too stopped reading a
// theme-varying token for the ring). Every one of those colours is a named
// constant in ui/theme/Color.kt's `AuraBrand` (RetailAccentDark/Mid/Light,
// Navy, MarkHighlight) because ColorTokenContractTest allows no colour
// literal outside that file and has no allowlist to widen -- this file
// holds no hex. `ink` (defaulted to TextPrimary) is kept as a parameter for
// source compatibility with every existing call site and because
// BrandMarkWiringContractTest's wiring check pins its presence in this
// signature, but the pierced-A no longer reads it: there is nothing left in
// this geometry that takes the theme's text colour the way the old A did.
//
// THAT GAP IS CLOSED, and this note is kept only because the gap was real
// for part of a day and the fix is worth knowing about.
// MarkGeometryParityContractTest.kt did pin the RETIRED ring+A+beacon-diamond
// construction's exact old coordinates, and failed on this redraw. It has
// since been rewritten to PARSE aura-mark.svg as a real XML DOM and derive
// every expectation from it, so no coordinate is typed into the test at all.
// That is why it stopped rotting: the old version read the SVG *and* also
// hardcoded what it expected to find there, which made it two copies of the
// same digits -- a brand revision broke it while a genuine desktop/Android
// divergence, the thing it exists to catch, would not have. It is green (8
// checks, up from 6) and a coordinate change here now fails it for the right
// reason.
//
// BrandMarkWiringContractTest.kt's colour assertion is NO LONGER part of
// this gap. It used to pin `AuraBrand.RingStart`/`RingEnd` as "the mark's
// own colours" and kept passing after this redraw -- but only by accident:
// the pierced-A never read either constant, and the assertion's `RingEnd`
// half was actually satisfied by `AuraAurora` (below, in this same file)
// still painting with it. A later retired-brand-colour sweep (2026-09-19)
// moved AuraAurora onto Retail's own accent trio too (see its own doc
// comment), which turned that accidental pass into a real failure and
// forced the rewrite -- the assertion now checks the pierced-A's ACTUAL
// colours (RetailAccentDark/Mid/Light, Navy).
// ─────────────────────────────────────────────────────────────────────────────


/**
 * True when the system has turned off all animations (Settings >
 * Accessibility > Remove animations, or a test harness that pins the
 * animator duration scale to 0 to make waits deterministic). DESIGN.md §6:
 * "Reduced motion is respected everywhere." Compose has no direct
 * `prefers-reduced-motion` query on Android, so this reads the same system
 * setting Android's own animation framework honours before letting anything
 * animate.
 */
private fun systemAnimationsDisabled(context: Context): Boolean =
    Settings.Global.getFloat(context.contentResolver, Settings.Global.ANIMATOR_DURATION_SCALE, 1f) == 0f

/**
 * The Aura mark: the pierced-A (Action-Aura-Brand-Guide.md) -- a faceted A
 * read through the Greek delta, pierced by a tilted orbit ring, drawn in
 * Compose so it renders crisp at any size instead of shipping as a raster.
 *
 * `ink` is kept for source compatibility with every existing call site (see
 * this file's header comment) but is no longer read: the pierced-A is fixed
 * brand colour ([AuraBrand]'s Retail accent trio, Navy and MarkHighlight),
 * not a theme-following glyph. Callers pass whatever they already pass;
 * nothing about how they call this composable changes.
 *
 * `animate` draws the mark in on first composition -- every fill and stroke
 * fading from transparent to full strength -- instead of appearing already
 * complete. Defaults to false so every existing caller (AppRoot's first-run/
 * loading header, this file's own default) keeps rendering the finished mark
 * exactly as before; only LoginScreen opts in. DESIGN.md §6: animate
 * opacity only (not layout), and respect reduced motion -- when the system
 * has animations off ([systemAnimationsDisabled]) this jumps straight to the
 * final frame rather than animating one out.
 */
@Composable
fun AuraMark(size: Dp, modifier: Modifier = Modifier, ink: Color = TextPrimary, animate: Boolean = false) {
    val context = LocalContext.current
    val reduceMotion = remember { systemAnimationsDisabled(context) }
    val shouldAnimate = animate && !reduceMotion
    val progress = remember { Animatable(if (shouldAnimate) 0f else 1f) }
    LaunchedEffect(shouldAnimate) {
        if (shouldAnimate) {
            progress.animateTo(1f, animationSpec = tween(durationMillis = 900, easing = LinearOutSlowInEasing))
        }
    }

    Canvas(modifier = modifier.size(size)) {
        // The SVG's own 120-unit design box (aura-mark.svg's viewBox), not
        // the old mark's 256 -- see this file's header comment.
        val u = size.toPx() / 120f
        val drawnFraction = progress.value

        // The ellipse both ring arcs share -- centre (60,69), rx=36, ry=8 --
        // ported directly from aura-mark.svg's `A 36 8 0 0 x 96 69`.
        val ringOval = Rect(
            left = (60f - 36f) * u,
            top = (69f - 8f) * u,
            right = (60f + 36f) * u,
            bottom = (69f + 8f) * u,
        )
        val ringPivot = Offset(60f * u, 69f * u)

        // Back arc -- passes BEHIND the letter's shoulders, dimmed. SVG's
        // sweep-flag 1 from the ellipse's left vertex (180°) to its right
        // vertex (0°/360°) takes the increasing-angle half, through 270°
        // (the ellipse's TOP) -- Compose's matching angle convention (0° at
        // 3 o'clock, sweep clockwise) is startAngle 180, sweep +180.
        // `transform="rotate(-12 60 69)"` on this element only (not the
        // facets, counter or front arc) is why only this draw call and the
        // front arc's below sit inside `rotate {}`.
        rotate(degrees = -12f, pivot = ringPivot) {
            val backArc = Path().apply { addArc(ringOval, startAngleDegrees = 180f, sweepAngleDegrees = 180f) }
            drawPath(
                path = backArc,
                brush = Brush.linearGradient(
                    colors = listOf(
                        AuraBrand.Navy.copy(alpha = drawnFraction),
                        AuraBrand.RetailAccentMid.copy(alpha = drawnFraction),
                    ),
                    start = Offset(24f * u, 69f * u),
                    end = Offset(96f * u, 69f * u),
                ),
                style = Stroke(width = 5f * u),
                alpha = 0.55f,
            )
        }

        // The two facets of the pierced A -- filled polygons, not a stroked
        // stem+bar. Left facet: apex (60,12.6) down the ridge to the
        // shoulder (60,56), out to the left foot (40,104), across to the
        // outer left corner (22,104).
        val leftFacet = Path().apply {
            moveTo(60f * u, 12.6f * u)
            lineTo(60f * u, 56f * u)
            lineTo(40f * u, 104f * u)
            lineTo(22f * u, 104f * u)
            close()
        }
        drawPath(
            path = leftFacet,
            brush = Brush.linearGradient(
                colors = listOf(
                    AuraBrand.RetailAccentLight.copy(alpha = drawnFraction),
                    AuraBrand.RetailAccentMid.copy(alpha = drawnFraction),
                ),
                start = Offset(60f * u, 12.6f * u),
                end = Offset(60f * u, 104f * u),
            ),
        )
        // Right facet: same apex and shoulder, out to the right foot
        // (98,104) and its inner corner (80,104) -- the darker, shadowed
        // side of the letter (Navy stop).
        val rightFacet = Path().apply {
            moveTo(60f * u, 12.6f * u)
            lineTo(98f * u, 104f * u)
            lineTo(80f * u, 104f * u)
            lineTo(60f * u, 56f * u)
            close()
        }
        drawPath(
            path = rightFacet,
            brush = Brush.linearGradient(
                colors = listOf(
                    AuraBrand.RetailAccentDark.copy(alpha = drawnFraction),
                    AuraBrand.Navy.copy(alpha = drawnFraction),
                ),
                start = Offset(60f * u, 12.6f * u),
                end = Offset(60f * u, 104f * u),
            ),
        )

        // The ridge highlight -- a thin bright line down the facet seam.
        drawLine(
            color = AuraBrand.MarkHighlight.copy(alpha = 0.9f * drawnFraction),
            start = Offset(60f * u, 12.6f * u),
            end = Offset(60f * u, 56f * u),
            strokeWidth = 1.3f * u,
        )

        // Retail's own counter glyph -- a small square, "the module on the
        // shelf" (Action-Aura-Brand-Guide.md) -- sitting in the A's
        // triangular window, where the ring shows through.
        drawRoundRect(
            color = AuraBrand.RetailAccentMid.copy(alpha = drawnFraction),
            topLeft = Offset(55.5f * u, 81.5f * u),
            size = Size(9f * u, 9f * u),
            cornerRadius = CornerRadius(2f * u, 2f * u),
        )

        // Front arc -- sweeps ACROSS THE FRONT of the legs, full strength.
        // SVG sweep-flag 0 takes the decreasing-angle half from 180° through
        // 90° (the ellipse's BOTTOM) to 0° -- Compose: startAngle 180,
        // sweep -180. Drawn after the facets/counter so it sits in front of
        // them, exactly as aura-mark.svg orders its elements.
        rotate(degrees = -12f, pivot = ringPivot) {
            val frontArc = Path().apply { addArc(ringOval, startAngleDegrees = 180f, sweepAngleDegrees = -180f) }
            drawPath(
                path = frontArc,
                brush = Brush.linearGradient(
                    colors = listOf(
                        AuraBrand.RetailAccentMid.copy(alpha = drawnFraction),
                        AuraBrand.RetailAccentLight.copy(alpha = drawnFraction),
                    ),
                    start = Offset(24f * u, 69f * u),
                    end = Offset(96f * u, 69f * u),
                ),
                style = Stroke(width = 6f * u, cap = StrokeCap.Round),
            )
        }

        // The energy node where the front arc ends -- a soft halo behind a
        // bright core, both fixed brand ink, replacing the old beacon
        // diamond. Unrotated, like the facets and counter above.
        drawCircle(
            color = AuraBrand.RetailAccentLight.copy(alpha = 0.3f * drawnFraction),
            radius = 7.5f * u,
            center = Offset(95.2f * u, 61.5f * u),
        )
        drawCircle(
            color = AuraBrand.MarkHighlight.copy(alpha = drawnFraction),
            radius = 4f * u,
            center = Offset(95.2f * u, 61.5f * u),
        )
    }
}

/**
 * "Aura" in bold next to the product line in light weight -- the wordmark's
 * on-screen stand-in (DESIGN.md §3 pairs AURA 700 with the product line in
 * 300; the lockup SVG carries this as outlines, this is the live-text
 * equivalent for a screen that needs to stay editable and localizable).
 */
@Composable
fun AuraWordmark(product: String = "Retail") {
    val text = buildAnnotatedString {
        withStyle(SpanStyle(fontWeight = FontWeight.Bold)) { append("Aura") }
        append(" ")
        withStyle(SpanStyle(fontWeight = FontWeight.Light)) { append(product) }
    }
    Text(text = text, style = MaterialTheme.typography.headlineMedium, color = TextPrimary)
}

/**
 * The brand's atmosphere behind a screen's content: two soft radial washes
 * giving the ground gentle warmth and depth rather than a flat fill. Named
 * in PascalCase like [AuraMark]/[AuraWordmark] rather than the usual
 * lowerCamelCase modifier-factory convention, on purpose: it is one of this
 * file's three brand-drawing exports, not a generic layout modifier.
 *
 * `strength` is the wash's peak alpha and is theme-aware by default: a wash
 * bright enough to read against a near-black Night/Calm/Dusk ground would
 * tint the light Day/Sand grounds instead of whispering behind them, so dark
 * themes default to roughly double the alpha of light ones -- the same
 * "computed, not eyeballed" discipline DESIGN.md §2.6 asks of colour
 * everywhere else. No grain layer: intro.html adds a bitmap noise texture on
 * top of its aurora, but that is a full-screen bitmap decode held in memory
 * for an effect this subtle on a phone's sign-in screen -- not worth it here,
 * so this stays two flat gradients.
 *
 * Second pass (2026-09-08): the first pass's top-right wash sat at
 * `(0.75w, 0.20h)` -- close enough inside the frame that its centre, not
 * just its falloff, was visible, which read as a swampy green cast at the
 * top of the screen once layered over the wash below it. Two fixes: the
 * dark-theme strength dropped 0.22 -> 0.14 (light kept proportionally lower,
 * 0.10 -> 0.07, same ~2x ratio as before), and the top-right wash's centre
 * moved off-canvas to the top-right corner (`1.05w, -0.05h`) so only its
 * outer falloff enters the frame. Alpha and position are unchanged by the
 * third pass below -- only the hue moved.
 *
 * Third pass (2026-09-19, retired-brand colour sweep): this wash used to
 * echo `products/retail/frontend/brand/intro.html`'s `.aurora` layer --
 * teal near the top-right, blue near the bottom-left, DESIGN.md §3's OLD
 * ring gradient stops (`AuraBrand.RingEnd`/`RingMid`). That ring is retired
 * (see this file's header comment), and its blue/teal was never Retail's to
 * begin with -- teal (#2F7B7B) is the MASTER brand's own accent per
 * Action-Aura-Brand-Guide.md. A grep for those two constants alone could not
 * have caught this: they were still a legitimate-looking call site right up
 * until the question became "does teal belong on THIS product's screen",
 * which is exactly how the sign-in button's identical fill survived the
 * mark redesign too (see LoginScreen.kt's `SignInButton` doc comment).
 * Now `AuraBrand.RetailAccentLight` (top-right) fading toward
 * `AuraBrand.RetailAccentDark` (bottom-left) -- the same light-to-dark
 * direction the pierced-A's own facets shade in, so the atmosphere and the
 * mark read as one identity. Contrast re-measured, not assumed: alpha is
 * untouched, and against the worst case actually painted directly on this
 * wash (CALM dark theme, `strength = 0.14f`, LoginScreen's tagline in
 * `TextTertiary` at 0.75 alpha, sitting directly on this wash before the
 * sign-in card) the composited background still clears 6.4:1 -- down from
 * the plain background's 8.3:1, but well clear of the 4.5:1 AA floor.
 */
fun Modifier.AuraAurora(
    strength: Float = if (AuraPalette.current.isDark) 0.14f else 0.07f,
): Modifier = drawBehind {
    val radius = minOf(size.width, size.height) * 0.9f
    drawRect(
        brush = Brush.radialGradient(
            colors = listOf(AuraBrand.RetailAccentLight.copy(alpha = strength), Color.Transparent),
            center = Offset(size.width * 1.05f, size.height * -0.05f),
            radius = radius,
        ),
    )
    drawRect(
        brush = Brush.radialGradient(
            colors = listOf(AuraBrand.RetailAccentDark.copy(alpha = strength), Color.Transparent),
            center = Offset(size.width * 0.15f, size.height * 0.85f),
            radius = radius,
        ),
    )
}
