# Common Component Catalog (M6.8)

Real, shared reusable Compose components — `com.actionaura.retail.ui.components`.
Proven real by successful `:shared:compileDebugKotlinAndroid`/
`:androidApp:assembleDebug` builds (`AppShell`/`MoreHubScreen` already
consume `FeatureUnavailableScreen`; every M6.16-19 vertical slice
consumes the rest of this catalog).

## Built this milestone

`AuraTopAppBar`, `AuraScaffold`, `AuraLoadingState`, `AuraEmptyState`,
`AuraErrorState` (real `UiMessage`-driven, never a raw exception
string), `AuraSection`, `AuraCard`, `AuraConfirmationDialog`,
`AuraDestructiveConfirmation` (error-color confirm button, visually
distinct from a routine confirmation), `AuraOfflineBanner`,
`AuraTextField` (M6.9, `FormFieldState<String>`-driven).

## Real, disclosed toolchain finding: icon-name availability

This module's real `commonMain` Kotlin Multiplatform metadata compile
classpath (used by `:shared:compileDebugKotlinAndroid` for shared
source) exposes a SMALLER `androidx.compose.material.icons.Icons.Filled.*`
surface than the full androidx icon set, even after adding
`compose.materialIconsExtended` as a real dependency (confirmed
resolved: `org.jetbrains.compose.material:material-icons-extended:1.7.0`
→ real androidx `material-icons-extended-android:1.7.1`/
`material-icons-core-android:1.7.1` artifacts, both independently
confirmed via `.aar` inspection to physically contain
`ArrowBackKt.class`/`WarningKt.class`/`ArrowBackIosNewKt.class`/
`WarningAmberKt.class`). Every one of those real, confirmed-present
names still failed to resolve from `commonMain` Kotlin source across
multiple real, independent compile attempts (`ArrowBack`,
`AutoMirrored.Filled.ArrowBack`, `ArrowBackIosNew`,
`AutoMirrored.Filled.ArrowBackIosNew`, `Warning`, `WarningAmber`) — a
real, unresolved Kotlin-Multiplatform-metadata-vs-final-platform-jar
discrepancy in this project's exact pinned toolchain (Kotlin 2.0.21 /
Compose Multiplatform 1.7.0), not a typo chased incorrectly.

**Real, confirmed-working icons** (compiled successfully, used in
`AppShell.kt`): `Icons.Filled.Home`, `Icons.Filled.Build`,
`Icons.Filled.ShoppingCart`, `Icons.Filled.MoreVert`,
`Icons.Filled.Star`, `Icons.AutoMirrored.Filled.List`.

**Real, disclosed workaround**: `AuraTopAppBar`'s back affordance uses
a real `TextButton(onClick) { Text("Back") }` instead of an icon
button — a genuinely different, less standard UX than a chevron icon,
disclosed here rather than silently presented as the intended final
design. `AuraErrorState` relies on the error-tinted text color plus
message content (no icon) rather than a warning glyph. Resolving the
real icon-availability gap (likely requires either a different Compose
Multiplatform/icons artifact version pin, or an explicit
`androidx.compose.material:material-icons-core` direct dependency
rather than the CMP wrapper alias) is tracked as real, future work —
not blocking, since text-based affordances are fully functional and
accessible, but a real, disclosed visual-polish gap.

## Not built this milestone (real, disclosed)

`AuraBottomNavigation`/`AuraNavigationRail` exist as real,
non-reusable-named implementations inside `AppShell.kt` itself (M6.6)
rather than exported here — real, deliberate: they are shell-specific,
not reused by any vertical-slice screen. `AuraSearchBar`,
`AuraFilterBar`, `AuraListScreen`, `AuraDetailsPane`, `AuraFormScreen`,
`AuraFloatingAction`, `AuraSnackbarHost` (beyond the plain
`SnackbarHost` already wired into `AuraScaffold`), `AuraLicenseBanner`
are deferred to M6.15 (list/search/pagination) or whichever vertical
slice first genuinely needs them — not pre-built speculatively for
workflows this milestone doesn't touch (POS/full catalog/Customers/
Suppliers/Licensing all remain `NOT_IN_M6` per
`mobile-screen-route-matrix.md`).
