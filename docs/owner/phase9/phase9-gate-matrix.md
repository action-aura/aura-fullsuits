# Phase 9 — Gate Matrix (initial; finalized in `final-staging-gate-matrix.md`)

Preliminary classification, before execution, of what is achievable local-only vs. blocked pending
real infrastructure. Updated as milestones complete.

| Gate | Achievable local-only | Notes |
|---|---|---|
| Retail test-suite isolation | Yes | Pure engineering fix, no infra dependency |
| Architecture design | Yes | Design doc + real Compose stack |
| Deployment packaging (Gunicorn, health/readiness) | Yes | Runs against real local Owner |
| Host/network hardening | Partial | Real config/scripts produced; a real hardened *remote* host cannot be verified |
| Secret management | Yes | Real mechanism implemented and tested locally |
| PostgreSQL hardening | Yes | Applied to a real local Postgres instance |
| Backup + restore drill | Yes | Real `pg_dump`/restore against real local data, into an isolated second DB |
| Logging/monitoring/alerting | Yes | Real local stack, real Owner process observed |
| Scheduled operations | Yes | Real scheduler implementation, run locally |
| Security hardening review + scans | Yes | Real scans against the real repo/artifacts |
| CI validation pipeline | Yes (as a definition/dry run) | No remote CI provider connected this session |
| Staging deployment (Milestone 12) | **No** | Requires a real remote host + domain |
| Staging-connected product artifacts w/ HTTPS activation (Milestone 13) | **No** | Requires a real HTTPS staging URL |
| Private artifact distribution (Milestone 14) | **No** | Requires a real distribution channel |
| Controlled pilot operating model (docs/workflows) | Yes | No real customer onboarded |
| Incident response / support runbooks | Yes | Documentation, real by construction |
| Privacy/retention/audit-review | Yes | Documentation + real audit-chain verification code path |
| Capacity/resilience validation | Yes | Real synthetic load against real local Owner |
| Final regression | Yes | Real, fresh, from final HEAD |
| Final decision | Yes | Honest verdict — expected CONDITIONAL PASS |
