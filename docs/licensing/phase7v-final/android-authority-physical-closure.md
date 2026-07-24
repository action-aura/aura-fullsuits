# Phase 7V-F — Android Kotlin/Embedded-Python Authority Boundary (Part M)

## Status: NOT VERIFIED — physical device proof not completed (device disconnected)

No change from Phase 7V's own assessment of this part: the source/protocol-level proof remains the
strongest evidence available (21 directly relevant tests in
`test_internal_sync_routes.py`/`test_assertion_verifier.py`, all passing, covering every listed
forgery/tampering scenario — missing secret, wrong secret, forged/unsigned assertion, wrong
product/platform/installation/device-fingerprint, malformed envelope). See Phase 7V's
`docs/licensing/phase7v/android-authority-boundary-physical-report.md` for the full breakdown,
unchanged this session.

## Internal shared-secret properties (re-confirmed by source inspection this session)

No changes were made to `ServerBootstrap.kt`'s `internalSharedSecret` generation
(`SecureRandom`-backed, process-lifetime only, never persisted, never logged, never sent to Owner,
not in `BuildConfig`) — the app.py fixes this session (lazy Windows-only import, trusted-time
anchor caching) did not touch this code path at all.

## Verdict

**NOT VERIFIED** (physical). **PASS** (source/protocol level, unchanged from Phase 7V, re-confirmed
not regressed by this session's changes via the full 46-file/585-test regression rerun).
