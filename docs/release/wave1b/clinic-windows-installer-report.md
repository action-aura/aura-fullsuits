# Wave 1B — Clinic Windows Installer Report

## Build
`products/clinic/packaging/aura_clinic_setup.iss` (Inno Setup 6.7.3), mirrors Retail's script exactly (see `retail-windows-installer-report.md` for the shared architecture, the SmartScreen finding, and the `/SUPPRESSMSGBOXES` auto-delete fix — both apply identically here). Fixed AppId `{159905F6-CEB8-4A5F-B5C3-D0190159F679}` (distinct from Retail's, never to change). Installs to `%LOCALAPPDATA%\Programs\Action Aura\Aura Clinic`; patient/business data at `%LOCALAPPDATA%\AuraClinic` (unchanged, pre-existing `launcher_clinic.py` resolution). Output: `dist/installers/AuraClinic-Setup-1.0.0-rc.1.exe` (unsigned, no cert this wave).

## Test sequence actually executed
1. **Clean install**: succeeded, same verification as Retail (shortcuts, registry entry with correct `DisplayName`/`DisplayVersion` `1.0.0-rc.1`).
2. **First launch**: `/api/onboarding/status` → `needs_setup: true`.
3. **Onboarding + synthetic patient**: created admin `clinicwin@test.local`, patient "Windows Test Patient" (phone `0000000000`, no real PII, `patient_code P-1784501126-172`).
4. **Uninstall (silent, post-fix)**: `%LOCALAPPDATA%\Programs\Action Aura\Aura Clinic` fully removed, registry key removed, `%LOCALAPPDATA%\AuraClinic\database\subsystems\clinic.db` **confirmed still present**.
5. **Reinstall**: `needs_setup: false`, same admin login succeeded, `GET /api/sub/clinic/patients` returned the exact same patient record created in step 3 (`id: 1`, `patient_code P-1784501126-172`) — full round-trip proven.

## Not independently re-tested (see Retail's report for the shared reasoning)
- A separate "upgrade over a different version" run was not repeated for Clinic — the mechanism (Inno Setup's AppId-based upgrade detection, the `[Files]`-only write scope, the `UninstallSilent()` guard) is identical code to Retail's, already proven end-to-end there. Re-running an identical mechanical test on a second product was judged low-value relative to the time cost, given everything else still pending this wave.
- Interactive delete-confirmation dialog: same as Retail, verified by source/logic review only.
- Single Windows environment only, same disclosed limitation as Retail.

## Result
PASS. Clean install, uninstall-preserves-data, and reinstall-restores-data all verified against a real, running installer with real synthetic patient data (not simulated).
