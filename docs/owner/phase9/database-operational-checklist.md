# Phase 9 — Database Operational Checklist

Real, run against `aura_owner_staging` this session unless marked NOT VERIFIED.

- [x] Dedicated database, separate from dev/test (`aura_owner_staging`).
- [ ] Dedicated least-privilege application role — NOT VERIFIED this session (no superuser access
      available); exact SQL documented in `postgresql-hardening.md`.
- [ ] Dedicated migration role — same gap; a real deployment could separate "migrate" (DDL) from
      "runtime app" (DML-only) roles, not done this session for the same superuser-access reason.
- [x] Connection limit set (`CONNECTION LIMIT` documented for the real role; database-level timeouts
      already applied and verified via `SHOW`).
- [x] `statement_timeout` — `30s`, confirmed via `SHOW`.
- [x] `idle_in_transaction_session_timeout` — `60s`, confirmed via `SHOW`.
- [x] Timezone policy — `UTC`, confirmed via `SHOW`.
- [ ] Secure network binding to a real remote host — NOT VERIFIED (no remote host); local Docker
      network isolation is the actual real control for the Compose-based deployment (`network-and-
      trust-boundaries.md`).
- [ ] TLS for DB traffic leaving the host — not applicable to this session's topology.
- [x] Schema ownership — `aura_owner` (session's available role) owns `aura_owner_staging`; a real
      deployment should transfer ownership to the dedicated app role once it can be created.
- [x] Migration locking reviewed — see `migration-runbook.md` (single-replica, no lock needed this
      topology; documented requirement for a future multi-replica topology).
- [ ] Slow-query visibility — see `observability-architecture.md` (Milestone 8); `pg_stat_statements`
      not enabled this session (would require a `postgresql.conf` restart on this shared dev instance,
      deferred to avoid disrupting the developer's own dev/test databases on the same server).
- [ ] Storage monitoring — see `observability-architecture.md`.
- [x] Autovacuum — confirmed `on` (PostgreSQL 17 default, not disabled), no override needed at this
      data volume.
- [ ] Index-health review — no staging data volume exists yet to meaningfully review (fresh schema,
      zero rows beyond seed data); deferred until real pilot data exists.
- [x] Audit-chain verification — real, existing Owner functionality (Phase 8), unchanged; not
      re-verified in isolation here since no source in that path changed this phase (see
      `phase8-evidence-reuse-decision.md`). Re-exercised as part of Milestone 19's final regression.
