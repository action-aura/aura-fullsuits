# Phase 9R — Remote Resumption Runbook

Allows a new session to resume from the exact repository-controlled closure
commit **without redoing M0–M17**. Follow in order. Each numbered step names
its own stop condition — do not proceed past a failed check.

## 1. Verify branch and commit

```
cd C:\Users\Dell\Desktop\AuraEnterprise\aura-fullsuits-phase9r
git branch --show-current   # must be phase9r/real-secure-remote-production
git rev-parse HEAD          # must match the closure commit in PRE-INFRASTRUCTURE-HANDOVER.md
```

**Stop if:** branch or commit doesn't match. Investigate before proceeding —
do not assume the closure state is what you expect.

## 2. Verify clean tree

```
git status --short
```

**Stop if:** anything is uncommitted. Resolve (commit or stash) before
touching M18+.

## 3. Verify historical tags

```
git tag --list
```

**Stop if:** `aura-secure-staging-phase9-complete` or
`aura-owner-real-production-phase9r-complete` exist — they should not, per
every prior checkpoint's explicit instruction. Their presence means
something deviated from this plan; investigate before proceeding.

## 4. Verify legacy repository

```
cd C:\Users\Dell\Desktop\AuraEnterprise\AuraEnterprise
git rev-parse HEAD    # must be 414e6ea5ca9fc698fe075a4ae4464cebf0b7bf34
```

**Stop if:** different. The legacy repository must remain exactly as it was
throughout every prior phase.

## 5. Verify Unified Mobile workspace

```
cd C:\Users\Dell\Desktop\AuraEnterprise\aura-fullsuits
git branch --show-current   # feat/retail-unified-mobile-android-ios (or wherever it has independently progressed)
```

Confirm this workspace's own history has continued independently and Phase
9R never committed anything to it. **Do not modify it** in the course of
resuming Phase 9R.

## 6. Provision remote host

Per `infrastructure-acquisition-manifest.md`'s "Remote host" section. Apply
`docs/owner/phase9/host-hardening.md`'s baseline (real, already written).

**Stop if:** SSH access isn't confirmed working before proceeding.

## 7. Configure firewall

Allow only 22 (restricted source IP if possible), 80, 443 inbound. Per
`docs/owner/phase9/firewall-and-network-policy.md`.

## 8. Configure DNS

Per the manifest's "Domain and DNS" section. Point the staging hostname's
A/AAAA record at the new host's IP.

**Stop if:** DNS doesn't resolve to the new host within a reasonable
propagation window (check with `dig`/`nslookup` from an external network,
not just locally).

## 9. Configure secrets

Generate real, unique `OWNER_SECRET_KEY`/`OWNER_LICENSE_PEPPER`/database
credentials for staging — **never copied from local dev, never reused
between environments** (`configuration-contract.md`'s explicit
requirement). Store via GitHub Environment secrets or the host's own
`.env.staging` (outside the repository, per Phase 9's existing pattern).

**Stop if:** `OWNER_STRICT_CONFIG=true` startup validation
(`app/config.py`) rejects the configuration — read the exact error, it
names the missing/invalid value.

## 10. Configure PostgreSQL

Bring up the `db` Compose service. Create the least-privilege application
role explicitly (per `production-postgresql.md`'s real finding — do not
reuse a superuser-adjacent role). Apply
`docs/owner/phase9/postgresql-hardening.md`'s real prior `ALTER DATABASE`
timeout/timezone settings.

## 11. Configure object storage

Per the manifest's "Object storage" section. Implement the real-storage
counterpart to `app/releases/storage.py`'s local/test adapter (same
function signatures: read/write artifact bytes) — this is real
implementation work deferred from M11, not yet built.

## 12. Configure monitoring destinations

Per the manifest's "Monitoring" section and `alert-catalog.md`'s already-
mapped signals.

## 13. Execute M18 — remote staging

`docker compose -f docker-compose.staging.yml up -d`. Run migrations
(`alembic upgrade head` — current head: see
`PRE-INFRASTRUCTURE-HANDOVER.md`). Seed RBAC/catalog/offline-policy. Run
`flask commercial preflight`.

**Stop if:** any blocking preflight check fails without a known, documented
reason (compare against the real local evidence in
`observability-and-alerting.md` — a first-deploy `trust_anchor` warning is
expected and documented there; anything else is a real problem).

## 14. Execute trusted TLS validation

Confirm Caddy obtains a real Let's Encrypt certificate (not `tls internal`)
against the real domain. Verify via a real external client
(`curl -v https://<staging-hostname>`), not just from the host itself.

**Stop if:** the certificate isn't publicly trusted — do not proceed to
claim "HTTPS working" on a self-signed or internal-CA cert.

## 15. Execute remote migration

Already done in step 13 if migrations ran as part of `up -d`'s startup
command. Verify via `/health/ready`'s `migration_at_head` check.

## 16. Execute health/readiness

`curl https://<staging-hostname>/health/live` and `/health/ready`. Compare
the check count and names against `observability-and-alerting.md`'s
real local evidence (50 checks as of the closure commit) — a real remote
run should show the same checks, real values.

## 17. Execute M19 — remote browser validation

Real Chromium against the real HTTPS domain. Full checklist in the
original Phase 9R scope (login, MFA, CSRF, English/Arabic/RTL, every
subsystem UI, all four viewports).

## 18. Execute M20 — remote client licensing validation

Real synthetic clients (Windows Retail/Android Retail/Windows Clinic/
Android Clinic) against the real remote licensing endpoints. Full
activation → refresh → suspend → reactivate → renew → revoke sequence.
**Never** simulate by inserting `Installation` rows directly.

## 19. Execute M21 — concurrency/abuse validation

Real concurrent requests against the real deployed multi-worker Gunicorn
topology — this is where `test_phase9r_rate_limit_multi_worker.py`'s
local-simulation evidence gets its real-network counterpart.

## 20. Execute M22 — performance validation

Representative synthetic dataset at real scale (not the ~13-18 row
dev-scale data this phase's local evidence used). Real `EXPLAIN ANALYZE`
against the critical paths named in `production-postgresql.md`'s own
deferred section.

## 21. Execute M23 — exposed-infrastructure security validation

Dependency/secret/container scans (already clean locally — re-run against
the real deployed artifact), TLS configuration check, security-header
validation, authenticated IDOR testing, rate-limit testing against the
real endpoint, signed-download tampering tests.

## 22. Execute M24 — runbooks

Incident response, key rotation, MFA recovery, SUPER_ADMIN loss, release
withdrawal — real procedures for the real deployed environment.

## 23. Execute M25 — controlled-pilot preparation

Pilot customer eligibility, support hours, escalation process, exit
criteria — per the original Phase 9R scope's own M25 checklist.

## 24. Execute M26 — blocking preflight

The full production preflight (`app/config.py` validate() plus
`flask commercial preflight` plus the M18-M23 evidence above) must pass
with zero unresolved P0/P1 before proceeding.

## 25. Execute M27 — full remote E2E

The complete 30-step synthetic business-to-activation workflow from the
original Phase 9R scope, executed for real against the real remote
environment.

## 26. Execute M28 — final regression

Full regression suite (same suites as this closure's own final regression,
`PRE-INFRASTRUCTURE-HANDOVER.md` §16) run one more time from the final
candidate commit, plus every M18-M27 remote suite.

## 27. Create the final tag — only after complete PASS

```
git tag -a aura-owner-real-production-phase9r-complete -m "..."
```

Only if every PASS requirement in the original Phase 9R scope is met with
real evidence — zero P0, zero P1, zero NOT VERIFIED remaining on anything
the PASS requirements list names. Do not create this tag speculatively or
under time pressure.

## Explicit stop conditions (apply throughout)

- Any step's own "Stop if" condition triggers.
- Any real evidence contradicts this document's assumptions (e.g. the
  closure commit's test counts don't match what's found at resumption
  time) — investigate the discrepancy before proceeding, don't paper over
  it.
- Real infrastructure access is lost or revoked mid-sequence — pause,
  document state, do not attempt to fake completion of a step that lost
  its infrastructure dependency.
- Any P0/P1 finding at any step — fix and re-verify that step before
  advancing to the next.
