# Phase 9 Milestone 7 — Restore Drill Report

## Result: PASS — real backup, real restore, real verification, all against real local infrastructure

## Setup

Real synthetic data seeded into the real `aura_owner_staging` database (created in Milestone 6):
- 1 Super Admin staff user (`phase9-drill-admin@example.com`), Argon2id password hash, MFA required.
- 1 confirmed MFA credential (TOTP secret `JBSWY3DPEHPK3PXP`, AES-encrypted at rest per the existing
  real Owner MFA implementation).
- 1 customer ("Phase 9 Restore Drill Test Co"), 1 subscription (transitioned to `ACTIVE`), 1 license
  (`ISSUED`, `device_limit=1`, real Ed25519-signed key issuance).
- 6 real audit-log entries generated as a side effect of the above (`SIGNING_KEY_GENERATED`,
  `SIGNING_KEY_ACTIVATED`, `SUBSCRIPTION_CREATED`, `SUBSCRIPTION_STATUS_CHANGED`, `LICENSE_CREATED`,
  `LICENSE_KEY_ISSUED`).

## Backup

`python deploy/staging/backup.py --db-name aura_owner_staging ...` — real `pg_dump` (custom format),
real SHA-256 checksum computed and written alongside the dump:

```
backup_id: staging-20260731T141900Z
dump_sha256: 58ee80f2ffc804b4b537cdaf6ce8fc58de8321030d459a87241857fe93cb5264
dump_size_bytes: 208193
```

## Restore (into an isolated database, live staging never touched)

1. Checksum verified against the manifest before restore (`sha256sum -c` -> `OK`).
2. `CREATE DATABASE aura_owner_staging_restore_drill` — a fresh, separate database, never overwriting
   `aura_owner_staging`.
3. `pg_restore --no-owner --no-privileges` into it.
4. **Elapsed restoration time: 2 seconds** (real, measured, this dataset size — see
   `disaster-recovery-runbook.md` for the RTO this informs).

## Verification checklist (every item real, run against the restored database)

| Item | Result |
|---|---|
| Backup selected | `staging-20260731T141900Z` |
| Checksum verified | PASS |
| PostgreSQL restored | PASS (`pg_restore`, no errors) |
| Required configuration applied | PASS (restored DB reachable via the same `StagingConfig`, no separate config step needed — schema-only restore) |
| Migrations confirmed | PASS — `alembic_version` = `0f8d55b753ed` (real head revision) |
| Staff login confirmed | PASS — real `POST /auth/login` with the real password succeeded (`302` to `/auth/mfa-verify`) |
| MFA behavior confirmed | PASS — real TOTP code accepted (`POST /auth/mfa-verify` -> `302` to `/`), full real login completed |
| Product/plan/customer/license records confirmed | PASS — customer, `ACTIVE` subscription, `ISSUED` license with `device_limit=1` all present and correct |
| Installation records confirmed | PASS (0 present — correctly matches: none were created before the backup was taken) |
| Audit-chain validity confirmed | PASS — `app.audit.services.verify_chain()` (the real, existing Phase 8 hash-chain verifier) returned `(True, None)` against the restored data |
| No duplicate scheduled jobs | DEFERRED — the real scheduler is built in Milestone 9, after this drill; re-verify in that milestone's own evidence |
| External API remains disabled until explicitly enabled | PASS — `GET /api/licensing/v1/check-ins` -> `404` (blueprint never registered, `EXTERNAL_API_ENABLED` unset -> `false`, matching Phase 8's own "no route exists, not merely a disabled check" design) |
| Test check-in | NOT APPLICABLE this drill — no installation existed in the backed-up data to check in as; Milestone 13's staging-connected artifacts (NOT VERIFIED, no HTTPS URL) would be the real end-to-end check-in test |
| Elapsed restoration time recorded | PASS — 2 seconds |

## Cleanup

The isolated `aura_owner_staging_restore_drill` database was dropped after verification; the live
`aura_owner_staging` database was never touched by the restore itself (only re-used afterward for
Milestones 9/18/19).

## What this proves and what it doesn't

Proves: the backup format is genuinely restorable, the restored data is byte-correct (audit chain,
password hash, MFA secret all verify correctly against real cryptographic checks, not just "row
counts match"), and the whole cycle is fast enough (2s at this data volume) to support a real RTO
target. Does not prove: restoration onto a different host, restoration of the signing-key *private key
material* itself (this drill only backed up/verified key *metadata* — see `backup-policy.md` for why
that's split), or behavior under a much larger real pilot data volume (Milestone 18 addresses load, not
backup-at-scale, which is out of scope until real pilot data volume exists).
