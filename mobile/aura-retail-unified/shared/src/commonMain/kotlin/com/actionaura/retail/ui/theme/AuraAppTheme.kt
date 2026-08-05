package com.actionaura.retail.ui.theme

import androidx.compose.material3.MaterialTheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider

/**
 * M6.4 -- the one real shared theme entry point. Every screen's root
 * Composable is wrapped in this (never bare `MaterialTheme { }`
 * directly) so `AuraTheme.semanticColors` is always available and the
 * light/dark decision always goes through the real, shared
 * `AuraThemePreference` policy, never a hardcoded `darkTheme = true`
 * (the real, confirmed legacy-app gap, `presentation-authority-audit.md`).
 */
@Composable
fun AuraAppTheme(
    preference: AuraThemePreference = AuraThemePreference.System,
    content: @Composable () -> Unit,
) {
    val isDark = preference.resolveIsDark()
    val colorScheme = if (isDark) DarkColorScheme else LightColorScheme
    val semanticColors = if (isDark) DarkSemanticColors else LightSemanticColors

    CompositionLocalProvider(LocalAuraSemanticColors provides semanticColors) {
        MaterialTheme(
            colorScheme = colorScheme,
            shapes = AuraShapes,
            typography = AuraTypography,
            content = content,
        )
    }
}
