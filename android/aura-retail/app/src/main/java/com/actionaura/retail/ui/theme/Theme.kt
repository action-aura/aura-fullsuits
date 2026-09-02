package com.actionaura.retail.ui.theme

import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.Composable

// Operational Calm, dark — the fixed identity of the phone till. See Color.kt's
// header for why these are the desktop's own dark tokens and not a separate
// palette. The scheme is deliberately NOT DayNight and NOT Material You
// (dynamicColor): a till must look identical on every device it is installed
// on, or support calls start with twenty minutes of "describe your screen".
//
// The old dead LightColors scheme is gone with the darkTheme/dynamicColor
// parameters that never did anything — AuraTheme(darkTheme=false) silently
// rendered dark, which is worse than not offering the knob.
private val CalmDarkColors = darkColorScheme(
    // ONE accent. secondary/tertiary alias it on purpose: any M3 component
    // that reaches for a "different" accent still lands on-brand, which is
    // the desktop's "one accent, used semantically" rule enforced by scheme.
    primary = AccentAction,
    onPrimary = OnAccent,
    primaryContainer = AccentSoft,
    onPrimaryContainer = TextPrimary,
    secondary = AccentAction,
    onSecondary = OnAccent,
    // secondaryContainer is what NavigationBarItem uses for its selected
    // indicator; AccentSoft + AccentAction is the desktop's selected-nav-row
    // treatment (a soft accent wash under an accent glyph).
    secondaryContainer = AccentSoft,
    onSecondaryContainer = AccentAction,
    tertiary = AccentAction,
    onTertiary = OnAccent,
    tertiaryContainer = AccentSoft,
    onTertiaryContainer = TextPrimary,

    background = SurfaceApp,
    onBackground = TextPrimary,
    surface = SurfacePanel,
    onSurface = TextPrimary,
    surfaceVariant = SurfaceHover,
    onSurfaceVariant = TextTertiary,
    surfaceContainerLowest = SurfaceSunken,
    surfaceContainerLow = SurfaceRaised,
    surfaceContainer = SurfaceTill,
    surfaceContainerHigh = SurfaceHover,
    surfaceContainerHighest = SurfaceActive,

    outline = BorderDefault,
    outlineVariant = BorderHairline,

    error = Danger,
    onError = DangerContainer,
    errorContainer = DangerContainer,
    onErrorContainer = Danger,
)

@Composable
fun AuraTheme(content: @Composable () -> Unit) {
    MaterialTheme(
        colorScheme = CalmDarkColors,
        shapes = AuraShapes,
        typography = AuraTypography,
        content = content,
    )
}
