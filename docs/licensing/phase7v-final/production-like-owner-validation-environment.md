# Phase 7V-F — Production-Like Owner Validation Environment (Part C)

## Configuration

Real local Owner instance, `OWNER_ENV=testing` config profile (real Postgres, real security
middleware — "testing" here means "point at the test database," not "disable security"), started
as a genuine `werkzeug.serving.make_server` HTTP server on `127.0.0.1:19001` (localhost only, no
public/LAN exposure). Real active Ed25519 signing key generated via the existing Phase 6
`generate_signing_key`/`activate_signing_key` flow (`owner/app/licensing_service/signing.py`) —
key id recorded in `final-trust-anchor-evidence.md`. No debug mode, no insecure TLS bypass (Owner
itself doesn't have one — that gap was Windows/Android product-side, closed in Phase 7V), no
default secrets, no development license bypass.

## Short signed validation policy

A real `OfflinePolicy` row (`policy_code=phase7vf-short-validation`) was created and assigned to
both synthetic licenses via the existing `assign_policy()` service function — a genuine
Owner-signed policy embedded in the assertion payload, not a client-side override:

```
check_in_interval_seconds: 30
retry_interval_seconds: 15
offline_grace_seconds: 90
warning_start_seconds: 60
hard_expiry_behavior: RESTRICT_COMMERCIAL_FEATURES_PRESERVE_DATA
clock_rollback_tolerance_seconds: 300
assertion_refresh_threshold_seconds: 3600
emergency_extension_allowed: false
```

No product-side fake clock was used anywhere — every WARNING/GRACE/RESTRICTED transition observed
this session came from real elapsed wall-clock/monotonic time against this real signed policy
(see `android-physical-offline-state-evidence.md` and `windows-live-restricted-mode-closure.md`).

## Synthetic customers and licenses

Two synthetic customers (one per product), each with an active subscription and an active license,
`allowed_platforms=WINDOWS,ANDROID`, real license keys issued via the existing
`issue_license_key()` service (never persisted in plaintext, matching Phase 6/7 guarantees). A
second round of licenses was issued mid-session after the first round's `device_limit=2` was
exhausted by repeated test activations — `device_limit=10` on the second round, still entirely
synthetic, still real signed licenses.

## Real elapsed-time testing, not simulated

Every offline/warning/restricted observation was produced by: activating a real product build
against this real Owner, then killing the real Owner process (`taskkill`/`kill -9`, a genuine
`ECONNREFUSED` on the next request, not a mock), waiting real wall-clock seconds, then issuing a
real check-in HTTP request and reading the real resulting state. See
`windows-live-restricted-mode-closure.md` for the exact sequence and timings.

## No public exposure

Owner bound to `127.0.0.1` only, reachable from the Android device solely via `adb reverse
tcp:19001 tcp:19001` (a local USB-tunneled loopback, not a network-exposed port) — matching the
spec's "localhost or controlled LAN exposure only, no public internet exposure" requirement.
