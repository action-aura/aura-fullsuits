# Phase 6 -- Signing-Key Rotation Runbook

## Routine (scheduled) rotation
1. `flask licensing rotate-signing-key --reason scheduled_rotation` -- generates a new key, activates it, and automatically retires (not revokes) the previous active key.
2. Confirm: `flask licensing verify-signing-key-health` reports `OK` for the new active key.
3. Confirm the new key is published: `curl http://127.0.0.1:5551/api/licensing/v1/signing-keys` (or the internal `/licensing-admin/signing-keys` view) shows the new key `ACTIVE` and the old one `RETIRED`.
4. No client action required -- the next check-in each installation performs will receive an assertion signed by the new key; the old key remains valid for verifying any assertion issued before rotation, until that assertion's own `expires_at` (bounded by `OWNER_ASSERTION_TTL_SECONDS`, default 24h).
5. **Overlap window**: keep a `RETIRED` key's row (and its public key published via `/signing-keys`) for at least one full `OWNER_ASSERTION_TTL_SECONDS` past its retirement -- after that window, every assertion it ever signed has expired on its own, and the key may be safely dropped from the published set in a future cleanup (not automated this phase).

## Emergency (compromise) rotation
1. `flask licensing rotate-signing-key --reason compromise_response` -- issues a fresh key immediately.
2. `flask licensing` (via the internal admin UI, `POST /licensing-admin/signing-keys/<key_id>/revoke`, `signing_keys.manage` permission + recent MFA) -- explicitly **revoke** the compromised key, not just retire it. This immediately invalidates every assertion it ever signed, even ones still inside their `expires_at` window.
3. Every currently-activated installation will fail its *next* check-in's assertion **verification** on the client side (if the client is checking the signing key's status via `/signing-keys` as documented) until it performs a fresh check-in against the new key -- this is the intended fail-safe behavior for a genuine compromise, not a bug.
4. Audit trail: `SIGNING_KEY_ROTATED` and `SIGNING_KEY_REVOKED` audit events, both attributed to the acting staff member, both requiring `signing_keys.manage` + recent MFA (Part S/F).

## What this phase does NOT automate
Scheduled/cron-triggered rotation (a human or a future ops job runs the CLI command); alerting on upcoming key expiry; automatic client-side re-fetch triggers beyond "the next check-in gets the new key naturally." All are reasonable future-phase operational hardening, not required for Phase 6's foundation.
