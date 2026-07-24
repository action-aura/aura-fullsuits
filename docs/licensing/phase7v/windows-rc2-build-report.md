# Phase 7V — Windows rc.2 Build Report (Part E)

## Build sequence

1. Rebuilt both frozen executables from current source (which now includes the Part G TLS fix):
   ```
   .venv/Scripts/pyinstaller.exe products/clinic/packaging/aura_clinic.spec --noconfirm
   .venv/Scripts/pyinstaller.exe products/retail/packaging/aura_retail.spec --noconfirm
   ```
   Both completed successfully (`Build complete!`), producing `dist/AuraClinic/AuraClinic.exe` and
   `dist/AuraRetail/AuraRetail.exe`.
2. Compiled both installers with the located Inno Setup compiler:
   ```
   "C:\Users\Dell\AppData\Local\Programs\Inno Setup 6\ISCC.exe" products/clinic/packaging/aura_clinic_setup.iss
   "C:\Users\Dell\AppData\Local\Programs\Inno Setup 6\ISCC.exe" products/retail/packaging/aura_retail_setup.iss
   ```
   Both compiled successfully.

## Artifacts produced (final, superseding an earlier intermediate build)

These are the **final** rc.2 checksums, built after the two P0 fixes below were applied
(stale-PyInstaller-cache purge, trust-anchor `datas` bundling) and after confirming no ephemeral
test-Owner trust anchor was baked in (see `windows-rc1-to-rc2-installer-validation.md` for how
that was discovered and cleaned up):

| Artifact | Path | SHA-256 | Size |
|---|---|---|---|
| Aura Clinic rc.2 installer | `dist/installers/AuraClinic-Setup-1.0.0-rc.2.exe` | `e6ade67954d0cf3a7d2f1b063749c20b0c9fa605d987e89b8d675a0ac9d2d878` | 14,655,130 bytes |
| Aura Retail rc.2 installer | `dist/installers/AuraRetail-Setup-1.0.0-rc.2.exe` | `e7e5a033d60dddea4a60727ec9a686ddf5dff04edb5a3159b910c562dcc97c24` | 15,430,226 bytes |

rc.1 installers (`AuraClinic-Setup-1.0.0-rc.1.exe`, `AuraRetail-Setup-1.0.0-rc.1.exe`) were **not
overwritten** — both version-stamped filenames coexist in `dist/installers/`.

An intermediate rc.2 build (checksums `e499474f...` Clinic / `bec5000b...` Retail) was produced
before two real defects were found during live validation and fixed in-place (see Part F/G docs):
(1) a stale PyInstaller build cache bundled a broken partial `backports.zstd` module that crashed
the app at startup, fixed via `--clean`; (2) `trust_anchor.json` was never included in either
`.spec`'s `datas`, meaning no commercial build could ever verify a real Owner response, fixed by
adding a conditional `datas` entry to both specs. The checksums above are the final, fixed,
verified-working artifacts — the intermediate ones were never shipped or referenced elsewhere.

## Version and identity verified

- `AppVersion "1.0.0-rc.2"` in both `.iss` files, feeding `AppVerName`, `OutputBaseFilename`, and
  the installer's embedded version resource.
- `AppId` (Inno Setup's upgrade key) is an unchanged, fixed GUID for each product
  (`{159905F6-CEB8-4A5F-B5C3-D0190159F679}` Clinic, `{A039EDA8-410E-4400-B3A5-A7CD4AF7F437}`
  Retail) — required for the Part F upgrade-continuity test to work at all.
- `products/clinic/backend/config.py` / `retail/backend/config.py`: `APP_VERSION = '1.0.0-rc.2'`.
- Installation directory: `{autopf}\Action Aura\Aura {Clinic|Retail}` (Program Files), data
  written to `{localappdata}\Aura{Clinic|Retail}` — outside Program Files, writable without
  elevation, matching `PrivilegesRequired=lowest`.

## Content inspection

- Correct executable included (`AuraClinic.exe` / `AuraRetail.exe` at dist tree root).
- Licensing static assets included: `products/clinic/frontend/licensing.html`,
  `licensing.js` (and Retail's mirrors) confirmed present under each dist tree's
  `_internal/products/{clinic,retail}/frontend/`.
- **No trust anchor bundled** — `trust_anchor.json` does not exist in either dist tree (it is a
  runtime-generated deployment artifact per Phase 7 Part D, never a build input).
- **No private signing key** — Owner's signing keys live only in `owner/var/signing-keys/`, never
  referenced by either product's PyInstaller spec.
- **No license key** — nothing license-key-shaped is ever written to disk by these builds; license
  keys are transient (used once at activation, never persisted per Phase 7 Part C guarantees).
- **No generated `secret.key`** — absent from both dist trees (would only be generated at first
  run against a real `AURA_APP_DATA`, gitignored per the Phase 7 hardening pass, and never a build
  input).
- **No synthetic customer/patient data** — clean PyInstaller `Analysis()` build from source only,
  no database files bundled.
- **No development trust anchor or Owner URL baked in** — `licensing.js`/`licensing.html` contain
  no `localhost`/`127.0.0.1`/hardcoded `http://` references (grepped, zero matches); the Owner URL
  is operator-configured at deployment time, never compiled into the frontend.
- **No debug configuration** — `[Setup]` has no `DisableWelcomePage`/debug flags; PyInstaller specs
  build in `--noconfirm` release mode, not `--debug`.
- **No hidden license bypass** — confirmed via Part G's live TLS-rejection test and the existing
  deny-by-default `capability_guard`; nothing in the installer changes that gate.
- **Windows unsigned status explicitly preserved and documented** — no Authenticode certificate
  available in this environment (same as Wave 1B); both `.iss` files have no `SignTool` directive.
  Installing either rc.2 build will still trigger a SmartScreen "unrecognized publisher" warning.
  This is a known, disclosed limitation, not a defect introduced by Phase 7V.

## Outcome

Both Windows rc.2 installers built successfully from the current (TLS-fixed) source, contain the
expected licensing UI, contain no secrets or dev-only configuration, and preserve upgrade
continuity (`AppId`) with their rc.1 predecessors. See `windows-rc1-to-rc2-installer-validation.md`
for the actual upgrade-lifecycle test performed with these artifacts.
