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

## Android `versionCode`

`versionCode` is a separate value from `versionName`/the semver above, with its own rule, because Google Play enforces it directly: **an upload whose `versionCode` is not strictly greater than the last one Play accepted for that application is refused outright**, at upload time, regardless of what `versionName` says. A release-candidate string can move as often as it likes without Play caring; `versionCode` standing still is what actually blocks a release.

**The rule is: `versionCode` is a plain integer, bumped by hand on every release, kept equal between `android/aura-retail/app/build.gradle` and `android/aura-clinic/app/build.gradle`.** It is deliberately **not** derived from `versionName` by any formula. A formula is attractive — it removes a manual step — and is a trap for this scheme specifically: `1.0.0-rc.6` → `1.0.0-rc.7` → `1.0.0` → `1.0.1` must all map to strictly increasing integers, `-rc.N` is dropped entirely at GA (see the bullet above), and any encoding scheme tying the integer to the string's shape breaks the moment that shape changes — which is exactly the point in this project's life where a release is happening. A hand-bumped integer with no encoded meaning has no shape to break.

Pinned by `products/retail/tests/retail_release_version_consistency_test.py`'s `test_android_version_codes_agree_between_products` (the two modules must agree) and `test_android_version_code_is_a_positive_integer`. What that test does **not** and cannot check: that the new value is actually greater than the last one really published to Play — that is a fact about release history, not about a single checkout of these two files, and Play enforces it itself at upload time.

Found stuck during this same pass: `versionName` had already moved `1.0.0-rc.6` → `1.0.0-rc.7` in both modules while `versionCode` sat unmoved at `7` (its rc.6-era value) in both — nothing had been checking it, which is the gap this section and the two tests above close. Bumped to `8` in both modules for this rc.7 release.

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

## Phase 8V-P bump (Commercial Operations UI + PENDING-activation product UX)

All four artifact families moved `1.0.0-rc.2` -> `1.0.0-rc.3` at the start of Phase 8V-P's physical
validation pass, per the "if current product artifacts differ from the final Phase 7 rc.2 artifacts
in shipped code or contracts, use the next release-candidate version" rule above:

| Product | Platform | Version |
|---|---|---|
| Aura Retail | Windows | 1.0.0-rc.3 |
| Aura Retail | Android | 1.0.0-rc.3 (versionCode 4) |
| Aura Clinic | Windows | 1.0.0-rc.3 |
| Aura Clinic | Android | 1.0.0-rc.3 (versionCode 4) |

Shipped code genuinely changed since rc.2: Phase 8 Milestone 7 added the `ActivationPending`
handling path (`commercial_runtime/licensing_contracts/activation.py`) and a new user-facing
"awaiting manual approval" message on both Android (`LicensingScreen.kt`, both products) and Windows
(`licensing.js`, both products); Phase 8V-P's own security review additionally fixed a real
assertion-verification allowlist gap in `commercial_runtime` that ships inside every product build.
Both are real behavior changes to code every build embeds, not documentation-only changes, so per the
bumping rules this is at minimum a PATCH-shaped rc bump — following Phase 7's own precedent of using
the plain `rc.N` bump for a licensing-related shipped-code change rather than a MINOR/MAJOR jump.

`SCHEMA_VERSION`/`LICENSING_SCHEMA_VERSION` are unchanged at `1` — every new Phase 8 field (Owner-side
`owner_*` tables, and the nine new assertion payload fields) lives in already-existing JSON/JSONB
columns on both sides, never a new SQL column in the local product schema.

Updated in all six canonical sources: `products/retail/backend/config.py`,
`products/clinic/backend/config.py`, `android/aura-retail/app/build.gradle`,
`android/aura-clinic/app/build.gradle`, `products/retail/packaging/version_info.txt`,
`products/clinic/packaging/version_info.txt`, plus both `.iss` installer scripts (this document's own
rc.2 entry notes that file was missed once already — not missed this time). No `dist/` artifact from
rc.2 is overwritten; rc.3 artifacts are produced fresh alongside them.

The Android `versionCode`/`versionName` bump landed as a source-only change in this pass (no
physical Android device was available in this session to build-and-validate a new APK/AAB against —
see `docs/owner/phase8vp/physical-device-readiness.md`); the Windows side was built, installed, and
exercised for real this same pass — see `docs/owner/phase8vp/final-artifact-build-report.md`.

## Every release artifact must expose (verified this wave)
Product name, product code, version name, version code/build number, schema version, financial contract version (Retail only), release channel (`rc` for this wave), build timestamp, Git commit, environment, architecture — see `release-candidate-manifest.md` for the generated instance of this per artifact.
