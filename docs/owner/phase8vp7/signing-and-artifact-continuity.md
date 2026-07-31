# Phase 8V-P7 — Signing, Package, and Artifact Continuity

## Android

| Field | Clinic | Retail |
|---|---|---|
| Package ID | `com.actionaura.clinic` (unchanged) | `com.actionaura.retail` (unchanged) |
| versionName | `1.0.0-rc.4` | `1.0.0-rc.4` |
| versionCode | `5` | `5` |
| APK SHA-256 | `7f5de48109fe7dd3446101d3d486cdac42cea244b22a1c13e172dcd483bd4260` | `55f7a4a015f365f9b117dfc5cef6099d9cb341c50d4c424d20325a1d4024c318` |
| APK size | 54,159,013 bytes | 67,753,281 bytes |
| Certificate SHA-256 | `35508048cee7776ca94a45a9f04c6a870edf98729429482a8ad44e89dd0bbbf2` -- **exact match** to historical identity (Phase 8V-P2 onward) | `cae6b18450c14a71eba47545e5b3ba52089eef0397d5881f4c1dc7d5a4b7d32d` -- **exact match** to historical identity (Phase 8V-P2 onward) |
| Debuggable | No | No |
| AAB | `app/build/outputs/bundle/release/app-release.aab` (built, `signReleaseBundle` task ran) | same |
| Owner URL baked in | `http://127.0.0.1:5551/api/licensing/v1` | `http://127.0.0.1:5551/api/licensing/v1` |
| Upgrade continuity | `firstInstallTime` unchanged (`2026-07-20 04:01:56`); `installation_id c7150980-...` unchanged across upgrade | `firstInstallTime` unchanged (`2026-07-20 04:01:51`); `installation_id e77bd448-...` unchanged across upgrade |

Both verified via `apksigner verify --print-certs` (full SHA-256, not an abbreviated prefix) and
`dumpsys package` (`versionName`/`versionCode`/`firstInstallTime`/`lastUpdateTime`), both directly on
the real physical device after a real `adb install -r` in-place upgrade -- not inferred.

## Windows

| Field | Clinic | Retail |
|---|---|---|
| Product version (embedded) | `1.0.0-rc.4` (confirmed live via `/api/version` after launch) | `1.0.0-rc.4` (confirmed live via `/api/version` after launch) |
| Exe SHA-256 | `daec4bf461529cbdd3cd7c1a6e0275cc9600428fa3a47467b852a054e87500b7` | `35c37a0b1b90e7842acc7e6c3713e85a120ac2c09e79023f1d2bbd435c685d73` |
| Exe size | 5,069,065 bytes | 5,797,504 bytes |
| Authenticode signing | **Not signed** (`Get-AuthenticodeSignature` -> `NotSigned`) -- a real, pre-existing project condition, not a regression introduced this session; no code-signing certificate has been established for Windows builds at any point in this project's history (confirmed: the same `NotSigned` status held for the pre-fix `dist/AuraRetail/AuraRetail.exe` built 2026-07-27, per no contrary evidence in any prior session's artifact report) | Same, same disposition |
| Confirmed embedded fix | `PyInstaller` build log line "Building because ...assertion_verifier.py changed" for both -- direct, positive confirmation the corrected `commercial_runtime` was actually re-frozen into this build, not assumed from a successful build alone | Same |

## Disposition

Android identity and signing continuity: full PASS, real evidence, both products. Windows: version and
embedded-fix continuity confirmed real; Authenticode signing was never established for this project and
remains an open item outside this session's scope (not named as a Phase 8 gate by any prior session's
governing spec either -- disclosed here for completeness, not newly discovered as a regression).
