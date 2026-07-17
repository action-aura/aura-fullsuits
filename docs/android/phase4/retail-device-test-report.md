# Aura Retail Android — Device/Emulator Test Report

Status: **NOT TESTED** for all device-dependent steps (no device/emulator
available — see `android-device-testing-guide.md`). Each item below uses
one of: `TESTED ON PHYSICAL DEVICE`, `TESTED ON EMULATOR`, `BUILD ONLY`,
`SOURCE REVIEW ONLY`, `NOT TESTED`. No item is marked tested unless it
actually was.

| # | Step | Status | Note |
|---|---|---|---|
| 1 | Install APK | NOT TESTED | Real debug APK exists (`retail-build-report.md`) and would install via `adb install`, never attempted |
| 2 | Launch | NOT TESTED | |
| 3 | Start embedded backend | SOURCE REVIEW ONLY | `ServerBootstrap.start()` calls unchanged `main.start_server()`; logic traced, not executed on-device |
| 4 | Reach `/api/health` | SOURCE REVIEW ONLY | Route confirmed present in the exact staged `app.py` this build packaged (Phase 3.7); `wait_until_ready()` confirmed to target it (this phase's fix); never actually polled on a running device |
| 5 | Initialize a clean database | NOT TESTED | Backend logic proven correct by `products/retail/tests/retail_onboarding_wave0_test.py` (dev-mode, not Android runtime) |
| 6 | Complete onboarding | NOT TESTED | Same backend logic, same caveat |
| 7 | Log in | NOT TESTED | |
| 8 | Add a product | NOT TESTED | |
| 9 | Configure/verify tax | NOT TESTED | |
| 10 | Add product to cart | NOT TESTED | |
| 11 | Apply discount | NOT TESTED (also: NOT PRESENT IN SOURCE) | No discount-entry UI exists in the cart (confirmed by reading `RetailScreens.kt`) — `discount_pct` defaults to 0 always; not added this phase (would be a new feature) |
| 12 | Complete sale | NOT TESTED | |
| 13 | Verify authoritative response | UNIT TEST VERIFIED | `SaleContractTest.kt` proves the response model deserializes and the checkout code path (source-reviewed) reads `r.data.total`, not a local calc |
| 14 | Verify stored totals | SOURCE REVIEW ONLY | `Sale` model's fields are populated straight from the backend's `GET /sales/recent`, which is DB-authoritative by construction (Wave 0) |
| 15 | Restart | NOT TESTED | |
| 16 | Verify sale persistence | NOT TESTED | |
| 17 | Valid partial return | NOT TESTED | Backend logic proven by `retail_returns_wave0_test.py`; Android request-shape proven by `SaleContractTest.kt`'s return tests |
| 18 | Attempt excessive return | NOT TESTED (backend behavior UNIT TEST VERIFIED at the Python level) | |
| 19 | Attempt duplicate return | UNIT TEST VERIFIED (request shape only) | `CreateReturnRequest` now always carries a fresh `idempotency_key`; dedup behavior itself is backend logic already proven in `retail_returns_wave0_test.py` |
| 20 | Verify inventory | NOT TESTED | |
| 21 | Create a backup | NOT TESTED | Backend `/api/backup/create` unchanged from Wave 0/Phase 3.7, proven by `retail_backup_restore_test.py` at the Python level; never invoked from the Android UI in this phase (no backup UI screen exists — see parity matrix) |
| 22 | Change to Arabic | NOT TESTED | |
| 23 | Verify RTL | NOT TESTED | `supportsRtl="true"` confirmed in manifest; actual rendering never observed |
| 24 | Open barcode scanner | NOT TESTED | |
| 25 | Test permission flow | NOT TESTED | `ActivityResultContracts` permission-request code present and unchanged (source-reviewed), never exercised |
| 26 | Real scan (physical camera) | NOT TESTED | Explicitly not claimed — no physical device available |
| 27 | Close and restart | NOT TESTED | |

## What actually was proven this phase (build + unit-test level)

- Real `assembleDebug`/`assembleRelease`/`bundleRelease` succeed (build
  proves the app installs the way any APK does — packaging is valid).
- 29/29 Kotlin unit/contract tests pass, covering: sale request/response
  contract shape, the specific worked example (subtotal=100, discount=20,
  tax=10% → total=88), the Android-style zero-tax-input case, manipulated
  -response-is-still-read-verbatim, return request/response contract shape
  including idempotency, barcode debounce logic, product-lookup logic,
  number formatting/rounding, and the `/api/health` readiness-URL
  regression guard.
- 279/279 backend Python tests pass (the actual server logic every Android
  request ultimately depends on).

## Honest bottom line

**No claim of on-device functional correctness is made for Retail
Android.** The financial-authority, returns, and readiness fixes are
proven correct at the source/unit-test level, matching the real,
unmodified backend contract — but end-to-end, running-app validation
(items 1-27 above) requires a device or emulator this environment does not
have.
