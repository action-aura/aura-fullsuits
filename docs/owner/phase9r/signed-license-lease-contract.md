# Phase 9R — Signed Offline License Lease Contract (M9)

## Disposition: validated against existing Phase 6/7 infrastructure, not rebuilt

M9 explicitly allows "implement or validate." Auditing the existing runtime
(`owner/app/licensing_service/assertions.py`, `signing.py`) against every
M9 requirement shows the signed-lease system Phase 6/7 already built
satisfies this milestone. Phase 9R's job here is real network-condition
re-verification in M20/M21 (blocked on infrastructure), not redesign.

## Bounded claims — audited field-by-field

`build_assertion_payload()` (`owner/app/licensing_service/assertions.py:37`)
produces exactly the bounded, license/installation-scoped claim set M9
requires — no business data, no operational data:

| M9-required field | Actual field | Evidence |
|---|---|---|
| License identifier | `license_public_id` | assertions.py:47 |
| Installation identifier | `installation_public_id` | assertions.py:48 |
| Product | `product_code` | assertions.py:45 |
| Platform | `platform` | assertions.py:49 |
| Issue time | `issued_at` | assertions.py:52 |
| Refresh deadline / expiry | `expires_at`, `not_before` | assertions.py:54, 53 |
| Expiry/grace status | `offline_policy`, `commercial_grace_end` (via `resolve_commercial_assertion_fields`) | assertions.py:60, 65 |
| Entitlement version | `entitlements` | assertions.py:59 |
| Policy version | `commercial_policy_version` (Phase 8 Part W) | assertions.py:65 comment |
| Release channel | `release_channel` | assertions.py:50 |
| Key identifier | `signing_key_id` (in the envelope, not the payload) | assertions.py:81 |

No table, no field, no code path here ever includes a product row, an
inventory row, a sale, a patient record, or any operational business data —
confirmed by the same grep-based review used in
`dependency-fix-evidence.md`; the payload is a fixed, explicit dict literal,
not a serialization of an arbitrary model.

## Per-installation credential, not a shared bearer token

`device_key_fingerprint` (assertions.py:57) binds every issued assertion to
the specific device key that requested it. `verify_device_signature()`
(exercised by `test_device_signature_verification_roundtrip`,
`test_device_wrong_public_key_rejected`,
`test_device_malformed_public_key_rejected`,
`test_device_unsupported_algorithm_rejected` — all in
`owner/tests/test_phase6_crypto.py`) means a captured assertion from one
device cannot be replayed as another device's credential; each installation
authenticates with its own key, never a value shared across a license's
devices. This satisfies M9's explicit requirement directly — it was already
a Phase 6 design decision (see `activation-protocol-threat-model.md` threat
#3), not something Phase 9R needed to add.

## Signing, verification, rotation, revocation — real evidence

`owner/app/licensing_service/signing.py` implements a 4-state key lifecycle:
`DRAFT` → `activate_signing_key()` → `ACTIVE` (auto-retiring the previous
active key) → `retire_signing_key()` → `RETIRED`, plus an independent
`REVOKED` state via `revoke_signing_key()`.

`verify_assertion()` (assertions.py:108-143):
- Looks up the key by `signing_key_id` regardless of its current status
  (line 119-120) — a `RETIRED` key still verifies signatures it made while
  active. This **is** the overlap window: rotating the active key does not
  invalidate assertions already issued under the previous key, because
  verification checks the signature against the historical key, not against
  "is this the currently active key."
- Explicitly rejects `REVOKED` keys regardless of signature validity (line
  122-123) — the documented compromised-key response: a revoked key is
  never trusted again for anything, active or historical.
- Validates `not_before`/`expires_at` against the verifier's own clock (line
  138-143) — no separate clock-skew tolerance constant currently
  configured; `ACTIVATION_TIMESTAMP_SKEW_SECONDS` (`app/config.py`) governs
  the *activation request* timestamp check, a related but distinct check in
  `owner/app/licensing_service/activation.py`, not assertion verification
  itself. This is worth a small tolerance addition to assertion
  verification specifically before real remote clients (which will have
  real clock drift) — tracked as a residual item for M20's remote client
  validation, not a redesign.

Real, passing test evidence (`owner/tests/test_phase6_crypto.py`,
`owner/tests/test_phase7_keyset_manifest.py`):

```
test_signing_key_generate_activate_rotate       -- current-key signing/rotation
test_assertion_sign_and_verify                  -- current-key verification
test_manifest_rotation_includes_both_retired_and_active_keys  -- overlap window
test_assertion_unknown_key_id_rejected          -- unknown-key rejection
test_device_malformed_public_key_rejected       -- malformed-key rejection
test_revoked_signing_key_never_trusted          -- compromised-key response
test_assertion_tampering_detected               -- tamper detection
test_assertion_expired_rejected                 -- expiry
test_assertion_not_yet_valid_rejected           -- not-before
test_export_public_keys_never_includes_private_material -- private key never exposed
```

All ten pass in the M0 baseline (`baseline-regression.md`) — real evidence,
not asserted.

## What M9 asks that genuinely isn't proven yet

- **Real network conditions.** Every test above runs against an in-process
  Flask test client, not a real remote client over real network latency,
  real clock drift, or real intermittent connectivity. M20 (remote
  licensing client validation) is where this gets proven for real —
  currently **NOT VERIFIED**, blocked on infrastructure per
  `infrastructure-availability-audit.md`.
- **Explicit clock-skew tolerance on assertion verification itself** — noted
  above as a small, real gap worth closing before M20, not a redesign.

## Bounded offline exposure — stated explicitly, not assumed

Per M9's own instruction: **this system does not claim immediate offline
revocation.** A signed assertion remains valid, and a client holding it
enforces its terms, until `expires_at` — which is bounded by
`ASSERTION_TTL_SECONDS` (default 86400s / 24h, `app/config.py`). If a
license is suspended or revoked at the server, an already-issued assertion
that hasn't yet expired continues to be honored by an offline client until
its own TTL elapses or the client next successfully refreshes online. This
is the real, documented offline-exposure window — at most `ASSERTION_TTL_SECONDS`,
not zero. Shrinking that window (shorter TTL, more frequent required
refresh) is a policy tuning decision for the pilot, not a Phase 9R defect.
