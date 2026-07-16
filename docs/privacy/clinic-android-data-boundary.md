# Aura Clinic (Android) — Data Boundary & Privacy Review

Phase 4F of the Android migration. Scope: `android/aura-clinic/`. This is a review
of the app as migrated, not a claim of legal/regulatory compliance (HIPAA, GDPR,
etc.) — that determination is out of scope for this phase.

## What data this app handles

Aura Clinic's native UI (Compose) talks only to its own embedded, per-device Flask
server over `http://127.0.0.1:<port>` (see `network_security_config.xml` — loopback
cleartext only, everything else HTTPS-only). Patient names, appointment details,
billing/invoice data, and prescriptions are stored **only** in the embedded SQLite
database inside the app's private storage
(`<filesDir>/data/database/`, via `android_platform.py` → `AURA_APP_DATA`). No patient
data leaves the device as part of this phase — there is no cloud sync, no analytics
SDK, and no crash-reporting SDK wired into this build.

## Findings

| Area | Finding | Status |
|---|---|---|
| Logging | Zero `android.util.Log`, `println`, or `printStackTrace` calls anywhere in `app/src/main/java` (verified by grep across the whole source tree). No PII can leak into logcat because nothing is logged. | PASS |
| Network logging | No OkHttp `HttpLoggingInterceptor` (or any interceptor) is registered in `ApiClient.kt` — request/response bodies (which can contain patient data) are never written anywhere, debug or release. | PASS |
| Android backup | `AndroidManifest.xml` sets `android:allowBackup="false"` — the OS will not include this app's private data (including the patient database) in Auto Backup or `adb backup`. | PASS |
| Exported components | Only `.MainActivity` is exported (`android:exported="true"`), and only because it is the launcher activity (`MAIN`/`LAUNCHER` intent filter) — required by Android for a launchable app. No other activities, services, receivers, or content providers are declared, exported or otherwise. | PASS |
| Camera/barcode permission | `CAMERA` permission and the `android.hardware.camera.any` feature declaration were present in the copied (shared) source manifest but are unused by Aura Clinic (no camera code anywhere in `app/src/main/java`). Removed from `android/aura-clinic/app/src/main/AndroidManifest.xml` in this phase — the clinic APK now requests only `INTERNET`. | FIXED |
| Cleartext network traffic | `network_security_config.xml` restricts cleartext to `127.0.0.1`/`localhost` only; all other traffic must be HTTPS. Unchanged from source, reused verbatim. | PASS |
| Server bind address | The embedded Flask server binds to `127.0.0.1` only (`main.py` `HOST = '127.0.0.1'`, passed through to `waitress.serve`) — not reachable from the local network. | PASS |

## Data flow summary

```
Compose UI  --Retrofit/OkHttp-->  127.0.0.1:<port>  --Flask-->  SQLite (app-private storage)
     |                                                                 ^
     +-- no analytics, no crash reporter, no external network calls --+
```

## Known limitations of this review

- This is a static source review, not a runtime traffic capture. No physical device
  or emulator was used to sniff actual network traffic in this phase (see
  `docs/android/clinic-android-parity-matrix.md` for what requires physical-device
  validation).
- Chaquopy's embedded Python runtime and its pip dependencies (Flask, Werkzeug,
  flask-cors, waitress) were not independently re-audited for their own internal
  logging in this phase; they are widely-used, unmodified upstream packages pinned
  to the same versions already used by the Windows desktop build (Phase 3), which
  passed its own regression suite.
- No formal threat model or penetration test was performed. This document is a code
  read, not a security assessment.
