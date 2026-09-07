package com.actionaura.retail.ui.brand

import androidx.compose.foundation.Canvas
import androidx.compose.foundation.layout.size
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.StrokeJoin
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.buildAnnotatedString
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.withStyle
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import com.actionaura.retail.ui.theme.AuraBrand
import com.actionaura.retail.ui.theme.TextPrimary

// ─────────────────────────────────────────────────────────────────────────────
// THE MARK -- DESIGN.md §3 ("The brand"), the geometry this file draws is a
// MEASUREMENT, not a redesign: a ring that has just been lit, open at the
// top-right where a spark sits, around an upward A with a short bar. It is
// drawn on a fixed 256x256 design box and scaled by `u = size.toPx() / 256f`
// so every stroke, radius and offset stays in the same proportion the SVG at
// products/retail/frontend/brand/aura-mark.svg was measured from.
//
// The ring and spark colours are the brand's FIXED identity colours, not
// product tokens -- DESIGN.md §3 lists them by name and role -- and they
// are named in ui/theme/Color.kt as `AuraBrand`, because
// ColorTokenContractTest allows no colour literal outside that file and has
// no allowlist to widen; this file holds no hex at all. Everything else
// this composable touches (`ink`, defaulted to TextPrimary) is a token
// getter, so the A follows the active theme's text colour exactly the way
// the desktop's inline mark follows `currentColor` -- dark ink on Day/Sand,
// light ink on Calm/Night/Dusk -- without this file ever naming a theme.
// ─────────────────────────────────────────────────────────────────────────────


/**
 * The Aura mark: the lit ring, its spark, and the upward A -- drawn in
 * Compose so it renders crisp at any size instead of shipping as a raster.
 *
 * `ink` defaults to [TextPrimary] (the active theme's own text colour) so
 * the A reads correctly on every one of the five themes without this file
 * branching on which one is active; callers on a fixed-dark ground (e.g. the
 * launcher icon, which is a separate vector asset) are not affected by this
 * default since they never call this composable.
 */
@Composable
fun AuraMark(size: Dp, modifier: Modifier = Modifier, ink: Color = TextPrimary) {
    Canvas(modifier = modifier.size(size)) {
        val u = size.toPx() / 256f
        val w = size.toPx()
        val h = size.toPx()

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
            sweepAngle = 300f,
            useCenter = false,
            topLeft = Offset((128 - 94) * u, (128 - 94) * u),
            size = Size(188 * u, 188 * u),
            style = Stroke(width = 15 * u, cap = StrokeCap.Round),
        )

        // Spark -- the glow, then the solid white core on top of it.
        drawCircle(
            brush = Brush.radialGradient(
                0f to Color.White,
                0.45f to AuraBrand.SparkHalo,
                1f to AuraBrand.SparkFade,
                center = Offset(196 * u, 60 * u),
                radius = 20 * u,
            ),
            radius = 20 * u,
            center = Offset(196 * u, 60 * u),
        )
        drawCircle(Color.White, radius = 6.5f * u, center = Offset(196 * u, 60 * u))

        // The A -- plain ink so it survives one-colour printing and a 16px
        // favicon (DESIGN.md §3). Follows the theme's text colour via `ink`.
        val stem = Path().apply {
            moveTo(80 * u, 178 * u)
            lineTo(128 * u, 76 * u)
            lineTo(176 * u, 178 * u)
        }
        drawPath(
            path = stem,
            color = ink,
            style = Stroke(width = 19 * u, cap = StrokeCap.Round, join = StrokeJoin.Round),
        )
        val bar = Path().apply {
            moveTo(106 * u, 142 * u)
            lineTo(150 * u, 142 * u)
        }
        drawPath(
            path = bar,
            color = ink,
            style = Stroke(width = 15 * u, cap = StrokeCap.Round, join = StrokeJoin.Round),
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
