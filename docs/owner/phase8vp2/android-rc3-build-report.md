# Phase 8V-P2 — Android rc.3 Build Report

Built from the working tree at commit `f03d775` ("feat: add flask commercial preflight environment
safety check") -- i.e. after both of this phase's source commits (the Scenario 7 fix and the
preflight command). Neither Android module's own source changed in either commit (both are Owner-only
Python/Flask changes under `owner/`), so this build is equivalent to building from any commit at or
after `f03d775`, including this phase's final documentation-only commit. Toolchain: JDK 17.0.19
(Microsoft build),
Android SDK (build-tools 34.0.0, platform-tools present), Gradle wrapper 8.9 (repo-pinned).

Source-level version fields were already bumped to `1.0.0-rc.3` / `versionCode 4` in the Phase 8V-P
session (`android/aura-clinic/app/build.gradle`, `android/aura-retail/app/build.gradle`) but no
artifact had actually been built from them until this session.

`gradlew clean` failed both products with a Windows file-lock error on `app/build/outputs/apk/release`
(a held file handle, not a build defect -- no emulator/process was using it; most likely antivirus or
an indexer). Not forced past with a destructive workaround; skipped `clean` and ran the remaining
steps directly, which Gradle handles correctly via its normal incremental/up-to-date output
replacement (build outputs shown below are from this session's own compile, not stale rc.2 leftovers
-- confirmed via `versionName`/`versionCode` in the badging dump and via fresh SHA-256 hashes).

## Clinic

```
$ ./gradlew --no-daemon testDebugUnitTest      -> BUILD SUCCESSFUL in 2m 39s
$ ./gradlew --no-daemon lintRelease            -> BUILD SUCCESSFUL in 4m 12s (no HTML report failures)
$ ./gradlew --no-daemon assembleRelease bundleRelease -> BUILD SUCCESSFUL in 2m 14s
```

Output:
- `android/aura-clinic/app/build/outputs/apk/release/app-release.apk`
- `android/aura-clinic/app/build/outputs/bundle/release/app-release.aab`

## Retail

```
$ ./gradlew --no-daemon testDebugUnitTest lintRelease -> BUILD SUCCESSFUL in 2m 53s
$ ./gradlew --no-daemon assembleRelease bundleRelease -> BUILD SUCCESSFUL in 1m 39s
```

Output:
- `android/aura-retail/app/build/outputs/apk/release/app-release.apk`
- `android/aura-retail/app/build/outputs/bundle/release/app-release.aab`

## Real, verified properties of both release APKs (via `aapt dump badging`)

```
Clinic: package: name='com.actionaura.clinic' versionCode='4' versionName='1.0.0-rc.3' ...
Retail: package: name='com.actionaura.retail' versionCode='4' versionName='1.0.0-rc.3' ...
```

Package IDs unchanged from every prior release. No `application-debuggable` attribute present in
either badging dump (aapt only emits that line when `android:debuggable="true"` is set) -- confirms
non-debuggable release builds, consistent with using the `release` build type/signing config, not
`debug` or `staging`.

All four artifacts copied to `dist/android/{clinic,retail}/` (gitignored, same convention as the
existing `dist/installers/` Windows artifacts) for `final-android-artifact-evidence.md`'s hashes and
for the next session's physical-install step to use without rebuilding.

Requirement checklist (Part F): release signing only (yes, see `android-certificate-continuity.md`);
non-debuggable (yes); package IDs unchanged (yes); current versionName/monotonic versionCode (yes,
4 > 3); current `/api/licensing/v1` URL path (unchanged source, not touched this session); current
trust anchor/commercial contract/`commercial_runtime` code (unchanged source); Scenario 7 fix
embedded (yes -- it is Owner-side only, but HEAD includes it); no stale payload/debug endpoint/TLS
bypass/fake paid mode/fake clock/synthetic data baked in (none of this session's changes touch
product code at all); no keystore, password, or private key printed or copied into the repo.
