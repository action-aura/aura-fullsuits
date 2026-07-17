# Phase 4I — Android Security Report (both products)

Status: **PROVEN** for everything checked via static analysis/build
inspection; no runtime/dynamic security testing was performed (no
device/emulator). This is a code-security review, not a penetration test
and not a compliance certification.

## Network security

- `network_security_config.xml` (both): `cleartextTrafficPermitted="true"`
  scoped **only** to `127.0.0.1`/`localhost`; `base-config
  cleartextTrafficPermitted="false"` for everything else. Confirmed by
  reading the actual shipped XML this phase.
- No TLS-verification-bypassing code anywhere (no custom
  `X509TrustManager`, no `HostnameVerifier` override) — grep-confirmed.
- No remote endpoint exists in either app yet (no telemetry, no license
  server, no update server) — the entire network surface is the on-device
  loopback Flask server.

## Exported components

Both manifests: exactly one exported component (`MainActivity`, required
for the `LAUNCHER` intent filter). No exported services, receivers, or
content providers. No `FileProvider` declared in either app.

## Storage

- `android:allowBackup="false"` (both) — Android's automatic device/cloud
  backup never captures either app's data.
- SQLite databases live under each app's private `filesDir` (via
  `android_platform.py`'s path resolution, unchanged from the prior
  migration) — never on shared/external storage.
- Cookie persistence: `SharedPreferences` with `Context.MODE_PRIVATE`
  (both `ApiClient.kt`), app-sandboxed, standard Android isolation.

## Logging

- Zero `android.util.Log` calls, zero `println`/`System.out` calls, in
  either app's Kotlin source (grep-confirmed both before and after this
  phase's changes — this phase added no logging).
- Zero `HttpLoggingInterceptor` (or any OkHttp logging interceptor)
  configured in either `ApiClient.kt` — no request/response body is ever
  logged, in any build type.
- Clinic-specific: two backend `print()` statements capable of leaking
  patient/lab-test names into Logcat (via Chaquopy's stdout forwarding)
  were found and fixed this phase — see `clinic-android-privacy-boundary.md`.
- Regression guards added this phase (`PrivacyLoggingGuardTest.kt`,
  Clinic) so a future change reintroducing any of the above fails a real
  JVM unit test, not just a one-time manual audit.

## Signing / secrets

No keystore, `.jks`, `.keystore`, or password committed anywhere in either
project (confirmed by filesystem search this phase). `keystore.properties`
is git-ignored and does not exist on disk. Both release artifacts built
this phase are genuinely unsigned, verified via `jarsigner`/`apksigner`
(not merely inferred from config absence) — see `android-signing-guide.md`.

## Independence / supply-chain

All dependencies resolve from `google()`/`mavenCentral()`/
`https://chaquo.com/maven` or from `aura-fullsuits/products`+
`commercial_runtime` at build time — nothing resolves from the original
`AuraEnterprise` repository (static grep + successful isolated build, see
`android-source-inventory.md`).

## What this report does NOT cover

- No dynamic/runtime security testing (no device to run a MobSF/Frida
  -style dynamic scan against).
- No penetration test.
- No third-party dependency CVE audit (dependency versions are pinned
  explicitly and match what a real build actually resolved, but their
  individual CVE status was not checked in this phase).
- No claim of compliance with any regulatory framework (HIPAA, GDPR, or
  otherwise) — see `clinic-android-privacy-boundary.md`'s own explicit
  non-claim.
