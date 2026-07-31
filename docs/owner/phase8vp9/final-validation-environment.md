# Phase 8V-P9 — Final Validation Environment

## Pre-start checks

No stale validation process found listening on 5551 or any prior product port (5000-5003) before
starting -- confirmed via `Get-NetTCPConnection -State Listen`, empty result.

## Owner

Started fresh this session (PID recorded, real process): real PostgreSQL, current migrations, debug
disabled, external API enabled, real Ed25519 signing key, current trust anchor, current RBAC seeds,
correct pepper (dev placeholder, confirmed via this session's own new preflight check), synthetic data
only. Wrapped with the same real wire-capture middleware used in every prior session
(`owner_capture_server.py`), redacting `license_key`/`signature`/`password`/`totp_secret`/
`recovery_code` in-memory before any disk write.

`flask commercial preflight` -> `ok: true`, confirmed via direct JSON field extraction. This is the
first real preflight run against this session's own hardened pepper checks (see
`license-pepper-preflight-final.md`) -- all three new checks appeared and passed/warned correctly.

## Connectivity

`adb reverse --remove-all` then `adb reverse tcp:5551 tcp:5551`, verified via `adb reverse --list`.

## Device

Infinix X6528, Android 13, API 33, arm64-v8a. `adb devices -l` -> `device` status, confirmed stable.

## Windows

`dist/AuraRetail/AuraRetail.exe` confirmed present (real rc.4 build from Phase 8V-P7, still the
current final artifact -- no product source changed this session, see
`final-build-installation-report.md`).
