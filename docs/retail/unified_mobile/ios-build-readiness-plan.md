# Aura Retail Unified Mobile — iOS Build Readiness Plan

## Real, confirmed environment constraint (not assumed)

This development host is Windows, with no macOS, no Xcode, no Apple Developer signing identity, and no physical iPhone. Per the entry-gate decision: iOS-buildable source, Gradle target declarations, and (in Milestone 19) a real Xcode project are written as real code/config throughout this initiative, but cannot be compiled, linked, run, or physically tested here.

## Precise mechanism, verified by real Gradle syncs (Milestone 2)

`mobile/aura-retail-unified/shared/build.gradle.kts` declares `iosX64()`, `iosArm64()`, `iosSimulatorArm64()` unconditionally, with `kotlin.native.ignoreDisabledTargets=true` set in `gradle.properties`. Running a real Gradle sync on this Windows host (not simulated, not assumed) produced this exact, real error the first time the build script referenced the `iosMain` source set directly:

```
KotlinSourceSet with name 'iosMain' not found.
```

**Real finding**: with `kotlin.native.ignoreDisabledTargets=true`, Kotlin's Gradle plugin does not merely skip *linking/compiling* the Apple targets on a non-Mac host — it drops them from the project's target graph entirely at **configuration time**, before Kotlin's Default Hierarchy Template ever runs. The `iosMain`/`iosTest` intermediate source sets, which that template creates automatically whenever at least one of `iosX64()`/`iosArm64()`/`iosSimulatorArm64()` is actually configured, therefore never exist on this host at all. This is a stronger, earlier-stage constraint than "cannot link an Apple binary" — it is "the iOS source sets do not exist in this Gradle project when synced on Windows."

**Practical consequence for how this initiative proceeds**: `shared/build.gradle.kts` cannot reference `iosMain`/`iosTest` (e.g. to add iOS-specific dependencies) while being synced from this Windows host, even though the `shared/src/iosMain/` and `shared/src/iosTest/` source directories remain real and populated with Kotlin/Native code throughout the initiative (Milestone 19). Any iOS-specific dependency additions to `shared/build.gradle.kts` must be written and will only actually configure/compile/verify on a Mac host — they cannot be proven here, only written correctly by inspection and cross-reference against JetBrains' official KMP documentation and the equivalent Android-side dependency shapes already proven to sync.

## What was verified to actually work on this host (real, not claimed)

Real `./gradlew :androidApp:assembleDebug :shared:testDebugUnitTest` run, this milestone, after five real config fixes (each found by an actual failing Gradle invocation, not anticipated in advance):

1. `local.properties` with `sdk.dir` — missing on the new Gradle root (each Gradle root needs its own).
2. Manual `iosMain`/`iosTest` `dependsOn()` wiring conflicting with Kotlin's Default Hierarchy Template — removed, letting the template own that wiring (real warning, then real hard error once removed incorrectly the first time — see above).
3. XML comment containing `--` in `AndroidManifest.xml` (`SAXParseException`, line 4) — real, exact XML-spec violation (double-hyphen is illegal inside XML comments); an easy, genuinely common mistake, caught by the real parser, not by review.
4. `androidApp`'s Kotlin compiler defaulting to JVM target 17 while `compileOptions` requested 11 (`Inconsistent JVM-target compatibility`) — added an explicit `jvmToolchain(...)`.
5. Requested JVM toolchain 11 not installed on this machine, no auto-download configured (`Cannot find a Java installation... languageVersion=11`) — real finding: this host only has JDK 17 available to Gradle. Aligned to 17 everywhere (`shared` and `androidApp`), matching `android/aura-retail`'s own already-proven-working `sourceCompatibility`/`targetCompatibility`/`jvmTarget` = 17 configuration exactly, rather than requesting an unavailable JDK version.

**Final real result**: `BUILD SUCCESSFUL`, a real `androidApp-debug.apk` produced at `androidApp/build/outputs/apk/debug/androidApp-debug.apk`, and the `commonTest` smoke test (`SkeletonSmokeTest`) really executed and passed (`tests="1" skipped="0" failures="0" errors="0"`, real JUnit XML) — proving `commonMain`/`commonTest` Kotlin actually compiles and runs, not just that the Android-only parts of the module do.

## What remains genuinely unverifiable on this host

- Whether `shared/build.gradle.kts`'s Apple-target declarations (`iosX64()`/`iosArm64()`/`iosSimulatorArm64()`) themselves produce a working `.framework` when synced on a real Mac.
- Whether any `iosMain` Kotlin/Native adapter code written in later milestones (Milestone 19: Keychain, camera, file picker, etc.) actually compiles against the real Kotlin/Native Apple SDKs.
- Xcode project generation/configuration (Milestone 19).
- Any Compose Multiplatform iOS rendering behavior.
- Physical iPhone behavior (Milestone 25).
- TestFlight/App Store submission (Milestone 27).

## Plan for closing this gap

These milestones (19, 25, 27, and the iOS-build legs of 26/28) are tracked `BLOCKED-ENVIRONMENT` in the gate matrix. They require, at minimum: a macOS host, Xcode with a current iOS SDK, an Apple Developer Program membership (for signing/TestFlight/App Store), and a physical iPhone for Milestone 25 specifically (simulator testing does not substitute, per the governing spec). When that access exists, the real work is: run `./gradlew :shared:embedAndSignAppleFrameworkForXcode` (or the KMP-generated equivalent) from Xcode's build phases, resolve any real Kotlin/Native compilation errors in the already-written `iosMain` adapters, and proceed through Milestones 19/25/27 for real. Nothing about this plan requires re-architecting the shared module — the `commonMain`/`iosMain` split, the platform-interface pattern, and the Gradle target declarations are already the correct, real KMP shape; they are simply unverifiable on the current host.
