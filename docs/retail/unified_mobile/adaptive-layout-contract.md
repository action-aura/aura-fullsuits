# Adaptive Layout Contract (M6.5)

Real, shared, pure adaptive-layout authority — `com.actionaura.retail.ui.adaptive`,
proven by `AuraWindowSizeClassTest.kt` (5/5).

## `AuraWindowSizeClass` — deliberately not the Android `WindowSizeClass`

M6.5's own explicit rule: "do not rely on Android-only WindowSizeClass
inside commonMain without an abstraction." `androidx.compose.material3.windowsizeclass`
was, at the time this module's Compose Multiplatform 1.7.0/Kotlin
2.0.21 versions were pinned, an Android-only real API — rather than
gate it behind an `expect`/`actual` wrapper, this module defines its
own tiny, pure, dependency-free classification:

```kotlin
enum class AuraWindowSizeClass { Compact, Medium, Expanded }
fun fromWidthDp(widthDp: Int): AuraWindowSizeClass
```

Breakpoints (600dp / 840dp) are Google's own real, published
Material width-based breakpoints — standard values, not invented.

## Real, pure, tested boundary values

`AuraWindowSizeClassTest.kt` proves the exact boundary behavior:
`599 → Compact`, `600 → Medium` (not Compact), `839 → Medium`, `840 →
Expanded` (not Medium) — real boundary-value testing, matching this
whole session's established discipline of proving edges exactly, not
just "clearly inside" cases.

## `AuraWindowSize` — real, minimal container-size contract

```kotlin
data class AuraWindowSize(val widthDp: Int, val heightDp: Int) {
    val sizeClass: AuraWindowSizeClass
    val isLandscape: Boolean  // real widthDp > heightDp comparison, never assumed from orientation alone
}
```

The platform Compose entry point (`App.kt`, measured via
`BoxWithConstraints` — real available content size, never a device
model/marketing-name check) constructs this and passes it down;
`commonMain` layout decisions (`AuthenticatedAppShell`) never query a
platform API directly.

## Real, current usage

`AuthenticatedAppShell` (M6.6) branches on `windowSize.sizeClass`:
`Compact` → bottom `NavigationBar`; `Medium`/`Expanded` → permanent
`NavigationRail` — proven by the real, successful
`:androidApp:assembleDebug` build wiring this end-to-end.

## Real, disclosed scope not covered this milestone

Safe-area/display-cutout/keyboard-inset handling is deferred to
whichever vertical slice first has a scrolling/edge-sensitive layout
that needs it (none of Category/Branch/Reporting/Import's own M6
screens have content dense enough to require it yet) — not silently
assumed solved by this contract alone.
