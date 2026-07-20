# Wave 1B -- Commercial Packaging, Release Hardening & Hardware Integration: Handover

## What this wave delivered
Both Aura Retail and Aura Clinic moved from "extracted, working standalone products" (end of Wave 1A) to **release-candidate packages** on both Windows and Android: versioned (`1.0.0-rc.1`), installable with a real Windows installer and a signed Android APK/AAB, safe to upgrade/uninstall/reinstall without data loss, schema-migration-safe, barcode-scanner-capable (Windows proven, Android newly built and device-verified), receipt-capable (Windows OS-print-spooler, Android share-fallback), security-reviewed, and fully documented -- including every real limitation, not just the successes.

## Read these first
- `release-candidate-manifest.md` -- the actual artifacts, checksums, and honest status labels.
- `wave1b-defect-registry.md` -- every real bug found and fixed this wave, most importantly **REL-006** (a genuine cross-product port-race that could silently cross-wire Retail and Clinic onto each other's backend under simultaneous launch -- found during this wave's own restart-persistence testing, fixed, and verified).
- `wave1b-residual-risk-register.md` -- what's still open going into Wave 1C, stated plainly.
- `retail-commercial-compatibility-matrix.md` / `clinic-commercial-compatibility-matrix.md` -- area-by-area status for everything a commercial buyer would ask about.

## Full document index (this wave)
**`docs/release/wave1b/`**: `pre-wave1b-release-inventory.md`, `baseline-artifact-register.md`, `build-environment-report.md` (Part A); `clinic-arabic-localization-report.md` (Part C); `pytest-isolation-correction.md` (Part D); `retail-windows-installer-report.md`, `clinic-windows-installer-report.md`, `windows-upgrade-data-preservation-report.md` (Parts E-G); `schema-migration-and-data-safety-report.md` (Part K); `release-security-review.md` (Part S); `build-and-test-gates-report.md` (Part T); `release-candidate-manifest.md` (Part U); `retail-commercial-compatibility-matrix.md`, `clinic-commercial-compatibility-matrix.md` (Part V); `wave1b-defect-registry.md` (Part W); `wave1b-residual-risk-register.md`.

**`docs/hardware/`**: `barcode-input-architecture.md`, `barcode-scanner-compatibility-matrix.md`, `barcode-scanner-setup-guide.md` (Parts L-N); `receipt-printer-architecture.md`, `receipt-printer-compatibility-matrix.md`, `receipt-printing-test-report.md` (Parts O-P).

**`docs/release/`** (not wave-scoped subfolder): `versioning-policy.md` (Part B); `windows-code-signing-guide.md` (Part H); `android-production-signing-policy.md`, `android-key-backup-checklist.md` (Part I); `customer-data-preservation-policy.md` (Part R).

## What's genuinely new and load-bearing
1. **Android HID barcode scanning** (`HidScanDetector.kt`, `HidScanBus.kt`, `MainActivity.dispatchKeyEvent`) -- a real, previously-missing capability, ported from the discovered, proven Windows engine, not reinvented.
2. **Receipt printing** -- Windows gained real OS-print-spooler support; Android gained a real, honest share-sheet fallback. A real data-authority bug (client cart data mixing into the printed receipt) was found and fixed as a natural consequence.
3. **Schema migration safety** (`migration_safety.py`) -- pre-migration backup, version-marker discipline, rollback-on-failure, tested against real pre-existing data on both products.
4. **Production Android signing** -- separate keystores per product, 27-year validity, real device-verified signed upgrades.
5. **Windows installers** with a real safety fix (silent-uninstall auto-delete risk) that would otherwise have been a genuine data-loss bug in production.
6. **The port-race fix (REL-006)** -- arguably this wave's most important finding: not something the original spec anticipated, but exactly the kind of defect thorough restart/sustained-run testing exists to catch.

## What is explicitly NOT done (see residual-risk-register.md for full detail)
- No code-signing certificate for Windows (unsigned, SmartScreen warning on first run).
- No direct ESC/POS thermal printing on either platform (OS-print-spooler / share-sheet cover the practical case).
- No camera-based or serial/BLE/vendor-SDK barcode scanning.
- Windows receipt printing not click-through tested against a real print dialog.
- Owner Control Center, licensing, subscription enforcement, telemetry, auto-updates, Jordan e-invoicing, WhatsApp/SMS automation, multi-branch sync, Aura Core integration -- all explicitly out of scope by this wave's own spec, not started.

## Explicit non-claims (per this wave's instruction)
Neither product is described anywhere in this wave's documentation as commercially ready, production ready, enterprise grade, safe for paid customers, or universally compatible with all scanners/printers. Every status label in every report is scoped to exactly what was built and verified.

## Next step
Wave 1C, when the user initiates it -- this wave stops here as instructed.
