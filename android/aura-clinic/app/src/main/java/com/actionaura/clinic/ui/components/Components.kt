package com.actionaura.clinic.ui.components

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
import androidx.compose.ui.draw.drawBehind
import androidx.compose.ui.draw.shadow
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Shape
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import com.actionaura.clinic.ui.theme.AuroraCyan
import com.actionaura.clinic.ui.theme.AuroraTeal
import com.actionaura.clinic.ui.theme.AuroraViolet
import com.actionaura.clinic.ui.theme.Ink
import com.actionaura.clinic.ui.theme.Ink2

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
                .background(MaterialTheme.colorScheme.primary.copy(alpha = 0.12f)),
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
private val avatarPalette = listOf(
    Color(0xFF14B8A6), Color(0xFF6366F1), Color(0xFFEC4899), Color(0xFFF59E0B),
    Color(0xFF10B981), Color(0xFF38BDF8), Color(0xFFA855F7), Color(0xFFEF4444),
)

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

// ── Aurora next-gen identity ──────────────────────────────────────────────────
/** Brand gradient for hero text / accents. */
fun auroraBrush(): Brush = Brush.linearGradient(listOf(AuroraTeal, AuroraCyan, AuroraViolet))

/** Ambient nebula backdrop: deep gradient + two soft color glows. Everything floats on this. */
@Composable
fun NebulaBackground(content: @Composable BoxScope.() -> Unit) {
    Box(
        Modifier.fillMaxSize().drawBehind {
            drawRect(Brush.verticalGradient(listOf(Ink, Ink2)))
            drawCircle(
                brush = Brush.radialGradient(
                    listOf(AuroraTeal.copy(alpha = 0.16f), Color.Transparent),
                    center = Offset(size.width * 0.12f, size.height * 0.02f), radius = size.width * 0.75f),
                radius = size.width * 0.75f, center = Offset(size.width * 0.12f, size.height * 0.02f))
            drawCircle(
                brush = Brush.radialGradient(
                    listOf(AuroraViolet.copy(alpha = 0.15f), Color.Transparent),
                    center = Offset(size.width * 0.95f, size.height * 0.92f), radius = size.width * 0.8f),
                radius = size.width * 0.8f, center = Offset(size.width * 0.95f, size.height * 0.92f))
        },
        content = content,
    )
}

/** Frosted panel with a luminous gradient rim — the signature card. */
@Composable
fun GlowCard(
    modifier: Modifier = Modifier,
    glow: Color = AuroraTeal,
    shape: Shape = RoundedCornerShape(20.dp),
    onClick: (() -> Unit)? = null,
    content: @Composable ColumnScope.() -> Unit,
) {
    val rim = Modifier
        .shadow(elevation = 16.dp, shape = shape, clip = false,
            ambientColor = glow.copy(alpha = 0.35f), spotColor = glow.copy(alpha = 0.45f))
        .clip(shape)
        .background(MaterialTheme.colorScheme.surface)
        .border(
            width = 1.dp,
            brush = Brush.linearGradient(listOf(glow.copy(alpha = 0.55f), Color.Transparent, glow.copy(alpha = 0.15f))),
            shape = shape,
        )
    val click = if (onClick != null) Modifier.clickable(onClick = onClick) else Modifier
    Column(modifier.then(rim).then(click), content = content)
}

/** Soft, breathing glow for primary actions (cart bar, CTAs). */
@Composable
fun Modifier.pulseGlow(color: Color = AuroraTeal, shape: Shape = RoundedCornerShape(20.dp)): Modifier {
    val tr = rememberInfiniteTransition(label = "pulse")
    val a by tr.animateFloat(
        initialValue = 0.25f, targetValue = 0.6f,
        animationSpec = infiniteRepeatable(tween(1500), RepeatMode.Reverse), label = "a")
    return this.shadow(20.dp, shape, clip = false,
        ambientColor = color.copy(alpha = a), spotColor = color.copy(alpha = a))
}
