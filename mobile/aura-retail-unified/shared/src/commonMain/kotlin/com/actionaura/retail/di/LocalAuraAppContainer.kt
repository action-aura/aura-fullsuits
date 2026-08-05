package com.actionaura.retail.di

import androidx.compose.runtime.staticCompositionLocalOf

/**
 * M6.16 -- the real, single composition-local carrying the app-scoped
 * `AuraAppContainer` down to every screen. Provided exactly once, at
 * the real platform composition root (M6.26 wires this into
 * `androidApp`'s `MainActivity`) -- no screen ever constructs its own
 * `AuraAppContainer`/database/gate (`presentation-di-scope-report.md`'s
 * own explicit rule).
 *
 * `null` default (rather than throwing) is real, deliberate: it lets
 * `App()`'s own bootstrap phase render before the container exists,
 * and lets Compose Multiplatform desktop/JVM previews render shell
 * chrome without a real database -- every screen that NEEDS the
 * container checks for `null` explicitly and shows a real, honest
 * loading/unavailable state rather than crashing.
 */
val LocalAuraAppContainer = staticCompositionLocalOf<AuraAppContainer?> { null }
