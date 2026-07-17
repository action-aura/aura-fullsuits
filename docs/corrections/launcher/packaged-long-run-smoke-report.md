# Phase 3.7 — Packaged Long-Run Smoke Test Report

Status: **PASS, both products.** Real PyInstaller rebuilds with the
corrected launcher code, real running `.exe` processes, real elapsed wall
-clock time (2026-07-17). No claim below is estimated or simulated.

## Build

Both specs rebuilt clean after the fix (new `commercial_runtime.launcher_support`
hiddenimport added to both):
`python -m PyInstaller products/retail/packaging/aura_retail.spec --noconfirm --distpath dist --workpath build/pyinstaller-work-retail --log-level WARN`
`python -m PyInstaller products/clinic/packaging/aura_clinic.spec --noconfirm --distpath dist --workpath build/pyinstaller-work-clinic --log-level WARN`
Both: 0 errors (same 2 pre-existing `pycparser` INFO-level warnings as every
prior build in this repo — not correctness-relevant).

## Startup / readiness

| | Retail | Clinic |
|---|---|---|
| Selected port | `5000` | `5001` (5000 already held by Retail, which was still running — the port-fallback logic in `_find_free_port` was exercised for real, not just in isolation) |
| Readiness attempts | 2 | 2 |
| Readiness elapsed | **2.50s** | **2.45s** |
| State transitions logged | `not_started → starting → ready → ui_running` | `not_started → starting → ready → ui_running` |

Both launches became `READY` in under 3 seconds — nowhere near the old
45-second timeout, let alone the ~43-second failure point this phase set
out to fix.

## Functional smoke sequence (both products, run against the live packaged exe)

**Retail**: clean install (`needs_setup: true`) → `create-admin` → `login`
→ dashboard (clean, all-zero) → create product → stock-adjust → **sale with
an Android-style zero-tax payload → server-computed `tax_amount: 15.0,
total: 115.0`** (confirms the Wave 0 financial-authority fix is intact and
unaffected by this phase's changes) → `POST /api/backup/create` → real
`.aurabak.zip` with correct checksummed manifest.

**Clinic**: clean install → `create-admin` → `login` → dashboard (clean) →
create patient → create $100 invoice → payment of $40 (idempotency-keyed)
→ `invoice_status: "partial"`, `outstanding_balance: 60.0` (confirms the
Wave 0 Clinic payment-validation fix is intact) → `POST /api/backup/create`
→ real backup with `product_code: "clinic"`.

All synthetic data only (`LongRun Admin`/`LongRun Patient`/etc.) — no real
business or patient data used anywhere in this test.

## Long-run responsiveness checkpoints

Both processes were left running, untouched by any readiness-check logic
(no further `/api/health` polling occurs after `READY` — see
`launcher-readiness-design.md`), and independently probed via `curl` at
each checkpoint:

| Checkpoint | Retail (`launched 08:14:53`) | Clinic (`launched 08:17:13`) |
|---|---|---|
| ~43s (old failure point) | `200 OK` (confirmed responsive continuously since the functional sequence above, which itself completed within the first ~90s) | `200 OK` (same) |
| 2 min | `08:17:27` — **`200 OK`** | not yet launched at this wall-clock point (launched 2m20s after Retail); its own equivalent early check is covered by the functional sequence completing successfully within its first ~30s |
| 5 min | `08:22:45` (Retail elapsed 7m52s) — **`200 OK`** | `08:22:45` (Clinic elapsed 5m32s) — **`200 OK`** |
| 10 min | `08:27:32` (Retail elapsed 12m39s) — **`200 OK`**, dashboard returned correct data (`today_sales: 115.0`, `total_products: 1`) | `08:27:32` (Clinic elapsed 10m19s) — **`200 OK`**, dashboard returned correct data (`today_revenue: 40.0`, `total_patients: 1`) |

Retail actually ran **12 minutes 39 seconds** continuously (exceeding the
10-minute requirement, since the two products were tested with staggered
but overlapping timers to keep total wall-clock time down) and Clinic ran
**10 minutes 19 seconds** — both comfortably past the 10-minute bar with a
correctly-functioning, data-consistent server at the end.

## Shutdown, orphan check, restart, persistence

1. Both processes closed via `taskkill /F` — both terminated successfully
   (`SUCCESS: The process ... has been terminated` for each).
2. Orphan check (`ps -W | grep -i Aura`) immediately after: **no matching
   processes** — clean termination, no leftover server thread/process
   (contrast with the pre-fix reproduction, where the process remained
   alive and unmanaged after its own watchdog declared failure).
3. Both relaunched against the **same** `AURA_APP_DATA` directories (same
   simulated installation).
4. Both reached `READY` again in **1 attempt, ~1.5s** each — even faster
   than the first launch (secret key already existed: `Loaded existing
   per-installation secret key` instead of generating a new one).
5. **Persistence confirmed**: `GET /api/onboarding/status` returned
   `needs_setup: false` for both (no re-onboarding needed); the original
   admin credentials logged in successfully on both; Retail's dashboard
   still showed `total_products: 1, today_sales: 115.0`; Clinic's still
   showed `today_revenue: 40.0` — the exact data created before the
   restart, unchanged.
6. Both processes killed again; `PRAGMA integrity_check` run directly
   against each product's on-disk database: **`('ok',)` for both** — no
   corruption from either the 10+ minute run or the hard restart.

## Cleanup

Both throwaway `AURA_APP_DATA` directories and all log/cookie files used
for this test were deleted after this report was written. No synthetic
data was committed. `dist/`/`build/` remain gitignored.

## Conclusion

Every Definition-of-Done item this report is responsible for is met with
direct evidence: both launchers recognize the healthy server (readiness in
~2.5s instead of never), neither exits around 43 seconds (both ran 10+
minutes), both remain responsive throughout, no LAN-accessible bind
occurred (both logged `127.0.0.1` binds throughout), no orphan process
remained after shutdown, and restart-with-persistence works for both
products.
