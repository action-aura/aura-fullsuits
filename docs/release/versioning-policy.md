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

## Every release artifact must expose (verified this wave)
Product name, product code, version name, version code/build number, schema version, financial contract version (Retail only), release channel (`rc` for this wave), build timestamp, Git commit, environment, architecture — see `release-candidate-manifest.md` for the generated instance of this per artifact.
