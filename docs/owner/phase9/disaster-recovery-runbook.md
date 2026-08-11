# Phase 9 — Disaster Recovery Runbook

## Scenario: staging host lost entirely (disk failure, host termination, etc.)

1. Provision a replacement host, apply `host-hardening.md`'s baseline.
2. `git clone` the repository, `git checkout` the tag/commit currently deployed (per the deployment
   record — see `staging-deployment-report.md`'s format).
3. Restore `.env.staging` from its own secure backup location (never stored alongside the Postgres
   backup — a compromised/lost DB backup should not also mean lost secrets).
4. `docker compose -f docker-compose.staging.yml up -d db` — bring up an empty Postgres.
5. Select the most recent backup, verify its checksum (`sha256sum -c`), `pg_restore` into the fresh
   `db` container.
6. Restore the signing-key volume from its own separate backup (see `backup-policy.md`).
7. `docker compose -f docker-compose.staging.yml up -d` — bring up the full stack.
8. `flask commercial preflight` -> confirm `ok: true`.
9. `/health/ready` -> confirm `ready: true`.
10. Run the same verification checklist as `restore-drill-report.md` (staff login, MFA, commercial
    records, audit-chain).
11. Update DNS if the host's IP changed; confirm Caddy re-obtains a valid certificate.
12. Notify the pilot owner per `incident-communication-templates.md`.

## Scenario: Postgres data corruption, host otherwise healthy

1. Stop the `owner` service (prevent further writes) — `docker compose -f docker-compose.staging.yml
   stop owner`.
2. Follow steps 5, 8-10 above against the existing `db` container (drop and recreate the database
   first if corruption is at the database level, not the volume level).
3. Restart `owner`.

## Scenario: signing-key volume lost, Postgres intact

Every already-issued assertion signed by the lost key remains valid until its own `expires_at` (Phase
8 offline-policy design — clients don't need Owner to be reachable to keep trusting an assertion they
already hold). Immediate action: generate and activate a new signing key (`key-rotation-runbook.md`'s
emergency-revocation procedure), regenerate `trust_anchor.json` for future builds. Already-installed
staging clients pick up the new key automatically on their next successful check-in
(`_refresh_trust_manifest_best_effort()`, real Phase 8V-P9 code, unchanged).

## Real evidence this runbook is grounded in

`restore-drill-report.md` — every step above through "restore" was actually exercised this session
(minus host provisioning and DNS, which require a real host). 2-second real restoration time at this
data volume, real login/MFA/audit-chain verification post-restore.
