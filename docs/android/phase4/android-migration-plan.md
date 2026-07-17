# Phase 4 — Android Migration/Correction Plan (as executed)

Status: **PROVEN** (this is a record of what was actually done, not a
forward-looking plan). The bulk of the migration itself (source extraction,
Gradle project authoring, package rename, flavor-splitting) was completed
in an earlier session (tag `android-migration-phase4-complete`,
2026-07-16) — see `docs/android/android-migration-plan.md` for that
original plan. This document covers **only what this corrective phase
(3.7/4, 2026-07-17) actually changed**, in execution order.

## Step 1 — Read authoritative Wave 0 inputs

Read (this session, already loaded from prior phases of the same
conversation): `WAVE0-CORRECTIVE-HANDOVER.md`,
`cross-platform-financial-validation.md`, `wave0-test-report.md`,
`wave0-residual-risk-register.md`, `docs/architecture/financial-authority-contracts.md`.

## Step 2 — Discovery: what state is the already-migrated Android code in?

Read `docs/android/android-source-inventory.md` (original), then read the
actual current source files (`main.py`, `RetailScreens.kt`,
`RetailExtraScreens.kt`, `Models.kt`, `AuraApi.kt`, `BillingScreen.kt`) to
find concrete defects rather than assuming any. Found: the readiness-check
bug (identical to Phase 3.7's Windows finding), the Android-side financial
-display bug (client-computed total shown instead of the server's
authoritative response), missing idempotency keys on both Retail returns
and Clinic payments, and two PII-log-leak-capable `print()` statements in
`clinic_api.py`. See `android-source-inventory.md` (this directory) for
the full list with evidence.

## Step 3 — Fix readiness (Phase 4K)

`main.py` (both products): `wait_until_ready()` now polls
`GET /api/health` (already existed as a route from Phase 3.7's backend
fix) instead of `/`, uses `time.monotonic()`, and checks for an explicit
`200` status rather than "any non-exception response."

## Step 4 — Fix Retail financial-authority integration (Phase 4D)

`Models.kt`: `SaleItemReq` trimmed to `product_id`/`quantity`/
`discount_pct` (no `unit_price`); `CreateSaleRequest` trimmed to remove
`subtotal`/`discount_amount`/`tax_amount`/`total`; `SaleResult` expanded to
carry every field in the authoritative contract
(`subtotal`/`discount_amount`/`tax_amount`/`total`/`change`/
`calculation_version`/etc).
`RetailScreens.kt`: the checkout success path now displays
`r.data?.total` (the server's response), never the local pre-tax preview.

## Step 5 — Fix Retail returns integration (Phase 4E)

`Models.kt`: `ReturnItemReq` trimmed to `product_id`/`quantity`;
`CreateReturnRequest` gained a required `idempotency_key`; a new
`CreateReturnResponse`/`ReturnResult` pair added mirroring the
authoritative refund contract. `RetailExtraScreens.kt`'s submission call
site updated to match (generates a real `UUID` per attempt).

## Step 6 — Fix Clinic payment integration (Phase 4H)

`Models.kt`: `CreatePaymentRequest` gained a required `idempotency_key`; a
new `CreatePaymentResponse`/`ClinicPaymentResult` pair added (named
distinctly from a pre-existing, unrelated `PaymentResult` used by Retail
-derived AR endpoints Clinic's `AuraApi.kt` still declares but doesn't use
for billing). `BillingScreen.kt`'s `PaymentSheet` now checks `r.status`
and surfaces a rejection message instead of silently swallowing it.

## Step 7 — Harden Clinic privacy (Phase 4I)

`products/clinic/backend/api/clinic_api.py`: two `print(f'... {e}')`
statements (in the best-effort Accounting-sync blocks for lab expenses and
prescription-generated invoices) replaced with `logging.getLogger(...).warning(..., type(e).__name__)`
— never interpolating the raw exception, which could otherwise embed a
patient name or lab/test name via a SQL-bind error message forwarded to
Logcat by Chaquopy's stdout capture. See `clinic-android-privacy-boundary.md`.

## Step 8 — Extract barcode logic for testability (Phase 4F)

Two pure functions extracted verbatim from inline Compose lambdas (not
reimplemented — the exact same logic, just addressable): `isDuplicateScan()`
(`barcode/BarcodeDebounce.kt`) and `findProductByCode()`
(`barcode/ProductLookup.kt`). `BarcodeScanner.kt`/`RetailScreens.kt` call
sites updated to use them; behavior is unchanged.

## Step 9 — Add test infrastructure (Phase 4Q)

`app/build.gradle` (both): added `testImplementation` deps. New
`app/src/test/` source sets: 29 tests (Retail), 17 tests (Clinic) — all
passing. See `PHASE4-ANDROID-MIGRATION-HANDOVER.md` for the full breakdown.

## Step 10 — Real build validation (Phase 4R)

`clean`, `lintDebug`, `assembleDebug`, `assembleRelease`, `bundleRelease`
actually run for both products. See `retail-build-report.md` /
`clinic-build-report.md`.

## Step 11 — Independence verification (Phase 4U)

Static grep + successful build with `AuraEnterprise` excluded by
construction. See `android-source-inventory.md`'s independence section.

## Step 12 — Backend regression gate (Phase 4T)

`products/clinic/backend/api/clinic_api.py` was modified (Step 7) →
279-test rerun was required and performed: 279/279 pass, isolated-per-file.

## What was explicitly NOT done (in scope discipline)

No new UI screens, no discount-entry UI (doesn't exist in source), no
client-side role-gated navigation (doesn't exist in source — backend RBAC
is the real enforcement boundary), no device/emulator runtime testing (no
device or emulator available in this environment — see
`android-device-testing-guide.md`), no Owner/licensing/VPS/telemetry/update
work, no Android migration of anything beyond what was already migrated in
the prior session.
