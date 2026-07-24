# Phase 7V — Android rc.2 Production-Signed Build Report (Part H)

## Real gap found and fixed before either build could succeed

Both products' `licensing/DeviceIdentity.kt` called
`KeyGenParameterSpec.Builder.setIsStrongBoxBacked(true)` (API 28+) guarded only by a Kotlin
`try/catch (Throwable)`, while `minSdk = 26`. This is safe at runtime (the JVM resolves the method
lazily and the catch handles absence), but Android Lint's static `NewApi` check does not accept
`try/catch` as a version guard — `lintRelease` **failed** with a hard error on first run for both
products, blocking `assembleRelease`/`bundleRelease` entirely. Fixed identically in both files: the
call is now wrapped in `if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) { try { ... } catch
(_: Throwable) { ... } }` — the runtime behavior (best-effort StrongBox, fallback to standard
TEE-backed key) is unchanged; only the static guard was added. This is a pre-existing defect (from
Phase 7's original Part F Android device-identity work), not something introduced this session, and
is exactly the class of "newly discovered P0/P1 release blocker" the governing spec permits a
minimal source fix for.

## Build commands (both products, identical)

```
JAVA_HOME="C:\Program Files\Microsoft\jdk-17.0.19.10-hotspot" ./gradlew.bat testReleaseUnitTest lintRelease assembleRelease bundleRelease --no-daemon
```

Both products: `BUILD SUCCESSFUL` after the fix (Clinic: 2m 3s, 80 tasks; Retail: 2m 55s, 80 tasks).
`testReleaseUnitTest` passed for both (release unit tests, including `HardcodedStringAuditTest`,
`StringsCoverageTest`, licensing/state-machine/Kotlin↔Python sync tests — two pre-existing
`kotlin.String?` nullability lint *warnings*, not errors, in the same two test files, unrelated to
this session's changes).

## Signing

Used the existing production keystores at `C:\Users\Dell\AuraSigningKeys\` via each project's own
`keystore.properties` (gitignored, pre-existing, not modified) — no new keys generated, no
passwords printed or logged, no keystore copied into the repository.

| | Clinic | Retail |
|---|---|---|
| Keystore alias | `aura-clinic-release` | `aura-retail-release` |
| Signer DN | `CN=Action Aura, OU=Aura Clinic, O=Action Aura, L=Amman, ST=Amman, C=JO` | `CN=Action Aura, OU=Aura Retail, O=Action Aura, L=Amman, ST=Amman, C=JO` |

## Artifacts

| Artifact | SHA-256 | Size |
|---|---|---|
| Clinic APK | `android/aura-clinic/app/build/outputs/apk/release/app-release.apk` — `452a4c87111a193b8581f2ca14b71607d322ebc24b5977f88a0aecf93887a36d` | 51,655,957 bytes |
| Clinic AAB | `android/aura-clinic/app/build/outputs/bundle/release/app-release.aab` — `109a67ec04b9f97c7cbe855d9f5181221dde3d3ab57a13e94d8f7e7b2f990cdf` | 32,437,894 bytes |
| Retail APK | `android/aura-retail/app/build/outputs/apk/release/app-release.apk` — `33792b86cb48d23052428d59cbe6582ec518de1b07ac4922235adaefe7a455df` | 65,266,605 bytes |
| Retail AAB | `android/aura-retail/app/build/outputs/bundle/release/app-release.aab` — `087136d91b1068545d03cc4dc5a1d3a0897f606bd819ca0a1924fd956a2a20dc` | 39,308,430 bytes |

rc.1's original signed artifact files are not present on disk in this checkout (only their
checksums/fingerprints survive in `docs/release/wave1b/release-candidate-manifest.md`), so no rc.1
artifact was overwritten by this build.

## Version verified

`versionCode 3`, `versionName "1.0.0-rc.2"` in both `app/build.gradle` files (unchanged from Phase
7 — this session did not touch versioning, only the lint-blocking source bug).

## Signature verification (`apksigner verify --print-certs`)

Both APKs verified successfully; certificate fingerprints recorded in
`android-certificate-continuity.md`. Both release builds are non-debuggable (standard `release`
build type, no `debuggable true` override present in either `.iss`-equivalent Gradle config).

## Neither AAB uploaded anywhere

Consistent with Wave 1B precedent — both are release-ready bundles, not published Play Console
listings; no Play Store work was in scope for Phase 7V.
