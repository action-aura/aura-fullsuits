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
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.StrokeJoin
import androidx.compose.ui.graphics.drawscope.Stroke
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
// THE MARK -- DESIGN.md §3 ("The brand"), the geometry this file draws is a
// MEASUREMENT, not a redesign: a ring that has just been lit, open at the
// top-right where a beacon sits, around a bold upward A with a short bar. It
// is drawn on a fixed 256x256 design box and scaled by `u = size.toPx() /
// 256f` so every stroke, radius and offset stays in the same proportion the
// SVG at products/retail/frontend/brand/aura-mark.svg was measured from.
//
// The ring's colours are the brand's FIXED identity colours, not product
// tokens -- DESIGN.md §3 lists them by name and role -- and they are named
// in ui/theme/Color.kt as `AuraBrand`, because ColorTokenContractTest
// allows no colour literal outside that file and has no allowlist to
// widen; this file holds no hex at all. Everything else this composable
// touches (`ink`, defaulted to TextPrimary, and the beacon below which now
// shares it -- 2026-09-08, see that section's own comment) is a token
// getter, so the A and beacon follow the active theme's text colour exactly
// the way the desktop's inline mark follows `currentColor` -- dark ink on
// Day/Sand, light ink on Calm/Night/Dusk -- without this file ever naming a
// theme.
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
 * The Aura mark: the lit ring, its spark, and the upward A -- drawn in
 * Compose so it renders crisp at any size instead of shipping as a raster.
 *
 * `ink` defaults to [TextPrimary] (the active theme's own text colour) so
 * the A reads correctly on every one of the five themes without this file
 * branching on which one is active; callers on a fixed-dark ground (e.g. the
 * launcher icon, which is a separate vector asset) are not affected by this
 * default since they never call this composable.
 *
 * `animate` draws the mark in on first composition -- the ring's sweep from
 * 0° to its full 300° and the A's stroke fading from transparent to `ink` --
 * instead of appearing already complete. Defaults to false so every existing
 * caller (AppRoot's first-run/loading header, this file's own default) keeps
 * rendering the finished mark exactly as before; only LoginScreen opts in.
 * DESIGN.md §6: animate transform/opacity only (a sweep angle and a stroke
 * alpha, here, not layout), and respect reduced motion -- when the system
 * has animations off ([systemAnimationsDisabled]) this jumps straight to the
 * final frame rather than animating one out.
 */
@Composable
fun AuraMark(size: Dp, modifier: Modifier = Modifier, ink: Color = TextPrimary, animate: Boolean = false) {
    val context = LocalContext.current
    val reduceMotion = remember { systemAnimationsDisabled(context) }
    val shouldAnimate = animate && !reduceMotion
    // One progress value drives both the ring's sweep and the A's alpha
    // together, so they draw in as a single gesture rather than two
    // independently-timed ones.
    val progress = remember { Animatable(if (shouldAnimate) 0f else 1f) }
    LaunchedEffect(shouldAnimate) {
        if (shouldAnimate) {
            progress.animateTo(1f, animationSpec = tween(durationMillis = 900, easing = LinearOutSlowInEasing))
        }
    }

    Canvas(modifier = modifier.size(size)) {
        val u = size.toPx() / 256f
        val w = size.toPx()
        val h = size.toPx()
        val drawnFraction = progress.value

        // Ring -- 300° sweep, gap open at the top, right end 19.5° past
        // twelve. Compose's arc angles start at 3 o'clock and go clockwise,
        // which is why the SVG's dash-offset/rotation geometry becomes this
        // startAngle/sweepAngle pair rather than a literal transcription.
        drawArc(
            brush = Brush.linearGradient(
                colors = listOf(AuraBrand.RingStart, AuraBrand.RingMid, AuraBrand.RingEnd),
                start = Offset(0.15f * w, 0.9f * h),
                end = Offset(0.85f * w, 0.1f * h),
            ),
            startAngle = -70.5f,
            sweepAngle = 300f * drawnFraction,
            useCenter = false,
            topLeft = Offset((128 - 94) * u, (128 - 94) * u),
            size = Size(188 * u, 188 * u),
            style = Stroke(width = 15 * u, cap = StrokeCap.Round),
        )

        // The A -- plain ink so it survives one-colour printing and a 16px
        // favicon (DESIGN.md §3). Follows the theme's text colour via `ink`,
        // fading in with `drawnFraction` when animating. 2026-09-08: stroke
        // weights raised (peak 19->26, bar 15->20) and the apex
        // narrowed/raised (base 80/176->84/172, apex y 76->70) so the A --
        // not the ring -- is the first thing read; same silhouette, same
        // 256-unit box (see icons.js's MARK EVOLUTION comment and
        // products/retail/frontend/brand/aura-mark.svg for the full
        // reasoning, proven there with a real 1-bit render).
        val inkDuringDraw = ink.copy(alpha = ink.alpha * drawnFraction)

        // The beacon, at the ring's opening: a flat diamond in the SAME ink
        // as the A, not the old radial-gradient spark (a blurred glow plus
        // a white dot, which read as a generic tech/crypto glow). No blur,
        // no gradient brush -- a hard-edged geometric vertex that shares
        // the A's own colour and fade, so it can never band or vanish under
        // a one-colour print threshold the way the old glow did.
        val beacon = Path().apply {
            moveTo(196 * u, 45 * u)
            lineTo(211 * u, 60 * u)
            lineTo(196 * u, 75 * u)
            lineTo(181 * u, 60 * u)
            close()
        }
        drawPath(path = beacon, color = inkDuringDraw)

        val stem = Path().apply {
            moveTo(84 * u, 178 * u)
            lineTo(128 * u, 70 * u)
            lineTo(172 * u, 178 * u)
        }
        drawPath(
            path = stem,
            color = inkDuringDraw,
            style = Stroke(width = 26 * u, cap = StrokeCap.Round, join = StrokeJoin.Round),
        )
        val bar = Path().apply {
            moveTo(108 * u, 142 * u)
            lineTo(148 * u, 142 * u)
        }
        drawPath(
            path = bar,
            color = inkDuringDraw,
            style = Stroke(width = 20 * u, cap = StrokeCap.Round, join = StrokeJoin.Round),
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
 * echoing `products/retail/frontend/brand/intro.html`'s `.aurora` layer --
 * teal near the top-right, blue near the bottom-left (DESIGN.md §3's ring
 * gradient stops, reused here as an ambient wash rather than a stroke).
 * Named in PascalCase like [AuraMark]/[AuraWordmark] rather than the usual
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
 * Second pass (2026-09-08): the first pass's teal wash sat at
 * `(0.75w, 0.20h)` -- close enough inside the frame that its centre, not
 * just its falloff, was visible, which read as a swampy green cast at the
 * top of the screen once layered over the blue wash below it. Two fixes:
 * the dark-theme strength dropped 0.22 -> 0.14 (light kept proportionally
 * lower, 0.10 -> 0.07, same ~2x ratio as before), and the teal wash's centre
 * moved off-canvas to the top-right corner (`1.05w, -0.05h`) so only its
 * outer falloff -- a hint of teal in the corner, never the full hue --
 * enters the frame. The blue wash (bottom-left) is unchanged.
 */
fun Modifier.AuraAurora(
    strength: Float = if (AuraPalette.current.isDark) 0.14f else 0.07f,
): Modifier = drawBehind {
    val radius = minOf(size.width, size.height) * 0.9f
    drawRect(
        brush = Brush.radialGradient(
            colors = listOf(AuraBrand.RingEnd.copy(alpha = strength), Color.Transparent),
            center = Offset(size.width * 1.05f, size.height * -0.05f),
            radius = radius,
        ),
    )
    drawRect(
        brush = Brush.radialGradient(
            colors = listOf(AuraBrand.RingMid.copy(alpha = strength), Color.Transparent),
            center = Offset(size.width * 0.15f, size.height * 0.85f),
            radius = radius,
        ),
    )
}
