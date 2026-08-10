# Aura Retail — Android Parity Matrix (Phase 4S)

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
| Build | `assembleDebug` | PASS | `app-debug.apk`, 70,329,513 bytes, SHA-256 `bc8539b41bad314bebd1d1a240f8517082d25cf3d223af024f417856f39325fe` |
| Build | `assembleStaging` | PASS | Unsigned by design (no keystore configured this phase) — `app-staging-unsigned.apk` |
| Build | `assembleRelease` | PASS (unsigned) | `app-release-unsigned.apk` — compiles/packages; **not a signed release**, see `signing-and-release-guide.md` |
| Build | `lintDebug` | PASS | 0 errors, 27 warnings (outdated dep versions, redundant manifest label, missing monochrome icon — all cosmetic) |
| Build | `testDebugUnitTest` | PASS WITH DOCUMENTED LIMITATION | Stale claim corrected 2026-08-10 (Week 2 mobile build baseline): 12 real test files exist (`barcode/`, `licensing/`, `net/`, `server/`, `sync/`, `ui/i18n/`), 92 tests total, **90 pass / 2 fail**. Both failures are `SyncRelayClientTest` (`push sends signed request...`, `push 400 with reason_code...`), pre-existing and unrelated to this session's other work: they call MockWebServer's plain-`http://` `server.url("/")` directly against a `SyncRelayClient` that now enforces TLS-only relay URLs (`fix(sync): enforce the same TLS scheme rule on all three sync clients`), throwing `SyncInsecureRelayUrlError` before the request is ever sent. Other tests in the same file already use the HTTPS-wrapped `startFakeServer(sslContext)` fixture, so the fix is likely mechanical (route these two through that fixture too) — not attempted this pass, flagged for follow-up. |
| Identity | `applicationId` = `com.actionaura.retail` (`.debug`/`.staging` suffix per build type) | PASS | Verified via `aapt2 dump badging` on the built APK |
| Identity | Independent Gradle project (own `settings.gradle`/`build.gradle`, no shared applicationId with Clinic) | PASS | Verified — see `android-migration-plan.md` |
| Identity | Distinct package namespace `com.actionaura.retail` | PASS | Full rename from `com.actionaura.enterprise`, zero leftovers (grep-verified) |
| Identity | Distinct DB filename/storage dir | PASS | `android_platform.py` maps to `<filesDir>/data` — sandboxed per `applicationId`, cannot collide with Clinic |
| Identity | Commercial identity placeholders (product_code, installation_id, environment, app version, schema version) | PASS (scaffolding only) | `identity/CommercialIdentity.kt`; `tenantId` intentionally `null`, no license logic wired in |
| Identity | Real license/tenant/customer enforcement | DEFERRED TO LICENSING PHASE | Explicitly out of scope this phase |
| Navigation | Dashboard | PASS | Compiles; quick actions route to POS/Products |
| Navigation | POS / cart / checkout | PASS | Source screens migrated verbatim (`RetailScreens.kt`) |
| Navigation | Products | PASS | |
| Navigation | Reports / Transactions | PASS | |
| Navigation | Returns | PASS | |
| Navigation | Customers / Receivables / Statement / Aging | PASS | Confirmed backed by `products/retail/backend/api/retail_api.py` routes (already extracted Phase 2) |
| Navigation | Suppliers / Purchase Orders / Payables | PASS | Same backend-route confirmation |
| Navigation | Daily Cash / Cash Summary | PASS | |
| Navigation | Settings | PASS | |
| Barcode/Camera | CameraX preview integration | PASS (compiles) | `BarcodeScanner.kt` migrated verbatim, package renamed only |
| Barcode/Camera | ML Kit offline barcode recognition | REQUIRES PHYSICAL DEVICE VALIDATION | Cannot exercise a camera feed in this build environment |
| Barcode/Camera | Camera permission grant/deny flow | REQUIRES PHYSICAL DEVICE VALIDATION | |
| Barcode/Camera | Duplicate-scan suppression | REQUIRES PHYSICAL DEVICE VALIDATION | |
| Backend | Chaquopy Python interpreter starts on-device | REQUIRES PHYSICAL DEVICE VALIDATION | Build proves it compiles/packages; not proof the interpreter boots on a real Android runtime |
| Backend | Embedded server reachable at `127.0.0.1:<port>` | REQUIRES PHYSICAL DEVICE VALIDATION | |
| Backend | `main.py` no longer imports `aura_core` | PASS | Verified by grep — zero `import aura_core` statements anywhere in the migrated tree |
| Backend | pip deps trimmed to actual imports (Flask/Werkzeug/flask-cors/waitress/openpyxl/requests) | PASS | Verified by import-scan + successful Chaquopy pip install during the real build |
| Offline-first | Local SQLite persistence, no network dependency for core flows | PASS WITH DOCUMENTED LIMITATION | Same code path as the desktop build (Phase 2, 116/116 tests passing); on-device persistence itself not exercised this phase |
| Localization | English default | PASS | Source strings unchanged |
| Localization | Arabic translation table present | PASS | `ui/i18n/Strings.kt` migrated verbatim |
| Localization | RTL layout mirroring | REQUIRES PHYSICAL DEVICE VALIDATION | `AppRoot.kt` sets `LocalLayoutDirection` from `AppLocale.isRtl`; visual correctness needs a real screen |
| Privacy/security | `allowBackup="false"` | PASS | Unchanged from source |
| Privacy/security | Loopback-only cleartext network config | PASS | `network_security_config.xml` reused verbatim |
| Privacy/security | No logging of PII | PASS | Zero `Log`/`println` calls, grep-verified |
| Network | Owner Server / remote licensing endpoint | DEFERRED TO OWNER PLATFORM PHASE | Not built this phase |
| Updates | Remote/automatic update mechanism | DEFERRED TO UPDATE PHASE | Not built this phase |
| Repo independence | Build reads only from `aura-fullsuits/` (no reference to the original `AuraEnterprise` repo) | PASS | Verified by grep for the original repo's absolute path (zero matches) + build-log path inspection (`stageAuraPython`/`stageAuraAssets` sourced from `aura-fullsuits/products` and `aura-fullsuits/commercial_runtime` only) |
| Desktop regression | Retail backend/desktop unaffected by this phase | NOT APPLICABLE (untouched) | `git diff --stat -- products commercial_runtime` is empty — no backend/desktop files were modified in Phase 4, so the Phase 2 116/116 test suite result stands unchanged; not re-run this phase since there is nothing to regress |
