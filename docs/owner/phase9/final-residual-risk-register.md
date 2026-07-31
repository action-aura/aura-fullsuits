# Phase 9 — Final Residual Risk Register

| # | Risk | Severity | Status |
|---|---|---|---|
| 1 | No real remote staging host — everything validated locally/natively | Blocking for PASS, not for CONDITIONAL PASS | Open — requires real infrastructure access, not a code fix |
| 2 | No real public TLS / domain | Same | Open — same |
| 3 | Dedicated least-privilege Postgres application role not created | P2 | Open — requires superuser access; exact SQL documented (`postgresql-hardening.md`) |
| 4 | No live monitoring/alerting stack (Prometheus/Alertmanager) | P2 | Open — requires Docker/remote host; design + underlying signals real and tested |
| 5 | `/health/ready` shows real latency degradation under heavy concurrent load (30 concurrent -> p95 4.4s) | P3 | Open, documented, outside this pilot's realistic target load; recommend explicit pool sizing once real monitoring concurrency is known |
| 6 | No rc.6 staging-connected product artifacts | Blocking for pilot readiness | Open — correctly not built without a real HTTPS URL (would be premature/dishonest) |
| 7 | No real private artifact distribution channel | Blocking for pilot readiness | Open — depends on #1/#2/#6; a real starting point (`owner/app/releases` blueprint) identified |
| 8 | CI workflow defined but never executed (no connected git remote) | P2 | Open — every command in it individually verified manually this session |
| 9 | Secret-at-rest encryption on the backup volume not configured (no real host/volume) | P2 | Open — real deployment requirement, documented |
| 10 | Off-host backup copy not configured (no real remote storage target) | P2 | Open — real deployment requirement, documented |
| 11 | Migration role separation (DDL vs runtime app) not implemented | P3 | Open — same superuser-access gap as #3 |

## Risks closed this phase (real, verified fixes — not carried forward)

- 32 real dependency CVEs (`dependency-risk-register.md`) — **CLOSED**, verified via post-upgrade
  `pip-audit` + full 987/987 regression.
- HSTS missing for staging environment — **CLOSED**, real fix + real test.
- No structured/redacted logging anywhere in Owner — **CLOSED**, real implementation + 7 tests + a
  real duplicate-emission bug found and fixed along the way.
- `.gitignore` gap (`.env.staging` was not actually excluded) — **CLOSED**, real, found before any
  real secret was ever put at risk.
- Backup Compose service `restart: unless-stopped` on a one-shot script (would have hot-looped) —
  **CLOSED**.
- Retail canonical-suite ordering dependence (as a *supported command* question) — **CLOSED**, adopted
  the correct existing tool.

## Explicitly not reopened (Phase 8, unaffected)

Commercial-enforcement architecture, EmergencyExtension, the Phase 8V-P9 stale-assertion guard, and
every other Phase 8 functional area — no source in those paths changed this phase
(`phase8-evidence-reuse-decision.md`).
