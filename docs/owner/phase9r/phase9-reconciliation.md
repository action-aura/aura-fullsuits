# Phase 9R — Reconciliation with Phase 9 (Secure Staging and Pilot Readiness)

## Discovery

While starting M6 (reverse proxy/DNS/TLS), inspecting `deploy/` turned up a
substantial, real, previously-unreferenced body of work:
`docs/owner/phase9/` (60+ documents) plus `deploy/staging/` (Docker Compose,
Caddyfile, systemd units, backup script). Phase 9 ("secure staging and
pilot readiness") already executed almost this entire phase's scope —
architecture decisions, environment separation, secrets/key management,
PostgreSQL hardening (a real separate `aura_owner_staging` database
created and tested), scheduled operations with real concurrent-process
locking, backup/restore drills, observability design, TLS/security headers,
network/trust boundaries, incident response, pilot operating model — and
reached the identical **CONDITIONAL PASS** verdict, blocked by the identical
missing infrastructure (no domain, no remote host, no tested Docker
Engine). That is also why `aura-secure-staging-phase9-complete` was never
created — recorded in this repository's own history, not invented for this
document.

## Decision (owner-confirmed, 2026-08-04)

**Extend Phase 9's real artifacts rather than duplicate them.** Where a
Phase 9 document/config already covers a Phase 9R milestone, Phase 9R's job
is to re-verify it against everything that changed since (Phase 9.5A
through 9.5E added CRM, commercial sales/orders/payments/commissions, and
expense/reporting/management-collaboration — substantial schema and route
growth Phase 9's own artifacts predate) and extend it, not rewrite it from
a blank page.

## Specific reversals and reconciliations

### M1 architecture — ADR-2 reversed

`architecture-decision-record.md`'s ADR-2 recommended "direct host,
Docker optional, not required." Phase 9 already has a real, internally
consistent `docker-compose.staging.yml`, `deploy/staging/Caddyfile`, and
four systemd units built around Docker Compose as the deployment
mechanism. **Reversed:** Docker Compose is the adopted mechanism, per
Phase 9's own prior real decision — see the amendment appended to
`architecture-decision-record.md`.

### M4 PostgreSQL hardening — additive, not duplicate

Phase 9's `postgresql-hardening.md` already: created a real separate
`aura_owner_staging` database, set `statement_timeout`/
`idle_in_transaction_session_timeout`/`timezone=UTC` at the **database**
level (`ALTER DATABASE ... SET`), and identified the same `aura_owner`
least-privilege gap this milestone's `production-postgresql.md` also
found. Phase 9R's M4 work is genuinely additive on top of that, not a
re-run of the same finding:

- **Connection-level timeouts** (`app/extensions.py`, libpq `options`)
  complement Phase 9's database-level defaults — the database-level
  `ALTER DATABASE` setting is a safety net for *any* connection (`psql`,
  migrations, other tools); the new connection-level setting is explicit
  and portable regardless of which database the connection string points
  at. Not a conflict — two layers of the same protection.
- **Bounded connection pool** (`pool_size`/`max_overflow`) — not present in
  Phase 9's work at all, genuinely new.
- **The `owner_installations` composite-index gap** — a specific finding
  from cross-referencing real query code against `pg_indexes`, not
  mentioned in Phase 9's document, genuinely new.
- **Migration/drift re-verification** — Phase 9's own migration chain
  ended at `0f8d55b753ed` (6 revisions); the current head after Phase
  9.5A-E is `f5959fdb9738` (and now `13944658bddf` after this phase's own
  index migration). Confirming zero drift *now* is a real re-verification
  of a schema that has grown substantially since Phase 9's own check, not
  a repeat of it.

### M5 scheduler topology — additive, not duplicate

Phase 9's `deploy/staging/run_scheduled_ops.py` wraps the *existing* Phase
8 commercial-ops CLI commands (`expiry-scan`, `reconcile`,
`device-limit-scan`) plus audit-chain verification, using a real
Postgres session-level advisory lock with proven skip-if-held behavior
under real concurrent invocation. It does not touch report-snapshot
generation at all. Phase 9R's M5 finding — `REPORT_GENERATED_BY`'s
`"SCHEDULER"` value was structurally unreachable, no CLI command existed to
trigger periodic report generation despite the module's own docstring
claiming "CLI-triggerable" — is a genuinely separate, previously-unfound
gap. The new `flask reports generate-scheduled` command's two-layer safety
(`OWNER_SCHEDULER_ROLE` explicit-designation gate, plus
`generate_snapshot()`'s pre-existing per-report-key advisory lock as
defense in depth) matches the governing instruction's own explicit M5
requirement ("only one canonical scheduler service... advisory locking as
defense in depth, not as an excuse for uncontrolled duplicate schedulers")
more precisely than Phase 9's lock-only pattern — a deliberate choice, not
an oversight, appropriate for a *new* job rather than the already-idempotent
jobs Phase 9's wrapper already covers.

## What this means for the remaining milestones

- **M6** (reverse proxy/DNS/TLS): extend `deploy/staging/Caddyfile`,
  `tls-and-security-headers.md`, `network-and-trust-boundaries.md` for
  anything 9.5A-E's new routes (CRM, commercial sales, expenses/reporting)
  require, rather than write a fresh Caddyfile.
- **M7** (rate limiting): verify Phase 9's rate-limiting posture (if any)
  covers the licensing/download endpoints M7 specifically calls out;
  extend, don't replace.
- **M12/M13** (backup/DR): Phase 9's `backup-policy.md`,
  `disaster-recovery-runbook.md`, `restore-drill-report.md` already exist
  with real drill evidence. Phase 9R's own `backup-policy.md` (M12,
  committed `622c624`) will be reconciled into a cross-reference rather
  than left as a parallel, possibly-conflicting document — tracked as a
  follow-up.
- **M14/M15** (observability/security monitoring): Phase 9's
  `observability-architecture.md`, `logging-and-redaction-policy.md`,
  `alert-catalog.md`, `monitoring-validation-report.md` are real prior
  work to extend for 9.5A-E's new surfaces, not to duplicate.
- **M16** (deployment pipeline): Phase 9's `staging-deployment-pipeline.md`
  and `ci-validation-contract.md` are the real prior artifact.

Each of these will be explicitly reconciled (read, diffed against what
9.5A-E changed, extended) as its milestone is reached, rather than
retroactively rewritten here in bulk.
