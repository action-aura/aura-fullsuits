# Phase 4 (Corrective Pass) — Android Migration Handover

**Base**: tag `windows-launcher-watchdog-corrected`. **Scope**:
`aura-fullsuits` only; `AuraEnterprise` never read from or written to.

## 1. Executive summary

Android source for both products was **already fully migrated** into
`aura-fullsuits/android/{aura-retail,aura-clinic}/` in an earlier session
(tag `android-migration-phase4-complete`, 2026-07-16, predating Wave 0 and
Phase 3.7). This phase is a **corrective pass**, not a re-migration: it
found and fixed two classes of real defect that the earlier migration
carried over from source (the Phase-3.7-class readiness-check bug, and a
client-side financial-display bug), closed two idempotency gaps, fixed a
PII-log-leak in shared backend code, added the first Android test
infrastructure for either product (0 → 46 tests), and ran real Gradle
builds through `assembleRelease`/`bundleRelease` for both. No
device/emulator was available in this environment, so all runtime/UI
claims are honestly marked `NOT TESTED`.

## 2. Original source locations

Both apps' Android source originates from `AuraEnterprise/android/`
(single Gradle project, two product flavors) — documented exhaustively in
`docs/android/android-source-inventory.md` from the original migration.
Not re-read from that repo this phase (read-only, never touched).

## 3. Migrated destinations

`aura-fullsuits/android/aura-retail/` and
`aura-fullsuits/android/aura-clinic/` — already existed at phase start;
this phase modified files within them (see §33).

## 4. Retail architecture

Independent Gradle project. Native Jetpack Compose UI (no WebView) talking
to an embedded Chaquopy-hosted Flask server (`products/retail/backend/`,
staged at build time) over `http://127.0.0.1:<port>/`. CameraX + ML Kit
for barcode scanning. Retrofit/Gson for the HTTP client, SQLite for local
persistence (server-side, inside the embedded backend).

## 5. Clinic architecture

Identical shape, `products/clinic/backend/`, no CameraX/ML Kit (no barcode
feature).

## 6. Gradle and dependency versions

Gradle 8.9, AGP 8.5.2, Kotlin 2.0.21, Compose compiler 2.0.21, Compose BOM
2024.09.03, Chaquopy 16.0.0, compileSdk/targetSdk 34, minSdk 26 — identical
across both products. Full list: `android-dependency-map.md`.

## 7. Chaquopy architecture

Each product stages its own backend + `commercial_runtime` from
`aura-fullsuits` into the Chaquopy Python source tree at build time
(`stageAuraPython` Gradle task, computing `suiteRoot` two directories up
from the Android project root — never `AuraEnterprise`). `main.py`
(product-specific, small, not shared) exposes `start_server()`/
`wait_until_ready()`/`server_port()` to Kotlin via `ServerBootstrap.kt`.
**Fixed this phase**: `wait_until_ready()` now polls `/api/health` with a
monotonic clock, not the bare `/` path that could never succeed.

## 8. Financial contract integration

`docs/architecture/financial-authority-contracts.md` is the frozen source
of truth. Both products' Kotlin request/response models were audited
against it and corrected — see `cross-platform-financial-contract-report.md`
for the full worked-example proof (Retail: subtotal=100/discount=20/
tax=10%→total=88; Clinic: the $100-invoice payment table).

## 9. Android zero-tax defect result

**Fixed at the remaining client-display layer.** The backend was already
immune (Wave 0). The Android client no longer displays a locally
-computed, always-untaxed total as if it were the sale's authoritative
result — `SaleResult` now carries the full server contract and
`RetailScreens.kt`'s checkout path reads `r.data.total`. Proven by 3
dedicated unit tests; not device-verified.

## 10. Clinic payment contract result

**Fixed.** `CreatePaymentRequest` now always carries a real
`idempotency_key` (previously absent). Response is now fully typed and
checked. One known, documented limitation: Retrofit's `HttpException`
path for real 400 rejections doesn't route through the intended
`r.status`-check branch (message-accuracy gap only, not a financial
-authority gap — see `android-residual-risk-register.md`).

## 11. Database isolation

Unchanged from the original migration, re-confirmed by reading
`android_platform.py`/`AssetInstaller.java`: each product's backend
resolves its own private `filesDir`-rooted `AURA_APP_DATA`, entirely
separate SQLite files, no shared database file or path between the two
apps or with either desktop product.

## 12. Backup integration

Backend endpoints (`/api/backup/create`/`/api/backup/restore`, Wave 0/
Phase 3.7) are staged into both apps' embedded backends and thus
technically reachable via a raw HTTP call, but **no Android UI screen
exists in either product to trigger one**. Not present in source; not
added this phase (would be a new feature).

## 13. Privacy controls

Clinic: two backend PII-log-leak-capable `print()` statements found and
fixed this phase. Zero `Log`/`println`/`HttpLoggingInterceptor` in Kotlin
source (both products), now regression-guarded by unit tests.
`allowBackup=false`, no FileProvider, single exported component, both
apps. `FLAG_SECURE` not set (documented gap, not silently claimed).
Full detail: `clinic-android-privacy-boundary.md`, `android-security-report.md`.

## 14. Build results

Both products: `clean`, `lintDebug` (0 errors, 27/23 warnings),
`assembleDebug`, `assembleRelease`, `bundleRelease` all **BUILD
SUCCESSFUL**, real commands, real output. Full logs/timing:
`retail-build-report.md`, `clinic-build-report.md`.

## 15. Test results

**Retail: 29/29 Kotlin unit/contract tests pass.** **Clinic: 17/17 pass.**
**Backend: 279/279 Python tests pass** (rerun required and performed —
`clinic_api.py` was modified). Isolated-per-file strategy per the standing
AUDIT-010 decision.

## 16. Device/emulator results

**NOT TESTED, both products.** No emulator package installed, no physical
device connected (`adb devices` empty). Every device-dependent item in
`retail-device-test-report.md`/`clinic-device-test-report.md` is marked
`NOT TESTED`, `BUILD ONLY`, or `SOURCE REVIEW ONLY` — never falsely
claimed as tested.

## 17. Camera result

**NOT TESTED.** No physical device with a camera was available. Barcode
debounce and product-lookup *logic* (extracted to pure functions) is
unit-tested; the actual CameraX/ML Kit camera pipeline was never
exercised.

## 18. APK/AAB artifacts

| Product | File | SHA-256 |
|---|---|---|
| Retail | `app-debug.apk` | `c19acc6b025b27699edd89f362e10ec9cd05da2e4e56ec1f339567bb061d2ff1` |
| Retail | `app-release-unsigned.apk` | `d79539232d1e7f6fee9dce8cd4fa40d1e97ba5dc1808c7ac5a090ebb62628604` |
| Retail | `app-release.aab` (unsigned) | `c45621f4e4c574e5fc4bc1976fe96e4107630a1006c54434c0d3f745cf549758` |
| Clinic | `app-debug.apk` | `ecce2ec42f054deeb2b260124a0d150f413c05785b685dd2858b2d29d23072d7` |
| Clinic | `app-release-unsigned.apk` | `c497723e2ff8069c7c424b3029373e27b49bc9ac47ad977730e7c8bd0b3dc055` |
| Clinic | `app-release.aab` (unsigned) | `96245e69c749f25c99f6d770d2e322fc05f1c4efdd818094478f4dc328a00707` |

Full paths/sizes: `retail-build-report.md`/`clinic-build-report.md`. Not
committed to git (gitignored by policy).

## 19. Signing status

**Unsigned, both products, verified via `jarsigner -verify`/`apksigner
verify`.** No production keystore exists. See `android-signing-guide.md`.

## 20. Independence verification

Static grep for `AuraEnterprise` in both Android trees returns only
explanatory comments (zero live path references); real builds succeeded
staging from `aura-fullsuits/products`+`commercial_runtime` exclusively.
No dev-machine absolute paths, no private IPs, no hardcoded credentials
found.

## 21. Remaining blockers (for a real paid pilot)

1. No device/emulator validation of any kind exists.
2. No production signing key.
3. No installer/distribution channel.
4. Clinic payment-rejection UX message-accuracy gap (§10).
5. No client-side role-gated navigation (Clinic).
6. No backup/restore UI in either app.
7. `FLAG_SECURE` not set (Clinic).
8. Windows release gates (unsigned builds, AUDIT-022/023) remain open —
   unaffected by this phase, still blocking per Wave 0's own handover.

## 22. Wave 1 recommendations

Real device/emulator install + the full 27-item (Retail) / 25-item
(Clinic) smoke test in `android-device-testing-guide.md`; fix the
Retrofit-`HttpException`-vs-response-body handling project-wide (a real UX
pass, not just Clinic payments); decide whether Clinic needs client-side
role-gated navigation or backend-only enforcement is acceptable long-term;
add a backup/restore UI screen to both apps; set `FLAG_SECURE` on Clinic
if shared-device screen privacy is a real deployment scenario; generate
and secure a production signing key when ready to distribute.

---

## 23-30. (continued in the Final Response, see below)

## 33. Files created and modified

**Created**: `android/aura-retail/app/src/main/java/com/actionaura/retail/barcode/{BarcodeDebounce,ProductLookup}.kt`;
`android/{aura-retail,aura-clinic}/app/src/test/**` (7 test files, 46
tests); `docs/android/phase4/` (this directory, 20 files).

**Modified**: `android/{aura-retail,aura-clinic}/app/src/main/python/main.py`;
`android/aura-retail/app/src/main/java/com/actionaura/retail/net/{Models,AuraApi}.kt`;
`android/aura-retail/app/src/main/java/com/actionaura/retail/ui/screens/{RetailScreens,RetailExtraScreens,BarcodeScanner}.kt`;
`android/aura-clinic/app/src/main/java/com/actionaura/clinic/net/{Models,AuraApi}.kt`;
`android/aura-clinic/app/src/main/java/com/actionaura/clinic/ui/screens/BillingScreen.kt`;
`android/{aura-retail,aura-clinic}/app/build.gradle` (test deps);
`products/clinic/backend/api/clinic_api.py`.

## 34. Exact commits

`db577ec` fix: correct Android embedded backend readiness check
`6092b8e` fix: integrate retail Android financial-authority and returns contracts
`bdc42fd` fix: integrate clinic Android payment contract; harden privacy logging
`168f088` test: add retail and clinic Android contract tests
(plus the docs commit that follows this handover, and the final tag)

## 35. Checkpoint tag

`android-migration-phase4-complete` **already exists** on an earlier
commit (`6e8418b`, 2026-07-16, the original migration). Per git safety
policy this phase does not force-move or overwrite that tag. This
corrective pass is tagged **`android-migration-phase4-corrective-complete`**
instead — see the Final Response for the exact commit it points to.

## 36. Stop condition

Per explicit instruction, this phase stops here. No Owner Control Center,
licensing, subscription enforcement, VPS, telemetry, update distribution,
or Aura Core integration work was started.
