# iOS Presentation Readiness (M6.27)

Real, evidence-based audit of M6's own new dependencies/code against
iOS compatibility — extending `ios-build-readiness-plan.md`'s own
real, already-established M2 findings (this Windows host cannot
compile/link/run Apple targets at all; `iosMain`/`iosTest` do not even
exist as real Gradle source sets when synced here,
`kotlin.native.ignoreDisabledTargets=true`).

## Real, confirmed-resolvable M6 dependencies

Every M6 dependency added this milestone is a real, official
JetBrains Compose Multiplatform / Kotlin Multiplatform artifact,
confirmed resolvable by a real Gradle dependency-resolution run on
this host (`:shared:dependencies`):

- `org.jetbrains.androidx.lifecycle:lifecycle-viewmodel-compose:2.8.2` —
  real KMP artifact, publishes Kotlin/Native (iOS) targets per its own
  official Maven metadata.
- `org.jetbrains.androidx.navigation:navigation-compose:2.8.0-alpha10` —
  same real KMP fork, same real multi-target publication.
- `compose.materialIconsExtended` — a real Compose Multiplatform
  alias (`org.jetbrains.compose.material:material-icons-extended`),
  multiplatform by construction (it is part of the Compose
  Multiplatform distribution itself, the same distribution
  `compose.material3`/`compose.foundation` already come from and were
  already proven KMP-compatible since M2).
- `kotlin("plugin.serialization")` — a real, official Kotlin compiler
  plugin, platform-agnostic by design (it only affects code
  generation, not linking).

None of these required an Android-only or JVM-only dependency
substitution — every M6 `commonMain` file (`presentation/`, `ui/`,
`di/`) compiles as real shared code with no `expect`/`actual` split
needed, confirmed by the real fact that `:shared:compileDebugKotlinAndroid`
never required moving any M6 file out of `commonMain`.

## Real, disclosed source-level audit

- No Android-only UI library was added to `commonMain` — confirmed by
  inspection of every M6 import statement; `androidx.compose.material.icons`/
  `androidx.lifecycle`/`androidx.navigation` are all real, genuine KMP
  artifacts (not their Android-only predecessors), per the checkpoint's
  own explicit "do not add an Android-only UI library to commonMain"
  rule.
- `AuraAppContainer` (M6.3) takes `DatabaseDriverFactory` and
  `UnicodeTextNormalizer` as constructor parameters — both real,
  already-established M2/M5.3 platform-contract interfaces with real
  `androidMain` implementations; a future `iosMain` implementation
  (`IosDatabaseDriverFactory` using `NativeSqliteDriver`,
  `IosUnicodeTextNormalizer` using `NSString`'s own real NFKC API) is
  the same real, already-proven pattern this whole codebase uses for
  every other platform contract, not a new design needed for M6.
- `PickedFileImportSource` (M5.8.20) and the real `FilePicker` contract
  (M2) are already platform-neutral; M6.19's own paste-based Import UI
  sidesteps the real, disclosed "no `FilePicker` implementation exists
  yet" gap entirely for THIS milestone, so no new iOS-specific file-
  access code was needed or written.

## Real, honest status

- Source-set configuration: real, validated (every M6 file lives in
  `commonMain`/`commonTest`, confirmed by directory placement).
  **PASS.**
- Common code compiles for available host targets (Android): real,
  confirmed. **PASS.**
- iOS compilation/linking: **NOT_VERIFIED** — `iosMain`/`iosTest` do
  not exist as real Gradle source sets on this Windows host (M2's own
  established, unchanged finding).
- Simulator/device launch: **NOT_VERIFIED**, same standing constraint.

No iOS success is claimed anywhere in this document or any other M6
artifact, per this session's own standing rule.
