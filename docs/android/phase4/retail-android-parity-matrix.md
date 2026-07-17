# Aura Retail Android — Parity Matrix (Phase 4V)

Every FAIL below is explained; none are unexplained.

| Area | Status | Note |
|---|---|---|
| Build system | BUILD VERIFIED | Real `clean`/`assembleDebug`/`assembleRelease`/`bundleRelease` succeed (`retail-build-report.md`) |
| Compose | BUILD VERIFIED | Compiles clean under Compose compiler 2.0.21; UI never rendered (no device) |
| Chaquopy | BUILD VERIFIED | Python source stages and compiles into the APK; server startup never exercised on-device |
| Backend startup | SOURCE REVIEW ONLY | `ServerBootstrap`/`main.py` logic unchanged in shape, traced correct |
| Health readiness | PASS WITH DOCUMENTED LIMITATION | Fixed this phase (`/api/health`, not `/`) — source-verified + regression-guarded by unit test; never observed succeeding on a real launch |
| Onboarding | REQUIRES PHYSICAL DEVICE | Backend logic proven (Python tests); UI flow never run |
| Authentication | REQUIRES PHYSICAL DEVICE | Same |
| Database | REQUIRES PHYSICAL DEVICE | SQLite path/init logic unchanged from prior migration, not re-verified on-device this phase |
| Dashboard | REQUIRES PHYSICAL DEVICE | |
| Products | REQUIRES PHYSICAL DEVICE | |
| POS | REQUIRES PHYSICAL DEVICE | |
| Cart | REQUIRES PHYSICAL DEVICE | Local preview-total logic (classification A) confirmed by source review to never be persisted/displayed as authoritative |
| Financial authority | PASS | **Fixed this phase.** `SaleResult` now carries the full server contract; checkout displays `r.data.total`. Verified by 3 dedicated unit tests + the unchanged backend's own 279 tests. Not device-verified. |
| Tax | PASS | Client never sends `tax_rate`/`tax_amount`; server always resolves from the product row (Wave 0, unchanged) |
| Discount | PASS WITH DOCUMENTED LIMITATION | Client can only ever send `discount_pct=0` (no discount-entry UI in source) — clamped/validated server-side regardless; NOT a regression, just an absent feature |
| Sale | PASS | Zero-tax-on-Android defect (AUDIT-002) fixed at the display layer this phase; server-side fix already existed (Wave 0) |
| Returns | PASS | Idempotency-key gap closed this phase; refund contract now fully typed |
| Inventory | REQUIRES PHYSICAL DEVICE | Stock-adjust/oversell logic unchanged, server-enforced (Wave 0), UI never exercised |
| Import | NOT APPLICABLE | Import is a desktop-only feature (`import_api.py`); no Android import UI exists in source |
| Barcode | PASS WITH DOCUMENTED LIMITATION | Debounce + product-lookup logic extracted and unit-tested this phase; actual CameraX/ML Kit camera behavior REQUIRES PHYSICAL DEVICE |
| CameraX | REQUIRES PHYSICAL DEVICE | Lifecycle/permission code unchanged, source-reviewed only |
| ML Kit | REQUIRES PHYSICAL DEVICE | Offline barcode model behavior cannot be verified without a camera |
| Localization | REQUIRES PHYSICAL DEVICE | `en`/`ar` string resources present (unchanged), never rendered |
| RTL | REQUIRES PHYSICAL DEVICE | `supportsRtl="true"` confirmed in manifest |
| Theme | REQUIRES PHYSICAL DEVICE | Light/dark theme code unchanged |
| Backup | NOT PRESENT IN SOURCE | No backup/restore UI screen exists in the Android app (grep-confirmed); backend endpoint exists and is proven at the Python level (Wave 0/Phase 3.7) |
| Offline operation | SOURCE REVIEW ONLY | Embedded server + local SQLite architecture unchanged; never observed running fully offline on a device |
| Restart persistence | REQUIRES PHYSICAL DEVICE | |
| Security configuration | PASS | `allowBackup=false`, loopback-only cleartext exception, no exported components beyond `MainActivity`, zero logging — all confirmed this phase |
| Signing | PASS WITH DOCUMENTED LIMITATION | Unsigned by design (no production key exists); verified via `jarsigner`/`apksigner` this phase, not a gap in the build config itself |
