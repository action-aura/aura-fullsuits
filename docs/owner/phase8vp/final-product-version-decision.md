# Phase 8V-P — Final Product Version Decision (Part C)

## Decision: 1.0.0-rc.2 -> 1.0.0-rc.3, all four artifact families

Full rationale, canonical-source list, and precedent recorded in
`docs/release/versioning-policy.md`'s new "Phase 8V-P bump" section (additive, does not erase the
rc.2 entry). Summary: shipped commercial-runtime code changed since rc.2 (the `ActivationPending`
handling path, the PENDING-activation product UX on both platforms, and this session's own
`ALLOWED_PAYLOAD_FIELDS`/trust-anchor fixes) — a real behavior change every build embeds, not a
documentation-only change, so per the existing policy's own rule this is at minimum a PATCH-shaped
`rc.N` bump, following Phase 7's own precedent exactly.

## What was updated (all six canonical sources)

| Source | Old | New |
|---|---|---|
| `products/clinic/backend/config.py::APP_VERSION` | `1.0.0-rc.2` | `1.0.0-rc.3` |
| `products/retail/backend/config.py::APP_VERSION` | `1.0.0-rc.2` | `1.0.0-rc.3` |
| `products/clinic/packaging/version_info.txt` (ProductVersion) | `1.0.0-rc.2` | `1.0.0-rc.3` |
| `products/retail/packaging/version_info.txt` (ProductVersion) | `1.0.0-rc.2` | `1.0.0-rc.3` |
| `products/clinic/packaging/aura_clinic_setup.iss` (AppVersion) | `1.0.0-rc.2` | `1.0.0-rc.3` |
| `products/retail/packaging/aura_retail_setup.iss` (AppVersion) | `1.0.0-rc.2` | `1.0.0-rc.3` |
| `android/aura-clinic/app/build.gradle` | `versionName "1.0.0-rc.2"` / `versionCode 3` | `versionName "1.0.0-rc.3"` / `versionCode 4` |
| `android/aura-retail/app/build.gradle` | `versionName "1.0.0-rc.2"` / `versionCode 3` | `versionName "1.0.0-rc.3"` / `versionCode 4` |

`FileVersion` (the Windows numeric-only resource field) stays `1.0.0.0` — unchanged, matching the
rc.2 bump's own precedent (that field has never carried the `-rc.N` suffix in this project).

## Android build state

Version *identity* is updated at the source level (needed regardless of build timing, so every
canonical source stays in sync per policy). The actual Android build (compiling, signing, producing
a real rc.3 APK/AAB) is deferred to the session with physical device access, per this session's own
explicit user decision — see `physical-device-readiness.md`. No `dist/` history for Android exists
yet in this repo to preserve or overwrite either way.

## Windows build state

Built fresh this session — see `final-artifact-build-report.md`. Existing `dist/*-Setup-1.0.0-rc.1.exe`
and `dist/*-Setup-1.0.0-rc.2.exe` files are untouched; rc.3 artifacts sit alongside them.

## No dedicated About screen (unchanged from Phase 7's own note)

Still true — version surfaces through `/api/version` and the License Status screen, same as
documented in the rc.2 bump. Not revisited this phase (no UI change requested or needed for version
display specifically).
