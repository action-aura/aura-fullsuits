package com.actionaura.retail.ui.theme

import android.os.Build
import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.dynamicDarkColorScheme
import androidx.compose.material3.dynamicLightColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext

private val LightColors = lightColorScheme(
    primary = Teal600,
    onPrimary = Color.White,
    primaryContainer = Teal200,
    onPrimaryContainer = Color(0xFF053A33),
    secondary = Teal500,
    background = LightBackground,
    onBackground = LightOnSurface,
    surface = LightSurface,
    onSurface = LightOnSurface,
    surfaceVariant = LightSurfaceVariant,
    onSurfaceVariant = LightOnSurfaceVariant,
    outline = LightOutline,
    error = Danger,
)

// Aurora — curated dark identity. Surfaces are tuned so even plain Cards read as
// frosted panels floating over the nebula background.
private val AuroraColors = darkColorScheme(
    primary = AuroraTeal,
    onPrimary = Color(0xFF00251F),
    primaryContainer = Teal600,
    onPrimaryContainer = Teal200,
    secondary = AuroraCyan,
    onSecondary = Color(0xFF002335),
    tertiary = AuroraViolet,
    onTertiary = Color(0xFF1A0B3D),
    background = Ink,
    onBackground = AuroraOnSurface,
    surface = AuroraSurface,
    onSurface = AuroraOnSurface,
    surfaceVariant = AuroraSurfaceHi,
    onSurfaceVariant = AuroraMuted,
    surfaceContainerLowest = Color(0xFF0B1322),
    surfaceContainerLow = Color(0xFF101a2d),
    surfaceContainer = AuroraSurface,
    surfaceContainerHigh = AuroraSurfaceHi,
    surfaceContainerHighest = Color(0xFF202C42),
    outline = AuroraOutline,
    outlineVariant = Color(0xFF1B2638),
    error = Danger,
)

@Composable
fun AuraTheme(
    darkTheme: Boolean = true,      // Aurora is a fixed dark identity
    dynamicColor: Boolean = false,  // curated palette over Material You
    content: @Composable () -> Unit,
) {
    MaterialTheme(
        colorScheme = AuroraColors,
        shapes = AuraShapes,
        typography = AuraTypography,
        content = content,
    )
}
