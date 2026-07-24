# Phase 7V-F — Android Logcat and Traffic Privacy (Part N)

## Status: NOT VERIFIED — device disconnected before a Logcat capture pass could be run

## Equivalent evidence gathered this session

Every licensing HTTP exchange this session (Windows, real Owner, real network traffic — activate,
check-in, offline retry/backoff, deactivate) was inspected as it happened (curl request/response
bodies visible in full). No patient data, no invoice/payment details, no sale-line details, no
stock quantities, no full license key beyond the single activation call, and no private key
material appeared in any exchange — matching the Phase 7 design guarantee re-confirmed in
`docs/licensing/phase7v/release-artifact-security-inspection.md`. The licensing protocol payloads
observed this session contained exactly the allowlisted fields (contract version, request/
correlation IDs, timestamps, nonces, product code, platform, installation ID, device fingerprint,
assertion reference, signature) — nothing more.

This is not a substitute for a real Android Logcat capture (which would additionally need to rule
out anything the Kotlin layer itself might log, e.g. around the `/_internal/sync-*` hand-off, that
a pure product-to-Owner traffic inspection cannot see).

## Verdict

**NOT VERIFIED** (physical Logcat capture). **PASS** (product-to-Owner traffic content, inspected
live this session on the equivalent Windows path).
