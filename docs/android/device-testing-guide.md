# Android Device/Emulator Testing Guide

## Environment status (this phase)

No Android emulator (AVD) is installed and no physical device was connected in this
build environment. Nothing described in this document was executed in Phase 4 — this
is a guide for whoever runs the next phase (or a human tester) with real hardware
available, plus the exact list of things this phase could *not* honestly claim to
have verified. See `android-retail-parity-matrix.md` /
`android-clinic-parity-matrix.md` for the per-item status
(`REQUIRES PHYSICAL DEVICE VALIDATION`).

## Prerequisites

- A device or emulator running Android 8.0 (API 26) or later (project `minSdk 26`).
- For the emulator: an AVD image for `arm64-v8a` or `x86_64` (Chaquopy 16.0.0 only
  ships those two ABIs — no `armeabi-v7a`, no `x86`).
- USB debugging enabled (physical device) or the AVD already running.
- `adb` available (ships with the Android SDK platform-tools already present in this
  environment at `%LOCALAPPDATA%\Android\Sdk\platform-tools`).

## Install

```
cd android/aura-retail   # or aura-clinic
./gradlew installDebug
```

or manually:

```
adb install -r app/build/outputs/apk/debug/app-debug.apk
```

## Retail smoke test (18 steps)

1. Launch the app — confirm the loading screen appears, then either Setup or Login.
2. First run: complete Setup (create administrator account) — confirm it succeeds
   and lands on the Dashboard.
3. Dashboard: confirm today's metrics render (even if zero) and Quick Actions
   navigate to POS/Products.
4. Navigate every bottom-nav tab (Dashboard/POS/Products/More) and confirm each loads.
5. POS: add a product to the cart, adjust quantity, confirm the running total updates.
6. POS: complete a checkout/sale and confirm a success state.
7. POS: open the barcode scanner, grant camera permission, confirm the camera preview
   appears. **Camera/scan-accuracy itself requires a physical device — cannot be
   verified on most emulators without a virtual camera feed.**
8. Scan (or manually enter) a barcode and confirm it resolves to the right product,
   with no duplicate-scan double-add.
9. Deny camera permission once and confirm the app falls back to manual entry
   without crashing.
10. Products: search/filter and open a product's detail.
11. Reports/Transactions: confirm a list of recent transactions renders.
12. Returns: process a return against a prior sale.
13. Customers/Receivables: open a customer, confirm statement/aging views render.
14. Suppliers/Purchase Orders/Payables: open each, confirm lists render.
15. Cash Summary/Aging: confirm the totals reconcile with what was entered in POS.
16. Settings: switch language to Arabic — confirm the whole UI mirrors to RTL,
    including the bottom nav and drawer, and that no raw translation keys are shown.
17. Restart the app (kill + relaunch) — confirm the session persists (no re-login
    required) and previously entered data is still present.
18. Rotate the device / resize the window (if applicable) — confirm no crash and
    layout remains usable.

## Clinic smoke test (19 steps)

1. Launch the app — confirm the loading screen appears, then either Setup or Login.
2. First run: complete Setup (create administrator account) — confirm it succeeds.
3. Dashboard: confirm today's metrics render and Quick Actions navigate to
   Patients/Appointments/Billing.
4. Navigate every bottom-nav tab (Dashboard/Patients/Appointments/Billing).
5. Patients: add a new patient, confirm it appears in the list.
6. Patients: open a patient's detail view, confirm history/records render.
7. Appointments: book a new appointment for a patient.
8. Appointments: confirm the "waiting"/"active visits" counts on the Dashboard update.
9. Billing: create an invoice for a patient/visit.
10. Billing: record a payment against an invoice, confirm the balance updates.
11. Drawer: open Doctors, confirm the doctor list renders.
12. Drawer: open Lab Expenses, confirm entries can be added/viewed.
13. Drawer: open Prescriptions, confirm they render (tied to a patient).
14. Settings: confirm role/permission-gated sections behave per the logged-in user's role.
15. Settings: switch language to Arabic — confirm RTL mirroring across drawer, bottom
    nav, and all forms, with no raw translation keys visible.
16. Restart the app — confirm the session persists and previously entered patient/
    appointment/billing data is still present.
17. Log out and back in — confirm the login flow works end-to-end.
18. Navigate to a patient detail then press back — confirm it returns to the
    Patients list (not the Dashboard), matching the `isDetail` back-navigation logic.
19. Rotate the device / resize the window — confirm no crash and layout remains usable.

## What this phase could NOT verify (be honest about this)

- Actual on-device server startup/reachability (Chaquopy `Python.start()` +
  `main.start_server()` + `wait_until_ready()` actually succeeding on a real Android
  runtime, not just compiling).
- Camera permission flow and ML Kit barcode recognition accuracy (retail).
- Duplicate-scan suppression behavior under real camera input.
- RTL mirroring rendering correctness (only verified in source code, not visually).
- Data persistence across a real app restart/kill.
- Any gesture-navigation, back-button, or system-UI interaction.
- Battery/performance characteristics of running an embedded Python server on-device.

None of these are claimed as "passed" anywhere in this phase's output; each is marked
`REQUIRES PHYSICAL DEVICE VALIDATION` in the parity matrices.
