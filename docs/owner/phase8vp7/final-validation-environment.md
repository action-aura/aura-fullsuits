# Phase 8V-P7 — Final Validation Environment

## Owner

Started fresh this session (no stale validation-only process found listening on 5551/0.0.0.0/prior
ports before starting -- checked via `Get-NetTCPConnection -State Listen`, confirmed empty). Real
PostgreSQL, current migrations, debug disabled, external API enabled, real Ed25519 signing key, current
trust anchor, current RBAC seeds, synthetic data only. Wrapped with the same real wire-capture
middleware used in Phase 8V-P5/P6 (`owner_capture_server.py`), redacting `license_key`/`signature`/
`password`/`totp_secret`/`recovery_code` in-memory before any disk write.

`flask commercial preflight` -> `ok: true`, confirmed via direct JSON field extraction, not assumed
from the absence of an error.

## Connectivity

`adb reverse --remove-all` then `adb reverse tcp:5551 tcp:5551`, verified via `adb reverse --list`
before physical work began. Re-verified again after the device's later reconnection following the
mid-session disconnection.

## Confirmed via real traffic (not just route existence)

- `service-info`: real 200 OK.
- Authenticated check-in: real 200 OK, real signed assertion returned, for both products, both before
  and after their respective rc.4 upgrades.
- Activation: real success and real, correctly-bounded rejections (see `raw-wire-final.md`) via six
  real exchanges from the Windows multi-instance work.

## Mid-session device disconnection

Documented in full in `phase8vp7-baseline.md` and the individual scenario docs it affects. The Owner
environment itself remained stable and correctly configured throughout -- the disconnection was a
physical Android/USB event, not an Owner-side or network-configuration issue (confirmed: Windows
product activity against the same Owner instance continued working normally throughout the Android
outage).
