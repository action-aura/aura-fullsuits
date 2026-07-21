# Phase 6 -- Device Identity Design

## New table: `owner_device_public_keys`
Fields (per Part D exactly): public UUID id, `installation_id` FK, `public_key` (base64 raw 32 bytes), `fingerprint` (SHA-256 of the raw public key, hex, indexed), `algorithm` (`ed25519`, extensible), `status` (`ACTIVE`/`REVOKED`/`REPLACED`), `registered_at`, `revoked_at`, `replaced_by_device_key_id` (self-FK), `last_proof_at`.

Deliberately absent (per Part D's "do not store" list): private keys, raw IMEI, full MAC, serial numbers, local database paths, geolocation, customer business records. Phase 5's `Installation.device_public_key` text column is left untouched (unused by Phase 6 code) rather than removed, per "add normalized models only where necessary" -- removing a Phase 5 column is out of scope for a phase whose own rules say not to broaden into a general Owner redesign.

## Proof of possession
Every activation and check-in request body is canonicalized (`licensing_service/canonical.py`) and the client signs the canonical bytes with its Ed25519 private key. The server verifies with `cryptography`'s `Ed25519PublicKey.verify()` against the *registered* public key (for check-in) or the *submitted* public key (for initial activation, establishing registration). A valid signature over a replayed or altered payload is impossible without the private key -- verified directly by `test_device_identity.py::test_altered_payload_rejected`.

## Registration flow
1. Simulator generates an Ed25519 key pair locally (`cryptography.hazmat...Ed25519PrivateKey.generate()`), keeps the private key in a local file outside any Git-tracked path (`owner/tools/activation_simulator/.device/` -- gitignored).
2. Activation request includes the raw public key (base64) and a signature over the canonical request body.
3. Server verifies the signature *before* trusting the key, then persists the public key + fingerprint + `ACTIVE` status only after the full activation decision succeeds (same transaction).

## Revocation and replacement
`device_identity.py::revoke_device_key()` sets `status=REVOKED`, `revoked_at=now()`; any subsequent check-in signed by that key is rejected (`DEVICE_KEY_REVOKED`) regardless of signature validity, checked *after* cryptographic verification so a revoked-but-still-technically-valid signature still fails closed. `replace_device_key()` links old->new via `replaced_by_device_key_id`, revokes the old key, and requires a fresh device registration (new key pair, new proof of possession) -- an old key can never be silently reactivated.

## Installation ID is not authentication
`activation.py`/`checkin.py` never treat a bare `installation_id` as sufficient; every state-changing or state-revealing operation past initial activation requires a valid, non-revoked device-key signature over the current request. Verified: `test_device_identity.py::test_stolen_installation_id_without_private_key_rejected`.
