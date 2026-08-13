# Phase 9 — Key Rotation Runbook

## License pepper rotation

Rotating the pepper invalidates every already-issued license key's HMAC (`hash_license_secret`) —
this is a **destructive** operation for any already-issued staging license. Only perform during a
pilot reset, never mid-pilot.

1. Announce the maintenance window to the pilot owner (Milestone 15).
2. Generate a new pepper: `python -c "import secrets; print(secrets.token_urlsafe(48))"`.
3. Update `OWNER_LICENSE_PEPPER` in `.env.staging` (never in a tracked file).
4. Restart the `owner` service: `docker compose -f docker-compose.staging.yml up -d owner`.
5. Run `flask commercial preflight` — confirm `license_pepper_configured: OK`, not the placeholder
   `WARNING`.
6. Re-issue any staging licenses that were active before rotation (their stored HMAC no longer
   verifies against the new pepper) via the normal `issue_license_key()` flow.
7. Record the rotation in the audit log (already automatic — `issue_license_key()` is already
   audited) and in `daily-pilot-review-template.md` for that day.

## Signing-key rotation

1. `flask licensing generate-signing-key` — creates a new key, does not activate it.
2. `flask licensing activate-signing-key <new_key_id>` — the new key becomes the active signer;
   the old key remains in the trust store as a valid *verifier* until explicitly retired, so
   already-issued assertions signed by it remain valid through their own `expires_at`.
3. Regenerate `trust_anchor.json` for any product build that will be produced after this point
   (`scripts/generate_trust_anchor.py`) — already-built/already-installed clients keep trusting the
   old key until their own next successful check-in re-syncs the trust manifest
   (`_refresh_trust_manifest_best_effort()`, real code, Phase 8V-P9).
4. Retire the old key only after confirming no active installation still depends on it exclusively
   (real Owner query: any installation whose `last_successful_checkin_at` is older than the old key's
   planned retirement date should be investigated first).

## Emergency revocation (suspected compromise)

1. `flask licensing activate-signing-key <emergency-new-key>` immediately — stops new assertions being
   signed by the compromised key.
2. Mark the compromised key `REVOKED` in the trust store — clients that already trust it (via a stale
   local trust manifest) will fail signature verification on their *next* assertion issued after
   revocation, not retroactively invalidate ones already accepted (matches the real, already-tested
   Phase 8 trust model — no retroactive trust removal exists, by design, since a client cannot be
   forced to distrust something it already accepted while legitimately offline).
3. Follow `incident-response-plan.md`'s "signing-key issue" category for containment/communication.

## Database credential rotation

1. `ALTER ROLE aura_owner_staging_app WITH PASSWORD '<new-password>';` (real deployment; see
   `postgresql-hardening.md` for the actual least-privilege role name).
2. Update `.env.staging`, restart `owner` and `backup` services.
3. Confirm `/health/ready`'s `database_connectivity` check is `OK` post-restart before considering the
   rotation complete.

## Backup encryption credential rotation

Re-encrypt the most recent backup under the new credential before retiring the old one; never leave a
window where the only valid backup is encrypted under a credential that has already been rotated out.
