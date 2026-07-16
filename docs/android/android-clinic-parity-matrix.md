# Aura Clinic — Android Parity Matrix (Phase 4S)

Legend: **PASS** (verified this phase) · **PASS WITH DOCUMENTED LIMITATION** ·
**FAIL** · **NOT PRESENT IN SOURCE** · **NOT APPLICABLE** ·
**REQUIRES PHYSICAL DEVICE VALIDATION** · **DEFERRED TO LICENSING PHASE** ·
**DEFERRED TO OWNER PLATFORM PHASE** · **DEFERRED TO UPDATE PHASE**

"Verified this phase" means: present in the migrated source, compiles, and packages
into a real APK via a real Gradle build in this session. It does NOT mean exercised
on a device — see `device-testing-guide.md` for what still needs a physical device.

| Area | Item | Status | Notes |
|---|---|---|---|
| Build | Project compiles (`compileDebugKotlin`) | PASS | Real build, this phase |
| Build | `assembleDebug` | PASS | `app-debug.apk`, 55,881,867 bytes, SHA-256 `bd8f9496b2bfa3ea08fb7ab92c87b7cca7b24f3894a56831d1372620b4f3d067` |
| Build | `assembleStaging` | PASS | Unsigned by design (no keystore configured this phase) — `app-staging-unsigned.apk` |
| Build | `assembleRelease` | PASS (unsigned) | `app-release-unsigned.apk` — compiles/packages; **not a signed release**, see `signing-and-release-guide.md` |
| Build | `lintDebug` | PASS | 0 errors, 23 warnings (outdated dep versions, redundant manifest label, missing monochrome icon — all cosmetic) |
| Build | `testDebugUnitTest` | NOT PRESENT IN SOURCE | 0 test files exist in the source Android project; task reports `NO-SOURCE`, not a failure |
| Identity | `applicationId` = `com.actionaura.clinic` (`.debug`/`.staging` suffix per build type) | PASS | Verified via `aapt2 dump badging` on the built APK |
| Identity | Independent Gradle project (own `settings.gradle`/`build.gradle`, no shared applicationId with Retail) | PASS | Verified — see `android-migration-plan.md` |
| Identity | Distinct package namespace `com.actionaura.clinic` | PASS | Full rename from `com.actionaura.enterprise`, zero leftovers (grep-verified) |
| Identity | Distinct DB filename/storage dir | PASS | `android_platform.py` maps to `<filesDir>/data` — sandboxed per `applicationId`, cannot collide with Retail |
| Identity | Commercial identity placeholders (product_code, installation_id, environment, app version, schema version) | PASS (scaffolding only) | `identity/CommercialIdentity.kt`; `tenantId` intentionally `null`, no license logic wired in |
| Identity | Real license/tenant/customer enforcement | DEFERRED TO LICENSING PHASE | Explicitly out of scope this phase |
| Navigation | Dashboard | PASS | Compiles; quick actions route to Patients/Appointments/Billing |
| Navigation | Patients (list) | PASS | |
| Navigation | Patient detail (`patient/{id}`) | PASS | Back-navigation `isDetail` logic preserved in `AppRoot.kt` |
| Navigation | Appointments | PASS | |
| Navigation | Billing | PASS | |
| Navigation | Doctors (drawer) | PASS | |
| Navigation | Lab Expenses (drawer) | PASS | |
| Navigation | Prescriptions (drawer) | PASS | |
| Navigation | Settings / roles | PASS | Role-gated UI logic migrated verbatim from source |
| Onboarding | First-run admin-account setup | PASS | `SetupScreen.kt` — clinic-specific label text preserved |
| Backend | Chaquopy Python interpreter starts on-device | REQUIRES PHYSICAL DEVICE VALIDATION | Build proves it compiles/packages; not proof the interpreter boots on a real Android runtime |
| Backend | Embedded server reachable at `127.0.0.1:<port>` | REQUIRES PHYSICAL DEVICE VALIDATION | |
| Backend | `main.py` no longer imports `aura_core` | PASS | Verified by grep — zero `import aura_core` statements anywhere in the migrated tree |
| Backend | pip deps trimmed to actual imports (Flask/Werkzeug/flask-cors/waitress) | PASS | Verified by import-scan + successful Chaquopy pip install during the real build; openpyxl/requests correctly excluded (unused by clinic backend) |
| Offline-first | Local SQLite persistence, no network dependency for core flows | PASS WITH DOCUMENTED LIMITATION | Same code path as the desktop build (Phase 3, 95/95 tests passing); on-device persistence itself not exercised this phase |
| Localization | English default | PASS | Source strings unchanged |
| Localization | Arabic translation table present | PASS | `ui/i18n/Strings.kt` migrated verbatim (includes unused barcode-related entries — dead strings, harmless, no barcode UI references them; see `android-source-inventory.md`) |
| Localization | RTL layout mirroring | REQUIRES PHYSICAL DEVICE VALIDATION | `AppRoot.kt` sets `LocalLayoutDirection` from `AppLocale.isRtl`; visual correctness needs a real screen |
| Camera/barcode | Any camera or barcode feature | NOT PRESENT IN SOURCE | Clinic never had this feature (retail-only); `CAMERA` permission removed from clinic's manifest this phase as a dead permission (see `docs/privacy/clinic-android-data-boundary.md`) |
| Privacy/security | `allowBackup="false"` | PASS | Unchanged from source |
| Privacy/security | Loopback-only cleartext network config | PASS | `network_security_config.xml` reused verbatim |
| Privacy/security | No logging of PII | PASS | Zero `Log`/`println` calls, grep-verified; no `HttpLoggingInterceptor` in `ApiClient.kt` |
| Privacy/security | No unused/unnecessary permissions | PASS (fixed this phase) | `CAMERA` + camera feature declaration removed from manifest |
| Network | Owner Server / remote licensing endpoint | DEFERRED TO OWNER PLATFORM PHASE | Not built this phase |
| Updates | Remote/automatic update mechanism | DEFERRED TO UPDATE PHASE | Not built this phase |
| Repo independence | Build reads only from `aura-fullsuits/` (no reference to the original `AuraEnterprise` repo) | PASS | Verified by grep for the original repo's absolute path (zero matches) + build-log path inspection (`stageAuraPython`/`stageAuraAssets` sourced from `aura-fullsuits/products` and `aura-fullsuits/commercial_runtime` only) |
| Desktop regression | Clinic backend/desktop unaffected by this phase | NOT APPLICABLE (untouched) | `git diff --stat -- products commercial_runtime` is empty — no backend/desktop files were modified in Phase 4, so the Phase 3 95/95 test suite result stands unchanged; not re-run this phase since there is nothing to regress |
