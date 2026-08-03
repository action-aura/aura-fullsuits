# Aura Retail Unified Mobile — Architecture Decision Records

## ADR-1: Separate Gradle root (`mobile/aura-retail-unified/`), not a module inside `android/aura-retail`

**Decision**: New, independent Gradle project at `mobile/aura-retail-unified/`, with its own `settings.gradle.kts`, wrapper, and `local.properties`, rather than adding Kotlin Multiplatform to the existing `android/aura-retail` Gradle root.

**Why**: `android/aura-retail` remains the migration baseline and rollback target through Milestone 18 (per the governing spec's own instruction: "do not replace it until proven"). Chaquopy's Gradle plugin and Kotlin Multiplatform's plugin have real, documented compatibility friction (Chaquopy hooks Android's variant API in ways that conflict with KMP's own Android-target configuration). Keeping them as two separate, independently buildable Gradle roots avoids that friction entirely and lets `android/aura-retail` keep building and shipping unmodified for as long as it needs to during the migration window.

## ADR-2: Platform capabilities as plain `interface`s in `commonMain`, not `expect`/`actual`

**Decision**: `SecureCredentialStore`, `DatabaseDriverFactory`, `CameraBarcodeScanner`, etc. (`shared/src/commonMain/kotlin/com/actionaura/retail/platform/PlatformContracts.kt`) are declared as ordinary Kotlin interfaces in `commonMain`, implemented by ordinary classes in `androidMain`/`iosMain`, and provided to shared use cases via constructor injection — not via Kotlin's `expect class`/`actual class` mechanism.

**Why**: `expect`/`actual` is the right tool when a type's *shape* must exist identically on every platform with platform-specific internals (e.g. a `expect class AtomicInt`). Here, the shape itself doesn't need to be identical — it needs to be *substitutable*, which is exactly what an interface + DI gives, with a smaller, more explicit surface and no special compiler mechanism to reason about. This also matches the governing spec's own wording verbatim ("Define explicit platform interfaces for...").

## ADR-3: JVM target 17 everywhere, not 11

**Decision**: Both `shared`'s Android target and `androidApp` compile against JVM target 17.

**Why**: real, not aspirational — this development host only has JDK 17 available to Gradle (no JDK 11 installed, no toolchain auto-download configured), and `android/aura-retail` already proves 17 works end-to-end for `minSdk 26`/`compileSdk 34`. Matching the sibling project's already-verified configuration is lower-risk than introducing a second JVM target convention this initiative would need to justify separately.

## ADR-4: `kotlin.native.ignoreDisabledTargets=true`, iOS targets declared unconditionally

**Decision**: `shared/build.gradle.kts` always declares `iosX64()`/`iosArm64()`/`iosSimulatorArm64()`, and `gradle.properties` sets `kotlin.native.ignoreDisabledTargets=true` rather than conditionally omitting the targets based on host OS.

**Why**: the alternative (detecting `HostManager.hostIsMac` and only declaring Apple targets on a Mac) would make the build script itself platform-dependent, which is exactly the kind of divergence this whole initiative exists to eliminate. Declaring the targets unconditionally and letting Kotlin's own tooling decide what it can build on the current host keeps one source of truth for "what this product targets," while `ignoreDisabledTargets` prevents that from hard-failing every sync on a non-Mac machine. See `ios-build-readiness-plan.md` for the exact real consequence this had (the `iosMain` source set not existing on this host) and how it was worked around in the Gradle script.

## ADR-5: Compose Multiplatform UI lives in `commonMain`, not per-platform

**Decision**: `shared/src/commonMain/kotlin/com/actionaura/retail/ui/` owns all screen composables; `androidApp` and (later) `iosApp` are thin entry-point shells (`MainActivity` calling `setContent { App() }`; iOS's future equivalent calling the same `App()` from Swift via the Kotlin/Native framework).

**Why**: this is the governing spec's own core mandate ("Compose Multiplatform for shared UI where practical... ui: screens... navigation contracts" listed explicitly under `commonMain`'s authority). Verified real this milestone: `App()` (a real `MaterialTheme`/`Surface`/`Text` composable) compiles in `commonMain` and renders through `androidApp`'s real `MainActivity`, producing a real, installable debug APK.

## ADR-6: Dead Clinic API surface is deleted, not migrated, during Milestone 5

**Decision**: `net/AuraApi.kt`'s Clinic-domain Retrofit methods (Milestone 1 finding) are not ported into `shared/src/commonMain/kotlin/com/actionaura/retail/data/` at all — Milestone 5 deletes them from the legacy Android app's own `AuraApi.kt` (proving zero remaining references first) rather than carrying them forward "just in case."

**Why**: per the governing spec's explicit instruction ("Do not migrate Clinic models, requests, or screens into the unified Retail application") and Milestone 1's own real-code confirmation that zero call sites exist in the real navigation graph.

## ADR-7: Categories/Branches/Reports gaps become new shared-core domain logic, not new Python backend routes

**Decision**: where Milestone 1's Product-Owner-Override audit found a genuine backend-capability gap (categories has no edit/archive/duplicate-check; branches has no edit/archive/selector; sales-trend/top-products lack weekly/monthly buckets and revenue-ranking), the new logic is designed and built directly in `shared/src/commonMain/kotlin/com/actionaura/retail/` Kotlin — never as new routes added to `products/retail/backend/api/retail_api.py` first.

**Why**: this whole initiative's core migration mandate is that the embedded Python/Chaquopy backend is being eliminated from the mobile runtime entirely, not extended. Building new mobile-only capability in Python first would create exactly the "second authority" the governing spec forbids, and would be throwaway work the instant Milestone 18's cutover happens. The existing Python behavior (products' status/uniqueness pattern, the existing sales-trend/top-products SQL) is used as the *behavioral reference* for the new Kotlin logic, per `complete-retail-capability-matrix.md` — reused as a specification, not as a dependency.
