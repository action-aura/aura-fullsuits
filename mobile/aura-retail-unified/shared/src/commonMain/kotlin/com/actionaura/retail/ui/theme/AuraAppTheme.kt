package com.actionaura.retail.ui.theme

import androidx.compose.material3.MaterialTheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.ui.platform.LocalLayoutDirection
import androidx.compose.ui.unit.LayoutDirection
import com.actionaura.retail.ui.localization.AppLocale
import com.actionaura.retail.ui.localization.LocalAppLocale

/**
 * M6.4/M6.12 -- the one real shared theme entry point. Every screen's
 * root Composable is wrapped in this (never bare `MaterialTheme { }`
 * directly) so `AuraTheme.semanticColors` is always available, the
 * light/dark decision always goes through the real, shared
 * `AuraThemePreference` policy (never a hardcoded `darkTheme = true`,
 * the real, confirmed legacy-app gap, `presentation-authority-audit.md`),
 * and the real app-wide layout direction always follows the real
 * selected locale -- `LocalLayoutDirection` flips to `Rtl` for Arabic,
 * no Activity restart needed, matching the audited legacy app's own
 * real, already-proven mechanism (`AppLocale.isRtl` read once,
 * `presentation-authority-audit.md`).
 */
@Composable
fun AuraAppTheme(
    preference: AuraThemePreference = AuraThemePreference.System,
    locale: AppLocale = AppLocale.EN,
    content: @Composable () -> Unit,
) {
    val isDark = preference.resolveIsDark()
    val colorScheme = if (isDark) DarkColorScheme else LightColorScheme
    val semanticColors = if (isDark) DarkSemanticColors else LightSemanticColors
    val layoutDirection = if (locale.isRtl) LayoutDirection.Rtl else LayoutDirection.Ltr

    CompositionLocalProvider(
        LocalAuraSemanticColors provides semanticColors,
        LocalAppLocale provides locale,
        LocalLayoutDirection provides layoutDirection,
    ) {
        MaterialTheme(
            colorScheme = colorScheme,
            shapes = AuraShapes,
            typography = AuraTypography,
            content = content,
        )
    }
}
