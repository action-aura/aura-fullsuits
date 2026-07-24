# Phase 7V — Scope and Baseline

## What Phase 7V is

Phase 7 (`aura-product-licensing-integration-phase7-complete`, commit `6ef6265`) delivered a
functionally complete, live-tested product-to-Owner licensing integration for Windows and
Android, both products. Phase 7V does not extend that functionality. It closes the
**release-validation gap**: production-signed Android artifacts, Windows installers built from
the rc.2 source, a real rc.1 → rc.2 upgrade proof on both platforms, physical-device proof, and
a full toolchain/security closure so the existing implementation can be evaluated for release
readiness with evidence instead of assumption.

## Baseline verified before any change

- Tag `aura-product-licensing-integration-phase7-complete` exists and points at commit
  `6ef6265e3c2c91a763572bd7e20f412e98b70d07`.
- `git show --no-patch` on that tag confirms the tag message and target commit match the Phase 7
  closing summary (secure product-to-owner integration, device-bound activation, signed
  assertion verification, offline license state, safe local enforcement).
- Branch: `master`. HEAD == the Phase 7 tag commit. `git status --short` clean (no uncommitted
  work at Phase 7V start).
- All prior checkpoint tags present and unmodified (retail-extraction-phase2-complete through
  aura-owner-licensing-activation-phase6-complete plus the Phase 7 tag itself) — 13 tags total.
- Original `AuraEnterprise` repository: not touched by this session (no commands executed against
  that path).

## Product version state at baseline

- `products/clinic/backend/config.py` / `products/retail/backend/config.py`: `APP_VERSION =
  '1.0.0-rc.2'`.
- `android/aura-clinic/app/build.gradle` / `android/aura-retail/app/build.gradle`: `versionCode 3`,
  `versionName "1.0.0-rc.2"`.
- rc.1 Windows installers still present at `dist/installers/AuraClinic-Setup-1.0.0-rc.1.exe` and
  `AuraRetail-Setup-1.0.0-rc.1.exe` — needed as the starting point for the Part F upgrade test.
- rc.1 Android signed artifacts are not present as files in this checkout (only their SHA-256 and
  certificate fingerprints survive, recorded in
  `docs/release/wave1b/release-candidate-manifest.md`). Certificate continuity (Part I) is
  therefore verified by comparing the **rc.2 signer certificate fingerprint** against the
  **fingerprint recorded in that Wave 1B manifest**, not by keeping a byte-identical rc.1 APK on
  disk.

## Scope boundary

Phase 7V performs release-validation activities only: toolchain restoration, real builds, real
signing, real installs/upgrades, physical-device proof, artifact security inspection, and
evidence documentation. It does not add features, does not change capability policy, does not
change financial logic, and does not begin Phase 8, VPS deployment, or any of the explicitly
prohibited items repeated in the governing spec. See `phase7-final-release-validation-decision.md`
for the closing gate verdicts.
