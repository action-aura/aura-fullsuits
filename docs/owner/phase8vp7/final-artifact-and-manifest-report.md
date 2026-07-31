# Phase 8V-P7 — Final Artifact and Manifest Report

## All four Android/Windows artifact families rebuilt from final HEAD, confirmed carrying the fix

| Product | Platform | Version | File | SHA-256 | Confirmed correct evaluator embedded |
|---|---|---|---|---|---|
| Clinic | Android APK | rc.4 / versionCode 5 | `app-release.apk` | `7f5de48109fe7dd3446101d3d486cdac42cea244b22a1c13e172dcd483bd4260` | Yes -- physically smoke-tested on-device (Scenario 3 smoke check, real RESTRICTED reproduced post-upgrade) |
| Clinic | Android AAB | rc.4 / versionCode 5 | `app-release.aab` | (built, `signReleaseBundle` ran) | Same build, same source |
| Retail | Android APK | rc.4 / versionCode 5 | `app-release.apk` | `55f7a4a015f365f9b117dfc5cef6099d9cb341c50d4c424d20325a1d4024c318` | Yes -- physically tested on-device (Scenario 2's real RESTRICTED proof, pre-disconnection) |
| Retail | Android AAB | rc.4 / versionCode 5 | `app-release.aab` | (built, `signReleaseBundle` ran) | Same build, same source |
| Clinic | Windows exe | rc.4 | `AuraClinic.exe` | `daec4bf461529cbdd3cd7c1a6e0275cc9600428fa3a47467b852a054e87500b7` | Build log confirms "Building because ...assertion_verifier.py changed"; not physically exercised this session (no Clinic-Windows scenario planned) |
| Retail | Windows exe | rc.4 | `AuraRetail.exe` | `35c37a0b1b90e7842acc7e6c3713e85a120ac2c09e79023f1d2bbd435c685d73` | Yes -- physically exercised via 4 real running instances (B/C/D/E), real activations, real Scenario 6/7 mechanics all executed against this exact build |

## Verification performed on every artifact

- Built from final HEAD (confirmed: `git status` clean before each build, `assertion_verifier.py`
  cited as the changed file triggering each PyInstaller PYZ rebuild).
- Correct product identity (`product_code` confirmed live via `/api/version` on every real running
  instance).
- Correct package/application ID (Android: unchanged, confirmed via `dumpsys package`).
- Correct certificate/signing identity (Android: full SHA-256, exact match to historical identity, see
  `signing-and-artifact-continuity.md`; Windows: no Authenticode signing established for this project,
  pre-existing condition, disclosed not newly discovered).
- Non-debuggable Android release (confirmed, no `application-debuggable` manifest line).
- Correct Owner URL, `/api/licensing/v1` exactly once (Android: baked into `BuildConfig` at compile
  time; Windows: resolved from `AURA_OWNER_LICENSING_URL` env var at runtime, confirmed live).
- Corrected commercial runtime embedded, no stale evaluator (positive confirmation for 3 of 4 artifacts
  via physical exercise; Clinic Windows confirmed via build-log evidence only).
- No debug endpoint, no trust-all, no fake paid/free state, no fake clock, no backdoor: unchanged from
  every prior session's clean finding, no new surface introduced this session.
- No secrets, no synthetic customer data embedded in any artifact (only real, synthetic *validation*
  data exists in the Owner database and on-device app data, never inside the artifact binaries
  themselves).
- No Phase 9 configuration.

## Manifest update

Additive only -- prior rc.3 artifacts and their checksums (recorded in `docs/owner/phase8vp4/
url-configured-build-report.md` and earlier) remain untouched, on disk and in documentation. This
report is the rc.4 manifest; it does not overwrite or delete the rc.3 one.
