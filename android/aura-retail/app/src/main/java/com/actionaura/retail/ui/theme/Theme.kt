package com.actionaura.retail.ui.theme

import android.app.Activity
import android.content.ContextWrapper
import androidx.compose.material3.ColorScheme
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.ui.platform.LocalView
import androidx.core.view.WindowCompat

// Operational Calm — the phone till's theme, now one of FIVE (owner request,
// 2026-09: "night mode back" + "themes for both mobile and desktop"), chosen
// in Settings (RetailExtraScreens.kt's Theme row) and persisted by
// ui/i18n/Strings.kt's AppTheme. See Color.kt's header for why these are the
// desktop's own per-theme tokens and not a separate palette family. The
// scheme is still deliberately NOT DayNight and NOT Material You
// (dynamicColor): whichever of the five a shop picks must look identical on
// every device it is installed on, or support calls start with twenty
// minutes of "describe your screen" -- the fixed-identity argument now
// applies per-theme instead of to one theme.
//
// The old dead LightColors scheme is gone with the darkTheme/dynamicColor
// parameters that never did anything -- AuraTheme(darkTheme=false) silently
// rendered dark, which is worse than not offering the knob.
private fun schemeFor(p: AuraColors): ColorScheme {
    // ONE accent per palette. secondary/tertiary alias it on purpose: any M3
    // component that reaches for a "different" accent still lands on-brand,
    // which is the desktop's "one accent, used semantically" rule enforced
    // by scheme, whichever of the five palettes is active.
    return if (p.isDark) {
        darkColorScheme(
            primary = p.accentAction,
            onPrimary = p.onAccent,
            primaryContainer = p.accentSoft,
            onPrimaryContainer = p.textPrimary,
            secondary = p.accentAction,
            onSecondary = p.onAccent,
            // secondaryContainer is what NavigationBarItem uses for its selected
            // indicator; accentSoft + accentAction is the desktop's selected-nav-row
            // treatment (a soft accent wash under an accent glyph).
            secondaryContainer = p.accentSoft,
            onSecondaryContainer = p.accentAction,
            tertiary = p.accentAction,
            onTertiary = p.onAccent,
            tertiaryContainer = p.accentSoft,
            onTertiaryContainer = p.textPrimary,

            background = p.surfaceApp,
            onBackground = p.textPrimary,
            surface = p.surfacePanel,
            onSurface = p.textPrimary,
            surfaceVariant = p.surfaceHover,
            onSurfaceVariant = p.textTertiary,
            surfaceContainerLowest = p.surfaceSunken,
            surfaceContainerLow = p.surfaceRaised,
            surfaceContainer = p.surfaceTill,
            surfaceContainerHigh = p.surfaceHover,
            surfaceContainerHighest = p.surfaceActive,

            outline = p.borderDefault,
            outlineVariant = p.borderHairline,

            error = p.danger,
            onError = p.dangerContainer,
            errorContainer = p.dangerContainer,
            onErrorContainer = p.danger,
        )
    } else {
        lightColorScheme(
            primary = p.accentAction,
            onPrimary = p.onAccent,
            primaryContainer = p.accentSoft,
            onPrimaryContainer = p.textPrimary,
            secondary = p.accentAction,
            onSecondary = p.onAccent,
            secondaryContainer = p.accentSoft,
            onSecondaryContainer = p.accentAction,
            tertiary = p.accentAction,
            onTertiary = p.onAccent,
            tertiaryContainer = p.accentSoft,
            onTertiaryContainer = p.textPrimary,

            background = p.surfaceApp,
            onBackground = p.textPrimary,
            surface = p.surfacePanel,
            onSurface = p.textPrimary,
            surfaceVariant = p.surfaceHover,
            onSurfaceVariant = p.textTertiary,
            surfaceContainerLowest = p.surfaceSunken,
            surfaceContainerLow = p.surfaceRaised,
            surfaceContainer = p.surfaceTill,
            surfaceContainerHigh = p.surfaceHover,
            surfaceContainerHighest = p.surfaceActive,

            outline = p.borderDefault,
            outlineVariant = p.borderHairline,

            error = p.danger,
            onError = p.dangerContainer,
            errorContainer = p.dangerContainer,
            onErrorContainer = p.danger,
        )
    }
}

@Composable
fun AuraTheme(content: @Composable () -> Unit) {
    val palette = AuraPalette.current

    // SYSTEM-BAR ICON POLARITY, re-applied on every theme change.
    //
    // MainActivity picks the polarity ONCE, from the palette restored before
    // the first composition. That is not enough on its own: switching theme
    // (Settings -> Theme) writes straight into AuraPalette.current's Compose
    // state and recomposes -- there is no Activity restart, so onCreate's
    // enableEdgeToEdge never runs again and the bars would keep whatever
    // polarity the app happened to launch with. Going Calm -> Day that leaves
    // white system icons on Day's #EAEEF3 ground (1.17:1), which is the very
    // defect MainActivity's comment describes.
    //
    // Keyed on isDark rather than on the palette identity: three of the five
    // palettes share each polarity, so a Night -> Dusk switch has nothing to
    // re-apply and does not touch the window at all.
    val view = LocalView.current
    if (!view.isInEditMode) {
        LaunchedEffect(palette.isDark) {
            // Unwrapped rather than a single `as? Activity`: a view's context
            // is only USUALLY the Activity, and a cast that quietly yields
            // null here would turn this fix off without a symptom -- the bars
            // would simply keep the launch polarity again.
            val activity = generateSequence(view.context) { (it as? ContextWrapper)?.baseContext }
                .filterIsInstance<Activity>()
                .firstOrNull()
            val window = activity?.window ?: return@LaunchedEffect
            WindowCompat.getInsetsController(window, view).apply {
                // Light BARS mean dark GLYPHS -- the flag is named for the
                // background it expects, not for the icons it produces.
                isAppearanceLightStatusBars = !palette.isDark
                isAppearanceLightNavigationBars = !palette.isDark
            }
        }
    }

    MaterialTheme(
        colorScheme = schemeFor(palette),
        shapes = AuraShapes,
        typography = AuraTypography,
        content = content,
    )
}
