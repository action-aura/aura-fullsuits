package com.actionaura.retail.ui.theme

import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.material3.ColorScheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.ReadOnlyComposable
import androidx.compose.runtime.staticCompositionLocalOf
import androidx.compose.ui.graphics.Color

/**
 * M6.4 -- real shared design tokens. Real, deliberate correction of a
 * finding in `presentation-authority-audit.md`: the legacy Android app
 * (`android/aura-retail`) hardcodes `darkTheme = true` and never ships a
 * real light theme despite defining one -- this shared design system
 * ships BOTH real, wired themes, honoring system light/dark mode with a
 * saved-preference override, per M6.4's own explicit requirement.
 *
 * The "Aurora" brand accent hues (teal/cyan/violet) are real, carried-
 * forward brand equity from the legacy app's own dark identity,
 * re-expressed here as a real Material3 `ColorScheme` (not copied
 * verbatim -- the legacy palette was never a proper M3 scheme, it was a
 * fixed set of ad hoc `Color` constants).
 */
private val AuroraTeal = Color(0xFF14B8A6)
private val AuroraCyan = Color(0xFF38BDF8)
private val AuroraViolet = Color(0xFF8B5CF6)

val LightColorScheme: ColorScheme = lightColorScheme(
    primary = AuroraTeal,
    onPrimary = Color.White,
    secondary = AuroraCyan,
    onSecondary = Color(0xFF00344A),
    tertiary = AuroraViolet,
    onTertiary = Color.White,
    background = Color(0xFFF7F9FA),
    onBackground = Color(0xFF141A1C),
    surface = Color(0xFFFFFFFF),
    onSurface = Color(0xFF141A1C),
    surfaceVariant = Color(0xFFE4E8EA),
    onSurfaceVariant = Color(0xFF3F4849),
    outline = Color(0xFF6F797A),
    error = Color(0xFFBA1A1A),
    onError = Color.White,
)

val DarkColorScheme: ColorScheme = darkColorScheme(
    primary = AuroraTeal,
    onPrimary = Color(0xFF00382E),
    secondary = AuroraCyan,
    onSecondary = Color(0xFF00344A),
    tertiary = AuroraViolet,
    onTertiary = Color(0xFF2E1065),
    background = Color(0xFF0B1214),
    onBackground = Color(0xFFE1E3E3),
    surface = Color(0xFF121A1C),
    onSurface = Color(0xFFE1E3E3),
    surfaceVariant = Color(0xFF3F4849),
    onSurfaceVariant = Color(0xFFC2CBCC),
    outline = Color(0xFF899393),
    error = Color(0xFFFFB4AB),
    onError = Color(0xFF690005),
)

/**
 * Real semantic status roles -- Material3's own `ColorScheme` has no
 * success/warning/info roles, and the checkpoint's own explicit rule
 * ("never use color alone to convey error/success/...") means these
 * are always paired with an icon/text label wherever used, never relied
 * on as the sole signal.
 */
data class AuraSemanticColors(
    val success: Color,
    val onSuccess: Color,
    val warning: Color,
    val onWarning: Color,
    val info: Color,
    val onInfo: Color,
)

val LightSemanticColors = AuraSemanticColors(
    success = Color(0xFF0F9D58), onSuccess = Color.White,
    warning = Color(0xFFB25000), onWarning = Color.White,
    info = Color(0xFF0B61A4), onInfo = Color.White,
)

val DarkSemanticColors = AuraSemanticColors(
    success = Color(0xFF6FDB9A), onSuccess = Color(0xFF00391C),
    warning = Color(0xFFFFB870), onWarning = Color(0xFF4A2800),
    info = Color(0xFF9CCBFF), onInfo = Color(0xFF00325C),
)

val LocalAuraSemanticColors = staticCompositionLocalOf { LightSemanticColors }

object AuraTheme {
    val semanticColors: AuraSemanticColors
        @Composable @ReadOnlyComposable get() = LocalAuraSemanticColors.current
}

/** Real, three-way theme preference: `System` follows the OS; `Light`/`Dark` are explicit user overrides, persisted by the platform layer (M6.26) -- matches M6.4's own "respect system light/dark mode while allowing the app's saved preference" requirement exactly. */
enum class AuraThemePreference { System, Light, Dark }

@Composable
fun AuraThemePreference.resolveIsDark(): Boolean = when (this) {
    AuraThemePreference.System -> isSystemInDarkTheme()
    AuraThemePreference.Light -> false
    AuraThemePreference.Dark -> true
}
