# Phase 9R — External Dependency Register (M0)

Every item below is a real external asset Phase 9R needs and this session
cannot provision, purchase, or invent. Each row names the exact gate it
blocks and what "resolved" looks like.

| # | Dependency | Blocks | Resolved when |
|---|---|---|---|
| 1 | Domain name, owned/registered | M6, M18, M19 | Registrar confirms ownership; WHOIS/registrar dashboard accessible |
| 2 | DNS provider + access | M6, M18 | Can create A/AAAA/CNAME/TXT records for the chosen domain |
| 3 | Remote server or cloud account (VPS/PaaS) | M1, M4, M5, M18 | SSH or platform-console access to a running instance in a chosen region |
| 4 | TLS certificate issuance path | M6 | ACME (Let's Encrypt) reachable from the server, or a purchased cert — decided in M1, executed in M6 |
| 5 | Production PostgreSQL host | M4, M18 | Either self-managed on the remote server or a managed provider; reachable with a dedicated least-privilege role |
| 6 | Object storage (private artifacts + external backups) | M11, M12 | Bucket/container created, credentials scoped to least privilege, reachable from the app server |
| 7 | External backup destination, physically separate from the production host | M12, M13 | Distinct from #6 or a distinct bucket/prefix with independent access control and versioning/immutability |
| 8 | Monitoring/alerting destination (e.g. a metrics/log sink and a notification channel) | M14, M15 | Health/metrics reach an external system; a test alert is received |
| 9 | Deployment credentials (SSH key or platform API token, scoped) | M16, M18 | CI/CD or manual deploy can authenticate without a shared root password |
| 10 | SMTP provider (only if email-based alerting/notifications are wanted) | M14 (optional) | Not required by anything in the completed Phase 9.5E baseline; only needed if a runbook wants email alerts instead of another channel |
| 11 | Budget/provider decision (which cloud, which region, which tier) | M1 | Owner picks from the options `deployment-architecture.md` will present |

## What Phase 9R does in the meantime

All configuration, code, and documentation that depends on these being
*abstractly correct* (e.g. "the app must fail closed if `DATABASE_URL` is
missing," "backups must be encrypted and off-host," "downloads must be
short-lived signed URLs, never permanent public links") is implemented and
tested against local/synthetic stand-ins now, so that once each dependency
above is resolved, connecting it is configuration, not new engineering.

## Recommended next step for the owner

Decide dependency #11 first (provider/region/budget) — everything else
(#1–#9) follows directly from that choice, and `deployment-architecture.md`
/ `production-cost-model.md` are written to make that decision concrete
rather than abstract.
