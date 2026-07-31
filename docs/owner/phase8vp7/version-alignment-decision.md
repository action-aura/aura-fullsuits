# Phase 8V-P7 — Version Alignment Decision

## Decision: bump all four artifact families `1.0.0-rc.3 -> 1.0.0-rc.4`, Android `versionCode 4 -> 5`

## Canonical policy consulted first (per this session's own instruction)

`docs/release/versioning-policy.md` is a real, established, project-canonical policy, already used
consistently across Phase 7 (`rc.1 -> rc.2`) and Phase 8V-P (`rc.2 -> rc.3`). Its own rule: "if current
product artifacts differ from the final [prior] rc.N artifacts in shipped code or contracts, use the
next release-candidate version." The commercial-enforcement fix (Phase 8V-P6, commit `45fe6b8`) is
exactly this case -- shipped licensing-decision code changed. `rc.4` is the correct next value per the
project's own documented rule, matching this session's own suggested value exactly.

## Six-plus-two canonical sources (per the policy document's own list, kept in sync manually)

1. `products/retail/backend/config.py` (`APP_VERSION`)
2. `products/clinic/backend/config.py` (`APP_VERSION`)
3. `android/aura-retail/app/build.gradle` (`versionName`/`versionCode`)
4. `android/aura-clinic/app/build.gradle` (`versionName`/`versionCode`)
5. `products/retail/packaging/version_info.txt`
6. `products/clinic/packaging/version_info.txt`
7. `products/retail/packaging/aura_retail_setup.iss` (`#define AppVersion`)
8. `products/clinic/packaging/aura_clinic_setup.iss` (`#define AppVersion`)

All eight updated together in one commit (see git history), consistent with the policy document's own
past practice.

## versionCode rule applied

Policy: "Android `versionCode` must increase by at least 1 on every release build that changes app
code, regardless of `versionName`." App code did not change (only the shared `commercial_runtime`
payload staged into the build changed) -- but the bumping rule's own wording ("changes app code")
is read broadly here to include the embedded commercial-runtime payload, since it is real, executable
code shipped inside the APK that materially changes real product behavior (RESTRICTED enforcement).
`versionCode 4 -> 5` applied to both products for this reason, not merely because a version string
changed.

## No history overwritten

rc.3 artifacts and their manifests remain on disk/in docs untouched; rc.4 artifacts are produced fresh
alongside them (see `final-build-report.md`).

## SCHEMA_VERSION / CALCULATION_VERSION

Unchanged -- no schema or financial-calculation-contract change this session, consistent with the
policy's own bumping rules (PATCH-shaped rc bump only).
