package com.actionaura.retail.ui

import androidx.compose.material3.windowsizeclass.WindowWidthSizeClass
import androidx.compose.runtime.staticCompositionLocalOf

/**
 * Tablet layout (ANDROID-3): which shape the app takes for the window it is in.
 *
 * COMPACT (a phone upright) keeps today's layout: a bottom navigation bar, one screen at a time,
 * the cart as a bottom sheet. MEDIUM and EXPANDED (a tablet, a phone-sized window stretched, a
 * foldable open) get a NavigationRail down the start edge and a two-pane till: the product grid
 * on one side and the cart + Charge staying on screen on the other. The breakpoints are Material 3's
 * own (600 / 840 dp) through WindowSizeClass, computed from the window's real size - never from a
 * device model name - so a split-screen window or a rotated phone lands in the right class too.
 * The mobile/aura-retail-unified app (ui/shell/AppShell.kt) makes the same split with its own
 * AuraWindowSizeClass; this is the same decision for this app.
 */
fun usesWideLayout(widthClass: WindowWidthSizeClass): Boolean = widthClass != WindowWidthSizeClass.Compact

/** True inside the wide shell. Provided once by MainShell; a screen reads it to pick its panes. */
val LocalWideLayout = staticCompositionLocalOf { false }
