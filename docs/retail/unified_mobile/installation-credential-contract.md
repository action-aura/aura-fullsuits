# Installation Credential Contract (M7.12)

What a successful activation actually returns, and the full lifecycle
of each real credential type.

## What activation actually returns

Not a bearer token, not a refresh token pair — a **signed offline-
usable assertion** (a JWT-like but custom-canonicalized signed JSON
envelope), built by `sign_assertion()`
(`owner/app/licensing_service/assertions.py:72-85`):

```
{ payload, signing_key_id, algorithm, assertion_version, signature }
```

`payload` real fields (allowlisted client-side too —
`ALLOWED_PAYLOAD_FIELDS`,
`commercial_runtime/licensing_contracts/assertion_verifier.py:59-100`):
`assertion_id, issuer, product_code, license_public_id,
installation_public_id, platform, app_version_policy, release_channel,
issued_at, not_before, expires_at, license_status, installation_status,
subscription_status, allowed_device_count, device_key_fingerprint,
entitlements, offline_policy, contract_version,
commercial_policy_version, renewal_status, plan_code, term_start,
term_end, past_due_since, commercial_grace_end, pilot_status,
emergency_extension_id`.

On a manual-approval `PENDING_ACTIVATION` outcome, **no** signed
assertion is issued yet (`_pending_review_response()`,
`activation.py:352-408`) — the credential only exists after staff
approval.

## Credential type 1 — Signed assertion (per-activation/check-in, short-lived, offline-verifiable)

- **Issued**: on successful activation, and re-issued on every
  successful check-in (`checkin.py:109-124`) — this is how the
  assertion is "refreshed."
- **Signed by**: Owner's `SigningKey` (Ed25519, see below) — never by
  the device's own key.
- **Verified by**: the client, fully offline, via
  `verify_assertion()` (`commercial_runtime/licensing_contracts/
  assertion_verifier.py:130-247`) — no network call required for
  verification itself.
- **Expiry/renewal**: `expires_at`/`not_before` fields; renewed by the
  next successful check-in; `OfflinePolicy`
  (`policy_evaluator.py:26-41`) governs check-in interval, retry
  interval, offline grace, warning thresholds, and an optional
  emergency-extension window for when the device genuinely cannot
  reach the server.
- **Revocation**: not directly revocable itself (it's a signed,
  time-boxed snapshot) — its *effect* is neutralized by (a) natural
  expiry, (b) the signing key being revoked
  (`revoke_signing_key()`, invalidates the signature check
  retroactively for that key), or (c) the underlying license/
  installation/subscription status changing, reflected in the next
  re-issued assertion or detected by local policy evaluation.

## Credential type 2 — Device key (`DevicePublicKey`, per-installation, long-lived, proves possession)

- **Model**: `owner_device_public_keys`
  (`app/models/licensing_service.py:35-51`): `installation_id`,
  `public_key`, `fingerprint` (globally unique), `algorithm` (default
  `ed25519`), `status` (`ACTIVE`/`REVOKED`/`REPLACED`), `revoked_at`,
  `replaced_by_device_key_id`, `last_proof_at`.
- **Registered**: at activation, `register_device_key()`
  (`device_identity.py:68-76`).
- **Used for**: signing every subsequent request from that
  installation (activation retries, check-ins, deactivation) — proof
  of possession, verified server-side via
  `device_identity.verify_signature()`.
- **Rotated**: `replace_device_key()`
  (`device_identity.py:124-139`) — issues a brand-new key, old key
  set `status=REPLACED` with a `replaced_by_device_key_id` pointer;
  "an old, replaced device can never be silently reactivated" (real
  code comment).
- **Revoked**: `revoke_device_key()` (`device_identity.py:112-121`) —
  `status=REVOKED`, `revoked_at` set; a revoked key's signature is
  never trusted again, confirmed server-side
  (`assertions.py:123-124`) and enforced client-side too (the trust
  concept mirrors the signing-key trust store, see below).
- **Admin action**: staff route `POST /licensing-admin/installations/
  <id>/replace-device` revokes the old key only — issuance of the new
  key is a separate, client-driven flow (re-activation).

## Credential type 3 — Owner signing key (`SigningKey`, server-side only, never sent to a device)

- **Model**: `owner_signing_keys` (`app/models/licensing_service.py:
  22-32`): `key_id`, `algorithm` (default `ed25519`), `public_key`,
  `status` (`DRAFT`/`ACTIVE`/`RETIRED`/`REVOKED`), `activated_at`,
  `retired_at`, `revoked_at`, `revocation_reason`.
- **Private key storage**: PEM file on disk at
  `<key_directory>/<key_id>.pem` — **never** in the database
  (`owner/app/licensing_service/signing.py` module docstring).
- **Rotation**: `rotate_signing_key()` generates a fresh key and
  activates it in one step; the previously-ACTIVE key becomes
  `RETIRED` (not deleted) — "assertions it already signed remain
  verifiable" (real code comment). Only explicit `revoke_signing_key()`
  invalidates a key's signatures retroactively.
- **Published to clients**: only the **public** key, via a signed
  rotation manifest — `export_signed_keyset_manifest()`
  (`signing.py:202-241`), admitted client-side into `OwnerTrustStore`
  (`commercial_runtime/licensing_contracts/trust_store.py`) only if
  the manifest itself is signed by a key already trusted
  (`admit_manifest()`, lines 76-121) — prevents an untrusted party
  from injecting a rogue signing key.

## Real test coverage

`owner/tests/test_licensing.py` (key format/entropy/never-persisted),
`owner/tests/test_phase6_activation_protocol.py` (full key never
appears anywhere), `commercial_runtime/licensing_contracts/tests/
test_device_identity.py`, `test_trust_store.py` (bootstrap-once,
manifest admission requires trusted signer, tampered/untrusted-signer
manifests silently discarded, revocation), `test_assertion_verifier.py`.

## Mobile contract implication

M7.17 must model three distinct credential concepts, never conflated:
`SignedAssertion` (short-lived, offline-verifiable, re-issued on
check-in), the device's own Ed25519 keypair (never transmitted, only
its public half sent once at registration/rotation), and the
`OwnerTrustStore`'s admitted signing-key set (public keys only, never
a secret the mobile client holds).
