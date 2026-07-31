# Phase 8V-P9 Part Q — Final Artifact & Manifest Audit

## Version alignment (rc.4 -> rc.5, versionCode 5 -> 6)

All 8 canonical sources bumped together (see `git diff` for this session): both Android
`build.gradle` files, both product `config.py` `APP_VERSION`, both `packaging/version_info.txt`
`ProductVersion`, both `packaging/*.iss` `AppVersion`. No rc.4 (or earlier) artifact/doc overwritten --
rc.5 was built fresh alongside them.

## Trigger

The Part K stale-assertion fix (`commercial_runtime/licensing_contracts/checkin_scheduler.py`,
`events.py`) is embedded in every Android and Windows artifact (Android via `stagedPythonSources`,
Windows via PyInstaller freeze) -- confirmed live in both PyInstaller build logs ("Building because
...checkin_scheduler.py changed").

## Final artifacts (this session, real, SHA-256)

| Artifact | SHA-256 |
|---|---|
| `android/aura-retail/.../app-release.apk` | `800e60091b6cd320bd65abe0298ff73bb2a455756c4eee2ec3a1266d679daebd` |
| `android/aura-retail/.../app-release.aab` | `9afabd7140020594cb6c658a4845e2b2dd815c05ea92387085621fede0be2bab` |
| `android/aura-clinic/.../app-release.apk` | `1665e7c7973ac65b9479efd04463aea1ca17c396aa3047c268c119f7d8b006a2` |
| `android/aura-clinic/.../app-release.aab` | `956a99ec6b44ced7b77a6c7cb26e6dc91ac93bf432e9d1f4dc6092181e38540e` |
| `dist/AuraRetail/AuraRetail.exe` | `60741cb7c56a41fb1cd8b03c370c907e4a3cc1957571c28f66a62f76639a52fb` |
| `dist/AuraClinic/AuraClinic.exe` | `170c90275a0bfb9c19674b1565e9cc2124b512b5dba72968a0152a20d250e0e9` |

## Signing continuity (no key replacement -- verified byte-for-byte against rc.4)

Via `apksigner verify --print-certs` on the real rc.5 APKs:

- Retail: `CN=Action Aura, OU=Aura Retail, ...`, SHA-256 `cae6b18450c14a71eba47545e5b3ba52089eef0397d5881f4c1dc7d5a4b7d32d` -- identical to the rc.4 cert recorded in Phase 8V-P7.
- Clinic: `CN=Action Aura, OU=Aura Clinic, ...`, SHA-256 `35508048cee7776ca94a45a9f04c6a870edf98729429482a8ad44e89dd0bbbf2` -- identical to the rc.4 cert recorded in Phase 8V-P7.

## Real installation confirmation

- Physical Infinix X6528: both apps upgraded in place via `adb install -r` (real device, real upgrade,
  not reinstall). `dumpsys package` confirmed `versionName 1.0.0-rc.4 -> 1.0.0-rc.5` for both
  packages immediately after install. On-device Settings -> Licensing -> About screen (real UI,
  captured via `uiautomator dump`) independently shows `Version 1.0.0-rc.5`.
- Windows: `dist/AuraRetail/AuraRetail.exe` and `dist/AuraClinic/AuraClinic.exe` real-launched multiple
  times this session (instances C, D, Block1, Block2) via their real `AURA_APP_DATA` directories;
  `/api/version` confirmed `"app_version":"1.0.0-rc.5"` live on a running instance.

## No history overwritten

rc.4 (and earlier rc.1-rc.3) artifacts and their manifests remain untouched on disk and in prior
phase docs; rc.5 was produced fresh alongside them, matching this project's established versioning
policy and past practice (`docs/release/versioning-policy.md`).
