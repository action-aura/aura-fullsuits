# Phase 3.5 — Audit Scope and Inventory

Analysis-only phase. No production source was modified to produce this audit.
Evidence gathered by: direct source reading, direct `pytest`/`gradlew` execution
in this environment, and targeted grep sweeps. Where evidence required a
physical device, printer, barcode scanner, or a real production-scale dataset,
it is marked UNVERIFIED in the relevant report — see each document's own
"unverified" section rather than assuming completeness from this index.

## Repositories

| Repo | Path | Role | Status this audit |
|---|---|---|---|
| Original | `c:\Users\Dell\Desktop\AuraEnterprise\AuraEnterprise` | Source of truth for pre-extraction code. **Read-only** — nothing here was touched. | Currently on branch `feat/crm-enterprise-lead-management`, HEAD `414e6ea5`, with a large set of **uncommitted local changes** (api/, core/crm/, database/, app.py, config.py, .gitignore, .superpowers/sdd/progress.md — CRM feature work unrelated to Retail/Clinic). This means the original repo has moved on since Retail/Clinic were extracted; **no commit SHA was recorded at extraction time** in any prior-phase doc, so the exact original-repo state Retail/Clinic Phase 1-3 were extracted from cannot be pinpointed retroactively. This is a documented audit limitation, not a defect introduced by this audit. |
| Extracted/commercial | `c:\Users\Dell\Desktop\AuraEnterprise\aura-fullsuits` | The product under audit. | Branch `master`, HEAD `f68fd44` at audit start, clean working tree (no uncommitted changes at start), 23 commits total, all dated 2026-07-12 (Retail Phase 1-2 + Clinic Phase 3) and 2026-07-16 (Android Phase 4). Tags: `retail-extraction-phase2-complete`, `clinic-extraction-phase3-complete`, `android-migration-phase4-complete`. |

## Products and platforms actually found

| Product | Windows/desktop | Android | Notes |
|---|---|---|---|
| Aura Retail | **PRESENT** — `products/retail/desktop/launcher_retail.py`, `products/retail/packaging/aura_retail.spec`. Built and smoke-tested in Phase 2B (`docs/build/retail-windows-build-report.md`, `dist/AuraRetail/AuraRetail.exe`, real PASS). | **PRESENT** — `android/aura-retail/`, independent Gradle project, built this session (Phase 4): debug/staging/release APKs all produced. | No original-source Windows-only Retail EXE was found separately — Retail existed as one flavor of the monolith's shared desktop build in the original repo; `launcher_retail.py`/the standalone spec are new files (Phase 2), patterned on the monolith's shared launcher, not fabricated from nothing. |
| Aura Clinic | **PRESENT** — `products/clinic/desktop/launcher_clinic.py`, `products/clinic/packaging/aura_clinic.spec`. Built and smoke-tested in Phase 3 (`docs/build/clinic-windows-build-report.md`, `dist/AuraClinic/AuraClinic.exe`, real PASS, 13/13 smoke steps). | **PRESENT** — `android/aura-clinic/`, independent Gradle project, built this session (Phase 4). | Same situation as Retail: Clinic ran inside the original monolith's shared desktop bootstrap, not as a separate original EXE. Windows is correctly classified PRESENT (not NOT PRESENT IN SOURCE) per the reasoning already recorded in `docs/build/clinic-windows-build-report.md`. |

No product/platform combination in this audit's scope is classified `NOT PRESENT IN SOURCE` — both products exist on both platforms, in both the original monolith form and the extracted standalone form.

## Shared layer

`commercial_runtime/` (`diagnostics/`, `identity/`, `installation/`, `licensing_contracts/`, `networking/`, `security/`, `telemetry/`, `updates/`) — multi-tenant registry (`users`, `company_modules`, `user_permissions`, `audit_logs`, `secure_links`, `company_settings`), password hashing, per-install secret key, auth routes. Used identically by Retail and Clinic, on both Windows and Android (Android's Chaquopy backend is literally the same Python package tree, not a reimplementation — see `android/aura-*/app/build.gradle`'s `stageAuraPython` task).

## Identity / versioning found

| Field | Retail | Clinic |
|---|---|---|
| `config.py` `APP_VERSION` | `0.1.0` | `0.1.0` |
| Android `versionName`/`versionCode` | `1.0.0` / `1` | `1.0.0` / `1` |
| Android `applicationId` | `com.actionaura.retail` | `com.actionaura.clinic` |
| Schema version tracking | **None found** — no `schema_version` table, no migration framework; schema is `CREATE TABLE IF NOT EXISTS` only (see `06-retail-data-integrity.md`/`07-clinic-data-integrity.md`) | Same |

Desktop `APP_VERSION` (0.1.0) and Android `versionName` (1.0.0) are **not synchronized** — two different version numbers exist for what is meant to be "the same product" across platforms. Minor, tracked in the defect registry.

## What this audit did and did not execute

- Ran the full existing automated test suite (both products) directly, per-file and combined — see `02-test-coverage-and-evidence.md`.
- Read and traced financial-calculation code paths end to end (client + server, Windows + Android) for both products — see `03`/`04`/`05`.
- Ran grep-based structural sweeps of schema, auth, authorization, and network-exposure code across both backends and both Android apps — see `06`-`11`.
- Did **not** run a physical Android device, printer, or barcode scanner (none available in this environment — same limitation already documented in `docs/android/device-testing-guide.md` from Phase 4).
- Did **not** generate and load a 10,000+/50,000+ row synthetic dataset for full-scale performance measurement in this pass — see `17-performance-and-scale-audit.md` for what was and wasn't measured, and why.
- Did **not** inspect, copy, or reference any real customer or patient data. All data referenced in test evidence is synthetic (from the existing automated test suites, which use fabricated names/emails/amounts) or is described but not created (audit is documentation/read-only by scope).

## Stop-condition check

None of the Phase 3.5 stop conditions were triggered: no real customer/patient data found in the repository, no production passwords or signing keys found (Windows builds are unsigned by design — see `docs/android/signing-and-release-guide.md` and the Windows build reports; Android release/staging APKs are likewise unsigned), no destructive migration was required, database ownership is unambiguous (all data seen was test/synthetic), and the original repository's uncommitted CRM work was left untouched throughout.
