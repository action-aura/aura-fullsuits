# Phase 6 -- Signed Assertion Design

## Envelope shape
```json
{
  "payload": { "...see below..." },
  "signing_key_id": "2026-07-...-k1",
  "algorithm": "ed25519",
  "assertion_version": 1,
  "signature": "<base64 signature over canonical(payload)>"
}
```
The signature covers the canonical-serialized `payload` object only (`licensing_service/canonical.py`, ADR-6.4) -- the envelope fields around it are not signed input but are trusted because the whole HTTP response itself came from an already-authenticated exchange; a verifier re-derives trust purely from `payload` + `signature` + the *published* public key for `signing_key_id`, never from transport.

## Payload fields (exactly the Part H allowlist, nothing more)
`assertion_id`, `issuer` (`"aura-owner"`), `product_code`, `license_public_id`, `installation_public_id`, `platform`, `app_version_policy`, `release_channel`, `issued_at`, `not_before`, `expires_at`, `license_status`, `installation_status`, `subscription_status`, `allowed_device_count`, `device_key_fingerprint`, `entitlements` (the immutable snapshot from `entitlements.py`), `offline_policy`, `contract_version`.
Explicitly never included: license key, license HMAC, pepper, payment data, tax identifiers, patient/sales/inventory data, local database metadata -- enforced by the same allowlist-guard pattern as Phase 5's `api/serializers.py::_guard` (extended, not replaced, with Phase 6 forbidden markers).

## Verification (both server-side test harness and the simulator)
`assertions.py::verify_assertion(envelope)`: (1) look up the public key for `signing_key_id` among *all* keys ever published (including retired-but-not-revoked ones, so an assertion issued before a rotation remains verifiable through its own `expires_at`), (2) reject if the key's status is `REVOKED`, (3) recompute `canonical(payload)` and verify the Ed25519 signature, (4) check `not_before <= now <= expires_at`. Any failure returns a specific, non-forgeable-hint reason -- never partial trust.

## Key-overlap policy
A rotated-out key moves to `RETIRED` (still verifiable) rather than `REVOKED` (never verifiable) unless explicitly revoked for compromise. `signing-key-rotation-runbook.md` documents the exact overlap window default (`OWNER_ASSERTION_TTL_SECONDS`-driven: a retired key must remain `RETIRED`, not deleted/revoked, for at least one full assertion TTL past its retirement so no legitimately-issued assertion ever becomes unverifiable).

## No customer-data leak surface
The entitlement snapshot embedded in an assertion is built exclusively from `entitlements.py`'s typed, allowlisted resolution output (Part O) -- it can never contain a forbidden business/medical field because the resolution engine itself only ever reads from `owner_entitlement_definitions`/`owner_plan_entitlements`/`owner_addon_entitlements`/`owner_license_entitlements`, none of which have a column capable of holding such data (same structural guarantee as Phase 5's `test_data_boundary.py`).
