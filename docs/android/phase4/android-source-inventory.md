# Phase 4A — Android Source Inventory

Status: **PROVEN**. This supersedes nothing in `docs/android/android-source-inventory.md`
(the original Phase 4 migration's inventory of the *original* AuraEnterprise
Android source) — that document remains the authoritative record of what was
migrated from where. This file inventories the **current state of the
already-migrated Android projects inside aura-fullsuits**, as found and
corrected in this phase (3.7/4 corrective pass, 2026-07-17).

## Current project shape (already migrated, confirmed present)

Two independent Gradle projects existed on disk before this phase began
(created in an earlier session, tagged `android-migration-phase4-complete`
on commit `6e8418b`, chronologically *before* Wave 0's backend fixes and
Phase 3.7's launcher fix):

- `aura-fullsuits/android/aura-retail/` — namespace/applicationId
  `com.actionaura.retail`, `.gradle`/`.kotlin` build caches already present
  (a real Gradle build had run at least once before this phase).
- `aura-fullsuits/android/aura-clinic/` — namespace/applicationId
  `com.actionaura.clinic`, same evidence of a prior real build.

## What this phase found on inspection (before any fix)

- **`app/src/main/python/main.py`** (both products) — `wait_until_ready()`
  polled the bare `http://127.0.0.1:<port>/` path. Neither `app.py` has
  ever served `/`. This is the exact Phase 3.7 Windows-launcher root cause,
  present here independently because this Android code was migrated
  *before* that correction existed. **Confirmed structurally broken on
  every launch** (source-content proof, not speculation) — see
  `cross-platform-financial-contract-report.md` for the fix.
- **`RetailScreens.kt`'s checkout path** — computed `total` locally from
  `sell_price * qty` only (no tax, no discount), sent it to the server as
  `subtotal`/`total` (fields the Wave-0-corrected backend already ignores),
  and — critically — displayed that same **local, untaxed** value as the
  "authoritative" sale result (`successTotal = saleTotal`), because
  `SaleResult` (the response model) didn't even have a `total` field to
  read the server's real answer from. This is the AUDIT-002 defect,
  independently confirmed still present on the Android **client display**
  side even though the backend itself was already immune (Wave 0).
- **`ReturnItemReq`/`CreateReturnRequest`** — no `idempotency_key` field
  existed at all; a double-tap on "Process refund" had zero client-side
  duplicate-submission protection.
- **`CreatePaymentRequest`** (Clinic) — same gap: no `idempotency_key`;
  the response was also completely unread (`r.status` was never checked),
  so a rejected payment (overpayment, zero/negative amount) failed
  silently with no user-facing message.
- **`products/clinic/backend/api/clinic_api.py`** (shared backend, staged
  into both — well, into Clinic's build only, but this file is reviewed
  here since Phase 4I's privacy audit covers it) — two `print(f'... {e}')`
  statements in best-effort accounting-sync blocks could leak a patient
  name or lab/test name into Logcat via Chaquopy's stdout forwarding, if
  the wrapped operation's exception message ever echoed a bound value.

Every item above is fixed in this phase — see
`cross-platform-financial-contract-report.md`,
`clinic-android-privacy-boundary.md`, and the git commits in
`PHASE4-ANDROID-MIGRATION-HANDOVER.md`.

## What this phase did NOT find (already correct)

- `AndroidManifest.xml` (both): `allowBackup="false"`, single exported
  component (`MainActivity`, required for the launcher), no FileProvider,
  no other exported services/receivers/providers.
- `network_security_config.xml` (both): loopback-only cleartext exception,
  `base-config cleartextTrafficPermitted="false"` for everything else.
- `ApiClient.kt` (both): no `HttpLoggingInterceptor`, no network-body
  logging of any kind, in any build type.
- Zero `android.util.Log`/`println`/`System.out` calls anywhere in either
  app's Kotlin source (grep-verified, now also regression-guarded by
  `PrivacyLoggingGuardTest.kt`).
- `release`/`staging` build types: `debuggable false`, no signing config
  applied unless `keystore.properties` exists on disk (it doesn't; no
  keystore/secrets were ever committed).
- Independence: `suiteRoot = rootProject.projectDir.parentFile.parentFile`
  resolves to `aura-fullsuits`, never `AuraEnterprise` — confirmed both by
  static grep (zero live path references, only explanatory comments
  mentioning the name) and by the fact that a real Gradle build
  successfully staged `products/`/`commercial_runtime/` from that
  computed path.

## Distinguishing shared vs. product-specific vs. generated

- **Genuinely shared mobile code**: none literally shared between the two
  Gradle projects (they are fully independent projects with duplicated,
  not linked, source for common patterns like `ApiClient.kt`'s cookie-jar
  logic) — this is unchanged from the original migration's design choice
  (documented in `docs/android/android-source-inventory.md`) and was not
  revisited in this phase (no shared-platform-module work was in scope).
- **Embedded product backend code**: staged at build time via each
  project's own `stageAuraPython` Gradle task from
  `aura-fullsuits/products/{retail,clinic}/backend` and
  `aura-fullsuits/commercial_runtime` — not committed as static files
  inside `android/`, so this phase's backend fixes (the `/api/health`
  route existed already from Phase 3.7; the two `print()` fixes this
  phase) are picked up automatically on the next Android build with no
  Android-side change needed for those two files.
- **Generated build artifacts**: `.gradle/`, `.kotlin/`, `app/build/` —
  present on disk (evidence of prior builds) but not committed (gitignored)
  and not treated as source by this inventory.
