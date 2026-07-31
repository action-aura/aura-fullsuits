# Phase 9 — Alert Catalog

Design catalog for the real deployment's Prometheus/Alertmanager stack (NOT VERIFIED as a running
system this session — no Docker/remote host; see `observability-architecture.md`). Each row maps
directly to something this phase already made observable (a `/health/ready` check, a structured log
`reason_code`, or an existing real Owner event type).

| Alert | Source signal | Threshold | Severity |
|---|---|---|---|
| Owner process down | `/health/live` unreachable | 2 consecutive failed scrapes (60s) | P0 |
| Owner not ready | `/health/ready` returns `503` | 3 consecutive failed scrapes (90s) | P0 |
| Database unreachable | `database_connectivity: FAIL` in `/health/ready` | immediate | P0 |
| Migration mismatch | `migration_at_head: FAIL` | immediate | P1 |
| Signing key unavailable | `active_signing_key_exists: FAIL` | immediate | P0 |
| Trust anchor mismatch | `trust_anchor_matches_active_key: FAIL` | immediate | P1 |
| License pepper misconfigured | `license_pepper_configured: FAIL` | immediate | P0 |
| HTTP 5xx error rate | Caddy/Owner access log | >5% of requests over 5min | P1 |
| Elevated latency | request duration in structured logs | p95 > 2s over 5min | P2 |
| Repeated auth failures | Owner's existing login-attempt tracking (Phase 4/8) | >10 failed logins/5min from one source | P1 |
| Repeated MFA failures | Owner's existing MFA verification path | >5 failed MFA/5min for one account | P1 |
| Activation failures | `ACTIVATION_FAILED` events (existing real event type) | >20% of attempts over 15min | P2 |
| Check-in failures | `CHECK_IN_FAILED` events | >20% of attempts over 15min | P2 |
| Stale-assertion rejections spike | `ASSERTION_STALE_REJECTED` events (Phase 8V-P9) | any occurrence outside a known replay-proxy test | P1 (possible real replay attempt) |
| Reconciliation scan failure | scheduler exit code != 0 (Milestone 9) | any occurrence | P1 |
| Backup failure | `backup.py` exit code != 0 | any occurrence | P0 |
| Backup not run in >26h | last successful backup manifest timestamp | > 26h old | P1 |
| Disk usage high | node-exporter `node_filesystem_avail_bytes` | < 15% free | P1 |
| Disk usage critical | same | < 5% free | P0 |
| Memory pressure | container memory usage | > 90% of limit sustained 10min | P2 |
| Unexpected process restart | container restart count | any restart outside a deliberate deploy | P2 |
| TLS certificate expiring | Caddy-managed cert expiry | < 14 days remaining | P1 |
| Audit-chain verification failure | `verify_chain()` returns `False` (real, existing Phase 8 function; scheduled via Milestone 9) | any occurrence | P0 |

## Deduplication and recovery notification

Real deployment requirement (Alertmanager's own grouping/inhibition, not custom-built): group by
`alert name + severity`, mute repeat notifications for a firing alert for 30 minutes, always send an
explicit "resolved" notification when a firing alert clears — never let an operator infer recovery from
silence alone.

## No alert storms

The P0/P1 split above is deliberately short — a small supervised pilot does not need dozens of
low-value P2/P3 alerts paging anyone; most P2 items are dashboard-only in the real deployment, not
paging alerts.
