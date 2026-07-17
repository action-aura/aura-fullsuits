# Phase 4I — Clinic Android Privacy Boundary

Status: **PROVEN** for the static findings and fixes below. Does not
supersede the original `docs/privacy/clinic-android-data-boundary.md`
(prior migration's own privacy review, e.g. confirming the `CAMERA`
permission was correctly dropped from Clinic's manifest) — this document
covers what changed and what was newly found in this corrective phase.

## What was audited this phase

Per the Phase 4I checklist: Logcat, Retrofit logging, embedded Flask
logging, exception handlers, Compose debug output, intent extras, deep
links, exported activities/services/receivers, content providers,
FileProvider, SharedPreferences, clipboard, screenshots, recent-app
preview, notifications, temporary files, external storage, Android device
backup, crash-report placeholders, test data, database errors, file
upload paths.

## Findings

### 1. Real finding, fixed this phase: PII in best-effort accounting-sync logging

`products/clinic/backend/api/clinic_api.py` (staged into the Clinic APK's
embedded Python) had two `print(f'... {e}')` statements inside
best-effort try/except blocks (lab-expense accounting sync, line ~654;
prescription-invoice accounting sync, line ~765). The invoice-sync block
specifically looks up the real patient name into a local variable
(`_pname`) before the try/except that could print `{e}` — if the wrapped
`sub_create()` call ever raised an exception whose message embedded a
bound value (a real, if uncommon, class of Python/SQLite exception
behavior, and the exact pattern already fixed once before in
`create_patient()`'s own primary handler per its docstring), that name
could reach Logcat via Chaquopy's stdout-to-Logcat forwarding.

**Fixed**: both replaced with
`logging.getLogger('aura.clinic').warning('...: %s', type(e).__name__)`
— logs only the exception *type*, never its message or any interpolated
request value. Verified: `products/clinic/tests/clinic_workflow_test.py`
(29/29) and the full 279-test backend suite still pass unchanged after
this edit.

### 2. Re-confirmed clean (unchanged from prior migration, re-verified this phase)

- Zero `android.util.Log`/`println`/`System.out` in Clinic's Kotlin
  source — now regression-guarded by `PrivacyLoggingGuardTest.kt`
  (`no_android_log_calls_in_kotlin_source`,
  `no_println_or_system_out_in_kotlin_source`).
- No `HttpLoggingInterceptor` wired into `ApiClient.kt`'s `OkHttpClient` —
  also regression-guarded (`no_http_body_logging_interceptor_configured`).
- `android:allowBackup="false"` — Android's automatic device/cloud backup
  never captures the Clinic database or any patient attachment.
- No `FileProvider`, no exported components beyond the required
  `MainActivity`, no deep links, no notifications, no clipboard usage
  found anywhere in Clinic's Kotlin source.
- No `CAMERA` permission (Clinic has no barcode feature) — confirmed
  absent from the manifest, matching the prior migration's own finding.
- Cookie storage (`SharedPreferences`, `MODE_PRIVATE`) is app-sandboxed,
  standard Android isolation, no plaintext file outside the app's own
  private storage.

### 3. Not evaluated this phase (requires a real device)

- Actual Logcat output during a live run (no device — see
  `android-device-testing-guide.md`).
- Recent-app-preview / screenshot behavior. **`FLAG_SECURE` is not set**
  in `MainActivity` (grep-confirmed absent) — this means the Android task
  switcher can show a screenshot/thumbnail of the last-viewed screen,
  which could include patient names on a shared or unlocked device. This
  is **not fixed in this phase** — recorded here explicitly as a
  documented gap, per the addendum's own instruction ("if not implemented,
  document it as a later privacy-hardening decision rather than silently
  claiming screenshot protection"), not silently claimed as protected.
- Crash-report placeholders: no crash-reporting SDK (Firebase Crashlytics,
  Sentry, etc.) is integrated in either app — there is nothing to audit
  for over-collection, but this also means no crash telemetry exists at
  all (consistent with "do not activate telemetry" being out of scope).

## Explicit non-claims

This document does not claim HIPAA, GDPR, or any other regulatory
compliance. It documents what was found and fixed in the source code
available in this environment, not a legal or clinical-data-handling
certification.
