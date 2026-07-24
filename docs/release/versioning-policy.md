# Aura Release Versioning Policy

## Scheme
Semantic versioning with a release-candidate suffix: `MAJOR.MINOR.PATCH-rc.N`.

Initial Wave 1B release-candidate version, all four artifact families:

| Product | Platform | Version |
|---|---|---|
| Aura Retail | Windows | 1.0.0-rc.1 |
| Aura Retail | Android | 1.0.0-rc.1 (versionCode 2) |
| Aura Clinic | Windows | 1.0.0-rc.1 |
| Aura Clinic | Android | 1.0.0-rc.1 (versionCode 2) |

Pre-Wave-1B state was an unsynchronized placeholder: Android `versionName "1.0.0"` / `versionCode 1` for both apps, Windows `APP_VERSION = '0.1.0'` in each backend config, and no embedded Windows executable version resource at all. None of these constituted a "stronger existing policy" per the spec's own allowance, so the spec's preferred `1.0.0-rc.1` was adopted outright.

## Product codes
- `AURA_RETAIL`
- `AURA_CLINIC`

(Matches the codes already used internally by `commercial_runtime/backup/service.py`'s backup filenames, e.g. `aura-retail-backup-...`.)

## Where version identity lives (single source per platform, kept in sync manually)
- **Backend (Windows + the embedded Android backend, same code)**: `APP_VERSION` in `products/retail/backend/config.py` / `products/clinic/backend/config.py`.
- **Android**: `versionName` / `versionCode` in `android/aura-retail/app/build.gradle` / `android/aura-clinic/app/build.gradle`.
- **Windows executable metadata**: `products/retail/packaging/version_info.txt` / `products/clinic/packaging/version_info.txt`, wired into each `.spec` via `EXE(..., version=...)`.
- **Windows installer**: `#define AppVersion "..."` in `products/retail/packaging/aura_retail_setup.iss` / `products/clinic/packaging/aura_clinic_setup.iss` -- **missed by this document until Phase 7's baseline audit found it still reading `1.0.0-rc.1` after every other source had already been listed here**; added to this list so it is never missed again.

## Machine-readable exposure
Both products' Flask apps expose `GET /api/version` (new this wave, deliberately separate from `/api/health`, whose contract is frozen for the launcher's readiness probe):

```json
{
  "product_code": "AURA_RETAIL",
  "product_name": "Aura Retail",
  "app_version": "1.0.0-rc.1",
  "schema_version": 1,
  "calculation_version": "retail-pricing-v2-wave0"
}
```

Clinic's response omits `calculation_version` (no separate calculation engine to version, per `docs/architecture/financial-authority-contracts.md`).

## Schema version
`SCHEMA_VERSION = 1` in `commercial_runtime/backup/service.py` — pre-existing, already used for backup-compatibility checks. Not bumped this wave (no schema changes made). See `schema-migration-and-data-safety-report.md` for the full migration-safety audit.

## Financial contract version
`CALCULATION_VERSION = "retail-pricing-v2-wave0"` in `products/retail/backend/core/retail/pricing.py` — pre-existing, unchanged this wave. Retail-only.

## Bumping rules for future waves
- PATCH: bug fixes, no schema/contract change.
- MINOR: new features, additive schema changes (migrations only add, never destructively alter).
- MAJOR: breaking schema or financial-contract changes.
- Drop the `-rc.N` suffix only at an explicit, gate-reviewed general-availability decision (Wave 1C or later) — never silently.
- Android `versionCode` must increase by at least 1 on every release build that changes app code, regardless of `versionName`.

## Phase 7 bump (Licensing & Activation Integration)

All four artifact families moved `1.0.0-rc.1` -> `1.0.0-rc.2` at the start of Phase 7 (product-to-Owner licensing integration), per that phase's explicit versioning requirement:

| Product | Platform | Version |
|---|---|---|
| Aura Retail | Windows | 1.0.0-rc.2 |
| Aura Retail | Android | 1.0.0-rc.2 (versionCode 3) |
| Aura Clinic | Windows | 1.0.0-rc.2 |
| Aura Clinic | Android | 1.0.0-rc.2 (versionCode 3) |

Updated in all six canonical sources: `products/retail/backend/config.py`, `products/clinic/backend/config.py`, `android/aura-retail/app/build.gradle`, `android/aura-clinic/app/build.gradle`, `products/retail/packaging/version_info.txt`, `products/clinic/packaging/version_info.txt`. `SCHEMA_VERSION` is intentionally left unbumped by this commit -- Phase 7's Part J introduces new (additive-only) licensing-state tables, which will bump it when that migration actually lands, not preemptively here. Per the bumping rules above, this stays a PATCH-shaped rc bump (`-rc.N` suffix), not a MINOR/MAJOR semver change, matching Phase 7's own explicit target versions rather than the general "additive schema change -> MINOR" rule, since the additive schema change hasn't landed as of this commit.

No existing rc.1 build artifact under `dist/` is overwritten by this bump -- rc.2 artifacts are produced fresh at Part Z/AA and will sit alongside, not replace, the rc.1 files already recorded in `docs/release/wave1b/release-candidate-manifest.md`.

There is currently no dedicated "About" screen in either product's frontend (`products/retail/frontend/`, `products/clinic/frontend/`) -- neither product had one before Phase 7, and `/api/version` had no UI consumer at all (only the Windows launcher's own port-identity check calls it). Rather than build a new standalone About screen purely to satisfy the version-display requirement, Phase 7's Part G "License Status" screen (which needs to show product/version context anyway) is the surface where the current version becomes user-visible for the first time -- tracked in `docs/licensing/phase7/phase7-implementation-plan.md`, not silently dropped.

## Every release artifact must expose (verified this wave)
Product name, product code, version name, version code/build number, schema version, financial contract version (Retail only), release channel (`rc` for this wave), build timestamp, Git commit, environment, architecture — see `release-candidate-manifest.md` for the generated instance of this per artifact.
