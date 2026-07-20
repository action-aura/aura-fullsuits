# Wave 1B — Pre-Wave-1B Release Inventory

## Verified before any change
- Tag `android-wave1a-physical-device-validated` confirmed pointing at HEAD (`6c44fa8`).
- Working tree clean, no untracked files.

## Windows packaging (built + smoke-tested fresh this wave)
- `products/retail/packaging/aura_retail.spec` → `dist/AuraRetail/AuraRetail.exe` — builds clean, launches, `/api/health` returns `200 {"status":"ok"}`.
- `products/clinic/packaging/aura_clinic.spec` → `dist/AuraClinic/AuraClinic.exe` — builds clean, launches, `/api/health` returns `200 {"status":"ok"}`.
- Both bind to `127.0.0.1:5000` by default (`_find_free_port(start=5000, stop=5020)` in each launcher).

## Android (carried forward from Wave 1A, unchanged)
- Retail: 29/29 Kotlin tests, debug + unsigned-release APK + AAB built and checksummed.
- Clinic: 45/45 Kotlin tests, debug + unsigned-release APK + AAB built and checksummed.

## Toolchain gaps identified and resolved this wave
- Inno Setup: missing → installed via winget (user-approved).
- Windows code-signing tool / certificate: missing → user confirmed no real certificate available; proceeding in documented "unsigned, scripts prepared" mode per spec's own allowance.
- Physical scanner/printer hardware: none available → user confirmed; proceeding with adapter architecture + simulated input, honestly labeled.

## Baseline preserved
All 6 baseline artifacts (2 Windows exes, 4 Android APKs) copied to `C:\Users\Dell\AuraReleaseBaselines\pre-wave1b\`, outside Git, for use as the upgrade-test starting point throughout Wave 1B. Full checksums in `baseline-artifact-register.md`.

## Not yet available
- No Android device connected at Part A time — required before Part J and Part C's physical verification.
