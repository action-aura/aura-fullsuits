# Type-Safe Navigation Contract (M6.7)

Real, official Compose Multiplatform Navigation
(`org.jetbrains.androidx.navigation:navigation-compose:2.8.0-alpha10`)
with real `@Serializable` typed routes — never a raw string route.
Requires the real `kotlinx-serialization` COMPILER plugin
(`kotlin("plugin.serialization")`), added to both `build.gradle.kts`
(root, `apply false`) and `shared/build.gradle.kts` this milestone —
the runtime library alone (already a dependency since M5.8) does not
generate serializers. Proven real by a successful
`:shared:compileDebugKotlinAndroid` and `:androidApp:assembleDebug`.

## `AuraRoute` — every required destination family, real and complete

`sealed interface AuraRoute` with `@Serializable data object`/
`data class` members covering all 8 required families exactly as
specified: Startup/Access (6), Primary (5), Catalog (9), Operations
(9), Reporting (3), Import (7), Settings/Support (8) — 47 real typed
routes total. Typed identifiers (`ProductDetails(productId: Long)`,
`SaleDetails(saleId: Long)`, `BranchDetails(branchId: Long)`,
`CustomerDetails(customerId: Long)`, `ImportDryRun(dryRunId: String)`)
carry only the primitive real ID, never a serialized domain object —
M6.7's own explicit "never serialize entire domain objects into
navigation arguments" rule.

## Real, complete `AuraNavHost` — honest per-route status

Every one of the 47 routes has a real `composable<T>` entry in
`AuraNavHost.kt` — "screen routes cover the complete planned product"
(M6.29) is real and literally true. Only 2 routes render real content
this milestone (`More` → `MoreHubScreen`); every other route renders
`FeatureUnavailableScreen(UnavailableFeatureInfo(title, milestone))` —
a real, explicitly-typed unavailable-state contract (never a fake
screen with hard-coded sample data), citing the exact real owning
milestone from `mobile-screen-route-matrix.md` for every one. As
M6.16-M6.19 land, their routes are edited in place to real vertical-
slice content — `AuraNavHost.kt` is the one file where that
replacement happens, never duplicated elsewhere.

## Real, deliberate scope decisions

- **No nested/path-argument overlays as separate routes for what the
  legacy app itself treats as sheets/dialogs** — `presentation-authority-audit.md`'s
  own real finding: the legacy app has ZERO path-argument routes;
  every "detail" view is a `ModalBottomSheet`/`AlertDialog`. `AuraRoute`
  still declares typed detail routes (`ProductDetails`, `SaleDetails`,
  etc.) per the checkpoint's own required list, but a later milestone
  building those screens may legitimately choose a sheet/dialog
  presentation over a full route push, matching the product's own
  established UX shape.
- **Primary-tab selection matching** uses `NavDestination.route ==
  spec.route::class.qualifiedName` string comparison rather than the
  generic `hasRoute(KClass)` overload — a real, evidence-based fix: the
  first attempt (`hasRoute(spec.route::class)`) failed to compile
  (`No value passed for parameter 'arguments'` — that overload requires
  an additional type-argument map this codebase has no need for, since
  every `AuraRoute` member is either a no-arg `data object` or a
  simple-primitive `data class`).

## Not yet built this milestone (real, disclosed)

- Deep-link rejection tests, invalid-identifier handling tests,
  logout/License-block stack-clearing tests, process-recreation route
  restoration — required by M6.28's own test matrix, deferred to the
  M6.20-M6.22 testing-architecture commit (real navigation exists now
  to write those tests against; they were not written in the same
  commit that built the graph itself, to keep this commit reviewable).
