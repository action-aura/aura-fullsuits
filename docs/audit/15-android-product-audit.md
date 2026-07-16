# Android Product Audit

Status values used per the audit spec: TESTED ON PHYSICAL DEVICE · TESTED ON
EMULATOR · SOURCE REVIEW ONLY · BUILD ONLY · NOT TESTED. No physical device or
emulator was available in this environment or in Phase 4 — **every runtime
item below is BUILD ONLY or SOURCE REVIEW ONLY**, never TESTED ON
PHYSICAL/EMULATOR. This is stated once here and applies throughout; it is not
repeated per row.

## BUILD

| Item | Retail | Clinic | Status |
|---|---|---|---|
| Gradle wrapper | 8.9, present, real builds succeed | Same | BUILD ONLY (but genuinely executed, not simulated) |
| JDK compatibility | Java 17, confirmed working | Same | BUILD ONLY |
| AGP compatibility | 8.5.2, confirmed working | Same | BUILD ONLY |
| Kotlin compatibility | 2.0.21, confirmed working | Same | BUILD ONLY |
| Compose compatibility | BOM 2024.09.03, confirmed working | Same | BUILD ONLY |
| Chaquopy compatibility | 16.0.0, real pip installs succeeded for both ABIs | Same | BUILD ONLY |
| Python dependencies | Trimmed to actual imports, real `pip install` succeeded (`05`, Phase 4 dependency map) | Same, further trimmed (no openpyxl/requests) | BUILD ONLY |
| `assembleDebug` | PASS, real build, 7m33s | PASS, real build, 5m45s | BUILD ONLY |
| Unit tests | 0 exist (`NO-SOURCE`) | 0 exist (`NO-SOURCE`) | N/A — honestly reported, not a failure |
| Lint | 0 errors, 27 warnings (cosmetic) | 0 errors, 23 warnings (cosmetic) | BUILD ONLY |
| `assembleRelease` | PASS, unsigned | PASS, unsigned | BUILD ONLY |
| Signing state | Debug-signed (debug variant) / genuinely unsigned (staging, release) | Same | BUILD ONLY, verified via `apksigner verify` |

## RUNTIME

Every item in this section is **SOURCE REVIEW ONLY** — none was exercised on a
device.

- **Startup / embedded backend**: `ServerBootstrap.start()` boots Chaquopy,
  calls `main.start_server()`/`wait_until_ready()`. Contract matches the
  Kotlin caller's expectations by source read; whether the Python interpreter
  actually initializes correctly inside Android's runtime (vs. desktop
  CPython) was never exercised.
- **Local port**: same `127.0.0.1`-bound pattern as desktop (`08`).
- **Readiness**: `wait_until_ready()` polls `http://127.0.0.1:<port>/` for up
  to 45s before the Kotlin side proceeds — reasonable design, unverified in
  practice.
- **Restart / process death**: Android can kill a backgrounded app's process
  at any time under memory pressure; whether `ServerBootstrap`'s
  `@Volatile private var port` state correctly re-initializes after a process
  is killed and the activity is recreated was not examined in source in this
  pass — UNVERIFIED, flagged for follow-up (this is a common source of "the
  app shows a blank/frozen screen after being backgrounded for a while" bugs
  on Android specifically, and this architecture — a long-lived embedded
  server process tied to the app's own process lifetime — is exactly the
  pattern most exposed to it).
- **Database**: same embedded SQLite as desktop, no separate Android data
  layer (`06`/`07`).
- **Offline mode**: PASS by architecture (fully local).
- **Configuration changes (rotation)**: `MainActivity` was migrated verbatim
  from source; not independently re-examined for `Activity` recreation
  handling in this pass.
- **Permission flow (Retail camera)**: `BarcodeScanner.kt` migrated verbatim;
  actual runtime permission grant/deny behavior REQUIRES PHYSICAL DEVICE
  VALIDATION (unchanged conclusion from Phase 4).
- **Theme/language/RTL**: source exists and compiles; visual correctness
  REQUIRES PHYSICAL DEVICE VALIDATION.
- **Back navigation**: `isDetail` logic for Clinic's patient-detail screen
  confirmed present in source (`13`); not exercised on-device.

## MOBILE UX

Not independently assessed in this pass beyond what Phase 4's migration
preserved from source — touch-target sizing, tablet layout, accessibility
labels, and screen-reader support were not audited (no accessibility-specific
review was performed in Phase 4 or this audit). **Treated as NOT ASSESSED**
rather than PASS or FAIL — a real gap in this audit's own coverage, called out
explicitly rather than assumed fine.

## RETAIL-SPECIFIC

- **CameraX lifecycle, ML Kit, duplicate-scan suppression**: source exists
  (`BarcodeScanner.kt`), REQUIRES PHYSICAL DEVICE VALIDATION for all of it —
  unchanged from Phase 4's own honest conclusion.
- **Cart speed / checkout speed**: not measurable without a device.
- **The tax/discount omission** (`03`) is itself a "retail-specific Android"
  finding and the most severe item in this whole document — repeated here for
  completeness, detailed fully in `03`/`05`.

## CLINIC-SPECIFIC

- **Patient confidentiality / screenshots**: `FLAG_SECURE` is not set on
  either product (`10`) — for Clinic this has real patient-privacy
  implications (an OS-level screenshot or recent-apps preview could capture
  a patient's name/visit data) — see `11`.
- **Long patient forms, appointment booking, medical notes, role navigation,
  billing forms**: source exists, migrated per Phase 4's own parity matrix;
  not re-examined for mobile-specific usability concerns in this pass.

## Summary

Both Android apps are in the same overall state: **real, verified, successful
builds** with **zero runtime verification** (no device/emulator has been
available at any point across Phase 4 or this audit). This is an honest,
consistently-documented limitation, not a hidden gap — but it means neither
app can be described as "tested" in the sense a customer-facing release would
require; both are, at best, BUILD ONLY today. Retail Android additionally
inherits the two severe defects proven by source in this audit
(no onboarding path, no tax/discount) which would block a device test from
even reaching a successful first sale if one were performed today.
