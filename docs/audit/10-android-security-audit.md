# Android Security Audit

Builds on Phase 4's own verification (`docs/android/*`, `docs/privacy/clinic-android-data-boundary.md`),
re-confirmed against current source in this pass rather than merely cited.

## Exported components — PROVEN

Exactly one `android:exported` attribute exists in each manifest
(re-grepped this pass) — `.MainActivity`, required because it's the
launcher activity. No services, receivers, content providers, or
`FileProvider` authorities are declared in either
`android/aura-{retail,clinic}/app/src/main/AndroidManifest.xml`. No deep
links (`<data android:scheme=...>`) exist. **No exported-component attack
surface beyond the mandatory launcher activity, on either product.**

## Network security config — PROVEN

`network_security_config.xml` (identical, reused verbatim on both products):
cleartext traffic restricted to `127.0.0.1`/`localhost` only; all other
domains require HTTPS with standard system trust-anchor verification (no
`<trust-anchors>` override, no `certificatePinning` bypass, no
`cleartextTrafficPermitted="true"` at the base-config level). No TLS
verification is bypassed anywhere in the OkHttp client construction
(`ApiClient.kt` — plain `OkHttpClient.Builder()`, no custom
`X509TrustManager`, no `hostnameVerifier` override).

## WebView — PROVEN ABSENT

Zero `WebView` usage in either app's Kotlin source. The only string match for
"WebView" in the codebase is a **comment** in both `ServerBootstrap.kt` files
referencing how the *original* source app worked ("the same way the WebView
build did") — the migrated apps are 100% native Jetpack Compose, so the
entire class of WebView-configuration risks (JavaScript-bridge injection,
`setAllowFileAccess`, mixed content) **does not apply**.

## Logging / Logcat leakage — PROVEN (re-confirmed this pass)

Zero `android.util.Log`/`println`/`printStackTrace` calls anywhere in either
app's `app/src/main/java` tree (re-grepped this pass, same result as Phase 4).
No request/response body logging exists (no `HttpLoggingInterceptor` registered
in either `ApiClient.kt`). Nothing can leak to Logcat because nothing is
logged, on either product.

## Android backup — PROVEN

`android:allowBackup="false"` in both manifests — the OS will not include
either app's private data (including the SQLite database) in Auto Backup or
`adb backup`.

## Embedded server exposure — PROVEN

Same finding as `08`: both apps' `main.py` binds `127.0.0.1` only, via the
same shared launcher pattern — not reachable from the local Wi-Fi network.

## Clipboard / screenshot / recent-apps leakage — UNVERIFIED

Neither `FLAG_SECURE` (which would block screenshots/recent-app-preview
capture) nor any clipboard-related code was found in either app's Kotlin
source — meaning **neither app currently sets `FLAG_SECURE`**, so a
screenshot or the Android recent-apps switcher preview could capture
whatever is on screen (e.g. a patient's name in Clinic, or a sale total in
Retail) at the OS level. This was not exercised on a real device (no
device available), so the *consequence* is UNVERIFIED, but the *absence of
the mitigating flag* is PROVEN by source read. For Clinic specifically this
is a genuine, real privacy-relevant gap — see `11`.

## Embedded secrets — PROVEN NONE

No hardcoded API keys/tokens found in `android/*/app/build.gradle` or
`app/src/main/java`/`python`. `CommercialIdentity.kt` (added Phase 4)
deliberately hardcodes `tenantId = null` and generates `installationId` via
`UUID.randomUUID()` — no real license key or tenant secret is embedded.

## APK signature state — PROVEN (re-confirmed this pass, see `01`)

Debug APKs carry Android's standard, non-secret auto-debug signature.
Staging and release APKs are **genuinely unsigned** (`apksigner verify`
reports `Missing META-INF/MANIFEST.MF` for both — confirmed in Phase 4 and
not re-built/re-signed in this audit pass since no signing key has been
introduced since).

## Root/device assumptions, external storage — PROVEN NONE FOUND

No `Environment.getExternalStorageDirectory()` or `MediaStore` writes were
found in either app — all writable storage goes through the app's private
`filesDir` (`android_platform.py`'s `resolve()`), which is sandboxed per-app
by the OS regardless of root status. No root-detection or root-dependent
code exists (neither required nor checked for).

## Summary

| Item | Status |
|---|---|
| Exported components (beyond mandatory launcher) | PASS — none |
| Cleartext network scope | PASS — loopback only |
| TLS verification bypass | PASS — none |
| WebView risk class | PASS — not applicable (no WebView) |
| Logcat/network leakage | PASS — nothing logged |
| Android backup | PASS — disabled |
| Embedded secrets | PASS — none |
| `FLAG_SECURE` (screenshot/recents protection) | **FAIL — not set, either product** |
| Code signing | **FAIL — unsigned staging/release, by design this phase** |
| External storage usage | PASS — none (sandboxed filesDir only) |
