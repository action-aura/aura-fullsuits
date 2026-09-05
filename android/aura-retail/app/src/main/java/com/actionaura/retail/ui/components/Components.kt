package com.actionaura.retail.ui.components

import androidx.compose.animation.core.*
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.shadow
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import com.actionaura.retail.ui.theme.AvatarPalette
import androidx.compose.ui.graphics.Shape
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp

// ── Shimmer skeleton ─────────────────────────────────────────────────────────
@Composable
fun shimmerBrush(): Brush {
    val base = MaterialTheme.colorScheme.surfaceVariant.copy(alpha = 0.7f)
    val hi = MaterialTheme.colorScheme.surfaceVariant.copy(alpha = 0.25f)
    val tr = rememberInfiniteTransition(label = "shimmer")
    val x by tr.animateFloat(
        initialValue = 0f, targetValue = 1200f,
        animationSpec = infiniteRepeatable(tween(1100, easing = LinearEasing)), label = "x",
    )
    return Brush.linearGradient(
        colors = listOf(base, hi, base),
        start = Offset(x - 400f, 0f), end = Offset(x, 0f),
    )
}

@Composable
fun SkeletonBox(modifier: Modifier = Modifier, corner: Dp = 8.dp) {
    Box(modifier.clip(RoundedCornerShape(corner)).background(shimmerBrush()))
}

/** Skeleton placeholder list (cards) shown while data loads. */
@Composable
fun SkeletonList(count: Int = 6, modifier: Modifier = Modifier) {
    Column(modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
        repeat(count) {
            ElevatedCard(Modifier.fillMaxWidth()) {
                Row(Modifier.padding(16.dp), verticalAlignment = Alignment.CenterVertically) {
                    SkeletonBox(Modifier.size(44.dp), corner = 22.dp)
                    Spacer(Modifier.width(14.dp))
                    Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                        SkeletonBox(Modifier.fillMaxWidth(0.55f).height(15.dp))
                        SkeletonBox(Modifier.fillMaxWidth(0.8f).height(12.dp))
                    }
                }
            }
        }
    }
}

/** Skeleton dashboard metric grid. */
@Composable
fun SkeletonMetrics() {
    Column(verticalArrangement = Arrangement.spacedBy(14.dp)) {
        repeat(2) {
            Row(horizontalArrangement = Arrangement.spacedBy(14.dp)) {
                repeat(2) {
                    ElevatedCard(Modifier.weight(1f)) {
                        Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
                            SkeletonBox(Modifier.fillMaxWidth(0.6f).height(11.dp))
                            SkeletonBox(Modifier.fillMaxWidth(0.4f).height(26.dp))
                            SkeletonBox(Modifier.fillMaxWidth(0.5f).height(10.dp))
                        }
                    }
                }
            }
        }
    }
}

// ── Empty state ──────────────────────────────────────────────────────────────
@Composable
fun EmptyState(
    icon: androidx.compose.ui.graphics.vector.ImageVector,
    title: String,
    subtitle: String,
    ctaText: String? = null,
    onCta: (() -> Unit)? = null,
    modifier: Modifier = Modifier,
) {
    Column(
        modifier.fillMaxSize().padding(32.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.Center,
    ) {
        Box(
            Modifier.size(96.dp).clip(CircleShape)
                .background(MaterialTheme.colorScheme.primaryContainer),
            contentAlignment = Alignment.Center,
        ) {
            Icon(icon, null, tint = MaterialTheme.colorScheme.primary, modifier = Modifier.size(44.dp))
        }
        Spacer(Modifier.height(20.dp))
        Text(title, style = MaterialTheme.typography.titleLarge, fontWeight = FontWeight.Bold,
            textAlign = TextAlign.Center)
        Spacer(Modifier.height(8.dp))
        Text(subtitle, style = MaterialTheme.typography.bodyMedium,
            color = MaterialTheme.colorScheme.onSurfaceVariant, textAlign = TextAlign.Center)
        if (ctaText != null && onCta != null) {
            Spacer(Modifier.height(24.dp))
            Button(onClick = onCta, modifier = Modifier.height(50.dp)) {
                Text(ctaText, style = MaterialTheme.typography.labelLarge)
            }
        }
    }
}

// ── Section header ───────────────────────────────────────────────────────────
@Composable
fun SectionHeader(title: String, actionText: String? = null, onAction: (() -> Unit)? = null) {
    Row(Modifier.fillMaxWidth().padding(vertical = 4.dp), verticalAlignment = Alignment.CenterVertically) {
        Text(title, style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.Bold,
            color = MaterialTheme.colorScheme.onSurface, modifier = Modifier.weight(1f))
        if (actionText != null && onAction != null) {
            TextButton(onClick = onAction) { Text(actionText) }
        }
    }
}

// ── Avatar (initials) ────────────────────────────────────────────────────────
// Decorative identity palette -- the eight hues, their contrast numbers and
// the reason their ORDER must never change live with the rest of the palette
// in ui/theme/Color.kt (AvatarPalette), since 2026-09-06 the one place any
// colour in this app is painted from.
private val avatarPalette = AvatarPalette

@Composable
fun Avatar(name: String?, size: Dp = 44.dp) {
    val clean = name?.trim().orEmpty()
    val initials = clean.split(" ").filter { it.isNotBlank() }
        .mapNotNull { it.firstOrNull()?.uppercaseChar() }.take(2).joinToString("").ifBlank { "?" }
    val color = avatarPalette[(clean.hashCode().let { if (it < 0) -it else it }) % avatarPalette.size]
    Box(
        Modifier.size(size).clip(CircleShape).background(color.copy(alpha = 0.18f)),
        contentAlignment = Alignment.Center,
    ) {
        Text(initials, color = color, fontWeight = FontWeight.Bold,
            style = MaterialTheme.typography.titleMedium)
    }
}

// ── Animated integer counter ─────────────────────────────────────────────────
@Composable
fun animatedInt(target: Int): Int {
    val v by animateIntAsState(targetValue = target, animationSpec = tween(700), label = "count")
    return v
}

// ── Operational Calm identity ────────────────────────────────────────────────
// This section used to be the "Aurora" kit: a nebula backdrop (full-screen
// gradient + two radial glows redrawn behind every frame), GlowCard (a 16dp
// TINTED shadow — ambientColor/spotColor — on nearly every card on screen,
// a materially more expensive Compose render path than a neutral shadow),
// and pulseGlow (an INFINITE shadow animation that kept the login screen,
// the POS cart bar and the payment-success screen recomposing forever).
// All three are gone: the desktop till's design language (css/main.css,
// "Operational Calm") uses calm flat surfaces, hairline borders and quiet
// neutral elevation, and the owner's report that the app "feels laggy" made
// the permanently-animating tinted-shadow look a cost with no defender.

/** Flat app backdrop — the desktop's --surface-app. Everything sits on this. */
@Composable
fun AppBackground(content: @Composable BoxScope.() -> Unit) {
    Box(
        Modifier.fillMaxSize().background(MaterialTheme.colorScheme.background),
        content = content,
    )
}

/**
 * The standard card — the phone's --surface-raised panel. Quiet neutral
 * elevation, hairline border, card radius from the shared shape scale.
 *
 * [accent] tints the border when the card CARRIES STATE (a warning total, an
 * error panel, a KPI) — the phone equivalent of the desktop's state-border
 * tokens. Neutral cards pass nothing and stay neutral: colour that means
 * nothing does not belong (token-layer rule).
 */
@Composable
fun TillCard(
    modifier: Modifier = Modifier,
    accent: Color? = null,
    shape: Shape = MaterialTheme.shapes.medium,
    onClick: (() -> Unit)? = null,
    content: @Composable ColumnScope.() -> Unit,
) {
    val border = accent?.copy(alpha = 0.40f) ?: MaterialTheme.colorScheme.outlineVariant
    val base = Modifier
        .shadow(elevation = 2.dp, shape = shape, clip = false)
        .clip(shape)
        .background(MaterialTheme.colorScheme.surfaceContainerLow)
        .border(width = 1.dp, color = border, shape = shape)
    val click = if (onClick != null) Modifier.clickable(onClick = onClick) else Modifier
    Column(modifier.then(base).then(click), content = content)
}
