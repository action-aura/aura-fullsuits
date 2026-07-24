# Phase 7V — Android Kotlin/Embedded-Python Authority Boundary (Part N)

## Status: physical device portion NOT VERIFIED (no device connected — see Part J)

No physical Android device was available in this environment at any point during Phase 7V (`adb
devices -l` returned an empty list at session start and on recheck immediately before Part H). The
spec requires physical-device proof for this part; that specific proof was not obtained and is not
claimed. What follows is what *was* verified — real, executed test evidence at the source/protocol
level, which is where this authority boundary is actually implemented and enforced (the physical
device would exercise the identical code path, not different code).

## What the authority boundary actually is

Android's Kotlin layer (`LicensingCoordinator.kt`) calls Owner directly, then hands the **raw**
Owner HTTP response to the embedded Python process via a localhost-only `/_internal/sync-*` route,
gated by a random per-process shared secret. Python independently re-verifies the Ed25519 signature
against the trust anchor before accepting anything Kotlin reports — "never trust a Kotlin boolean"
is the design principle (Phase 7 Part F/I).

## Real, executed evidence (`commercial_runtime/licensing_contracts/tests/`)

`test_internal_sync_routes.py` (6/6 passing):
- **Missing internal secret** → route rejects (`test_internal_sync_activation_rejects_missing_secret`).
- **Wrong internal secret** → route rejects (`test_internal_sync_activation_rejects_wrong_secret`).
- **Correct secret** → succeeds (`test_internal_sync_activation_succeeds_with_correct_secret`).
- **Forged/unsigned assertion, even with the correct internal secret** → rejected
  (`test_internal_sync_activation_rejects_forged_assertion_even_with_correct_secret`) — this is the
  core proof that possessing the localhost secret alone (which a compromised Kotlin layer would
  have) is not sufficient; Python's own Ed25519 verification against the trust anchor is the real
  gate.
- **No secret configured** → the sync routes are not even registered
  (`test_internal_routes_not_registered_without_shared_secret`).
- Deactivation sync path independently verified too.

`test_assertion_verifier.py` (15/15 passing) — covers every forged/malformed-assertion scenario the
spec lists:
- Unknown signing key rejected.
- Tampered payload rejected.
- Expired / not-yet-valid assertion rejected.
- **Wrong product** rejected (`test_wrong_product_rejected`).
- **Wrong platform** rejected (`test_wrong_platform_rejected`).
- **Wrong installation** rejected (`test_wrong_installation_rejected`).
- **Wrong device fingerprint** rejected (`test_wrong_device_fingerprint_rejected`) — the "assertion
  for wrong device" scenario.
- Unsupported algorithm rejected.
- Forbidden field / forbidden marker inside an allowed field rejected (injection-style attempts).
- Malformed envelope (missing field) rejected — the "malformed assertion" scenario.
- Unsafe `hard_expiry_behavior` value rejected.
- Naive (timezone-less) dates rejected.

"Older valid assertion attempting to downgrade newer state": `state_repository.py`'s persistence
model always replaces state atomically from a freshly-verified assertion; there is no code path
that compares assertion recency and prefers an older one, and `test_state_repository.py` (9/9
passing) exercises the persistence layer directly. Not the same as a dedicated
downgrade-attack test, so this specific scenario is marked **NOT SEPARATELY VERIFIED** below rather
than claimed as covered.

## Internal shared secret properties (verified by source inspection + the tests above)

- **Changes on process restart**: `ServerBootstrap.kt`'s `internalSharedSecret` is a `lazy val`
  generated fresh via `SecureRandom` each process start, never read from a stored value.
- **Never logged**: grepped Kotlin sync-path code for any log statement including the secret —
  none found.
- **Never persisted**: it lives only in the `ServerBootstrap` object's memory for the process
  lifetime; no file write of it exists anywhere in `android/aura-*/app/src/main/java/`.
- **Never sent to Owner**: it is used exclusively on the `/_internal/sync-*` localhost routes
  between Kotlin and the embedded Python; Owner's HTTP client (`OwnerClient.kt`) never includes it
  in any Owner-bound request.
- **Not included in APK assets**: it's generated at runtime, not embedded at build time; confirmed
  no `internalSharedSecret`-shaped constant exists in `BuildConfig` or `assets/`.

## What remains genuinely unverified (honest gap)

- Live device behavior under an actually-compromised/malicious Kotlin layer (a modified/hooked APK
  attempting to feed the embedded Python a forged sync request over the real Android IPC/network
  stack, on real hardware) — the spec's literal "physically prove" instruction. This requires a
  connected device (Part J) and was not performed.
- The specific "older valid assertion attempting to downgrade newer state" replay scenario has no
  dedicated test; the persistence model structurally prevents it (always-replace-atomically), but
  this reasoning was not exercised by a targeted test in this session.

## Verdict

Authority-boundary logic: **PASS at the source/protocol level** (21 directly relevant tests, all
passing, covering every listed forgery/tampering scenario except the downgrade-replay case).
Physical-device proof of the same boundary: **NOT VERIFIED** — no device connected. This gap alone
is sufficient, per the governing spec's Definition of Done, to withhold the final Phase 7V closing
tag until a device is connected and this part is completed for real.
