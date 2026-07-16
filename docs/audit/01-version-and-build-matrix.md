# Version and Build Matrix

Statuses used: TESTED (this audit or a prior phase actually ran it and recorded
a result) · BUILD ONLY (compiles/packages, not exercised at runtime) · STALE
(known to be behind current source) · UNKNOWN (could not be determined).

## Aura Retail

| Artifact | Location | Version/commit | Status |
|---|---|---|---|
| Original source (monolith flavor) | `AuraEnterprise/android/`, `AuraEnterprise/app.py` etc, on branch `feat/crm-enterprise-lead-management` | HEAD `414e6ea5`, uncommitted CRM changes on top | UNKNOWN exact extraction-point commit (not recorded by Phase 1/2) |
| Extracted backend | `aura-fullsuits/products/retail/backend/` | `aura-fullsuits` HEAD `f68fd44`, `APP_VERSION=0.1.0` | TESTED — 116/116 unit+integration tests pass (isolated per-file run, this audit, see `02`) |
| Windows packaged build | `dist/AuraRetail/AuraRetail.exe` (not committed, build artifact) | Built from `aura-fullsuits` HEAD at Phase 2B time | TESTED — Phase 2B smoke test PASS (`docs/build/retail-windows-build-report.md`); **not re-built in this audit pass**, so this specific status reflects Phase 2B's commit, which may be STALE relative to current HEAD (no backend changes have landed since Phase 2B per `git diff`, so functionally equivalent, but the literal .exe file predates several later commits) |
| Android source | `android/aura-retail/` | `aura-fullsuits` HEAD `f68fd44` | TESTED — real `assembleDebug`/`assembleStaging`/`assembleRelease` succeeded (Phase 4, `docs/android/android-retail-build-report.md`) |
| Android debug APK | `android/aura-retail/app/build/outputs/apk/debug/app-debug.apk` (not committed) | Phase 4 build, SHA-256 `bc8539b4...` | BUILD ONLY — no emulator/device run in Phase 4 or this audit |
| Android staging/release APK | same dir, `-unsigned.apk` | Phase 4 build | BUILD ONLY, unsigned |

## Aura Clinic

| Artifact | Location | Version/commit | Status |
|---|---|---|---|
| Original source (monolith flavor) | Same original repo, same caveat as Retail | UNKNOWN exact extraction-point commit | UNKNOWN |
| Extracted backend | `aura-fullsuits/products/clinic/backend/` | `APP_VERSION=0.1.0` | TESTED — 95/95 tests pass (isolated per-file run, this audit, see `02`) |
| Windows packaged build | `dist/AuraClinic/AuraClinic.exe` | Phase 3 | TESTED — 13/13 smoke steps PASS, including a post-IDOR-fix rebuild re-verification (`docs/build/clinic-windows-build-report.md`) |
| Android source | `android/aura-clinic/` | `aura-fullsuits` HEAD `f68fd44` | TESTED — real Gradle builds succeeded (Phase 4) |
| Android debug/staging/release APK | `android/aura-clinic/app/build/outputs/apk/**` | Phase 4 build, SHA-256s recorded in `docs/android/android-clinic-build-report.md` | BUILD ONLY — no emulator/device run |

## Cross-check: is extracted source synchronized with the packaged/built artifacts?

Retail and Clinic Windows `.exe` files were built at their respective phase's HEAD and **not rebuilt during this audit**. `git diff --stat -- products commercial_runtime` between the Windows-build commits and current HEAD shows **no changes** to `products/` or `commercial_runtime/` since Phase 3/4 (confirmed in Phase 4's own independence check) — so the packaged `.exe` files remain representative of current source. The Android builds were produced directly from current HEAD in Phase 4, four days after the Windows builds, from the same unmodified backend — so Windows and Android currently share byte-identical Python backend code (they are not two implementations, they are the same files staged into two different runtimes). This matters for the financial-parity audit (`05`): any financial-logic difference found between Windows and Android is a **client-side** (UI) difference, not a backend-logic fork, because there is only one backend.

## Package/application identity

| | Retail | Clinic |
|---|---|---|
| Windows exe name | `AuraRetail.exe` | `AuraClinic.exe` |
| Android `applicationId` | `com.actionaura.retail` (`.debug`/`.staging` suffix per build type) | `com.actionaura.clinic` |
| Desktop `APP_VERSION` | `0.1.0` | `0.1.0` |
| Android `versionName` | `1.0.0` | `1.0.0` |
| DB engine | SQLite (WAL, per `06`/`07`) | SQLite (WAL, per `06`/`07`) |
| Schema version tracking | None | None |

**Finding (tracked in the defect registry as a P4):** desktop `APP_VERSION` (0.1.0) and Android `versionName` (1.0.0) disagree for both products — there is no single "the app is version X" number a support agent or customer could quote that's consistent across platforms.
