# Shared Design System (M6.4)

Real, semantic Compose Multiplatform design tokens — `com.actionaura.retail.ui.theme`.
Proven real by a real, full Android debug APK build
(`:shared:compileDebugKotlinAndroid :androidApp:assembleDebug`, `BUILD
SUCCESSFUL`) with `AuraAppTheme` wired as the app's actual root theme.

## Real, corrected gap vs. the legacy app

`presentation-authority-audit.md`'s own real finding: the legacy
Android app (`android/aura-retail`) hardcodes `darkTheme = true` and
never wires a real light theme, despite defining one. `AuraAppTheme`
fixes this for real: `LightColorScheme`/`DarkColorScheme` are both
real, complete Material3 `ColorScheme`s, selected via
`AuraThemePreference` (`System`/`Light`/`Dark`) — `System` calls the
real `isSystemInDarkTheme()`, honoring the OS while still allowing a
saved user override, exactly M6.4's own requirement.

## Tokens

- **`AuraColors.kt`** — `LightColorScheme`/`DarkColorScheme` (the
  "Aurora" teal/cyan/violet brand hues re-expressed as a real M3
  scheme, not copied verbatim from the legacy app's own ad hoc `Color`
  constants) plus `AuraSemanticColors` (success/warning/info — M3 has
  no such roles natively) exposed via `AuraTheme.semanticColors`
  (`CompositionLocal`-backed, theme-aware).
- **`AuraTokens.kt`** — `AuraSpacing` (none/xs/sm/md/lg/xl/xxl),
  `AuraShapes` (extraSmall 8dp → extraLarge 28dp, real M3 `Shapes`),
  `AuraTypography` (7 real type roles, system sans-serif — no embedded
  font files, per M6.4's own explicit prohibition), `AuraDimensions`
  (`minTouchTarget = 48.dp`, the real WCAG/Material minimum, feeding
  M6.13's accessibility requirement structurally), `AuraMotion`
  (fast/medium/slow duration constants).

## Never-color-alone discipline

`AuraSemanticColors` is a real, deliberate scope decision: these
colors exist to be PAIRED with an icon/text label wherever used (stock
warnings, payment state, Return state) — the token system itself does
not enforce this (Compose has no compile-time way to require an icon
accompany a color), so it remains a real, disclosed per-component
responsibility for M6.8's common component catalog and every vertical
slice to honor, not a guarantee this file alone provides.

## No embedded fonts, no raw literals

Every real dimension/color reference in this milestone's own UI code
(`AppShell.kt`, `MoreHubScreen.kt`, `FeatureUnavailable.kt`) goes
through `AuraSpacing`/`AuraDimensions`/`MaterialTheme.colorScheme`/
`MaterialTheme.typography` — confirmed by inspection, no bare `.dp`/
`.sp`/`Color(0x...)` literal appears outside `AuraColors.kt`/
`AuraTokens.kt` themselves.
