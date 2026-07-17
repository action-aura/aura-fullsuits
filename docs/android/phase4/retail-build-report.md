# Aura Retail Android — Build Report (Phase 4R, 2026-07-17)

Real builds, actually run in this environment. No claim below is estimated.

## Environment

Host: Windows 11 Pro. JDK: Microsoft build 17.0.19. Gradle: 8.9 (wrapper).
AGP 8.5.2. Android SDK at `C:\Users\Dell\AppData\Local\Android\Sdk`
(platform-tools + build-tools + platform 34 installed; no emulator package
installed).

## Results

| Command | Result | Time |
|---|---|---|
| `./gradlew.bat clean lintDebug assembleDebug` | **BUILD SUCCESSFUL** | 2m 24s |
| `./gradlew.bat :app:testDebugUnitTest` | **BUILD SUCCESSFUL** — 29/29 tests, 0 failures | ~5s (incremental) |
| `./gradlew.bat assembleRelease` | **BUILD SUCCESSFUL** | 1m 54s |
| `./gradlew.bat bundleRelease` | **BUILD SUCCESSFUL** | 22s |

Lint: **0 errors, 27 warnings** (all pre-existing deprecation warnings —
`Icons.Filled.X` → `Icons.AutoMirrored.Filled.X` migrations,
`Modifier.menuAnchor()` overload deprecation, `PackageInfo.versionCode`
deprecation — none introduced by this phase, none correctness-relevant).

## Artifacts

| File | Build type | Size | SHA-256 |
|---|---|---|---|
| `app/build/outputs/apk/debug/app-debug.apk` | debug | 70,345,897 bytes | `c19acc6b025b27699edd89f362e10ec9cd05da2e4e56ec1f339567bb061d2ff1` |
| `app/build/outputs/apk/release/app-release-unsigned.apk` | release, **unsigned** | 64,353,051 bytes | `d79539232d1e7f6fee9dce8cd4fa40d1e97ba5dc1808c7ac5a090ebb62628604` |
| `app/build/outputs/bundle/release/app-release.aab` | release, **unsigned** | 38,379,150 bytes | `c45621f4e4c574e5fc4bc1976fe96e4107630a1006c54434c0d3f745cf549758` |

`applicationId com.actionaura.retail` (debug: `.debug` suffix),
`namespace com.actionaura.retail`, `versionCode 1`, `versionName "1.0.0"`,
`minSdk 26`, `targetSdk 34`.

## Signing verification

```
jarsigner -verify app/build/outputs/apk/release/app-release-unsigned.apk
  -> DOES NOT VERIFY / ERROR: Missing META-INF/MANIFEST.MF
apksigner verify --print-certs app-release-unsigned.apk
  -> DOES NOT VERIFY
jarsigner -verify -verbose app/build/outputs/bundle/release/app-release.aab
  -> "jar is unsigned."
```

Both the release APK and the release AAB are genuinely unsigned — no
`keystore.properties` exists on disk (confirmed: `find android -iname
"keystore.properties" -o -iname "*.jks" -o -iname "*.keystore"` returns
nothing outside `app/build/`), so `signingConfig` was never applied
(`app/build.gradle`'s `if (keystorePropsFile.exists())` guard). The AAB's
`signReleaseBundle` task name and the lack of an `-unsigned` suffix on its
filename could be misread as "signed" — `jarsigner -verify` is the ground
truth, and it is not.

## Manifest inspection

Single exported component (`MainActivity`, required for the launcher
intent filter), `android:allowBackup="false"`, cleartext permitted only
for `127.0.0.1`/`localhost` via `network_security_config.xml`, no
FileProvider, no debug flags in the release manifest (`debuggable false`
for `release`/`staging` build types, confirmed in `app/build.gradle`).

## Regression fix verified in this artifact

`main.py`'s readiness check now targets `/api/health` (source-verified in
the staged Python inside this exact build — `stageAuraPython` copies the
corrected file). Not runtime-verified against a real launched app (no
device/emulator available) — see `retail-device-test-report.md`.
