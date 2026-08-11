# Phase 9 — Staging Deployment Pipeline

## Manual, protected — never automatic (Non-Negotiable Principle 9)

No pipeline in this repository deploys to any real environment automatically. The real deployment
sequence is entirely manual operator action, documented step-by-step in
`staging-deployment-report.md`. This document describes the *pipeline shape* a real CD step would take
if/when a real host and explicit deployment authorization exist — it is design documentation, not a
built automation.

## Real steps this session validated manually (the pieces a real pipeline would orchestrate)

1. `git checkout <commit>` — real, done throughout this session on this branch.
2. Build the staging image (`docker build -f owner/Dockerfile.staging .`) — NOT VERIFIED (no Docker
   Engine this session; the Dockerfile itself is real and complete).
3. `docker compose -f docker-compose.staging.yml up -d db` then run migrations — the migration part
   was done for real, just outside Docker (native `alembic upgrade head` against the real
   `aura_owner_staging` Postgres database, Milestone 6).
4. Seed RBAC/catalog/offline-policy — done for real, natively (Milestone 6).
5. `flask commercial preflight` — done for real, natively (Milestone 6/9), correctly reported the
   expected `trust_anchor` FAIL for a brand-new never-built-against staging key.
6. `/health/live` + `/health/ready` — done for real, natively (Milestone 3/6).
7. Bring up the reverse proxy, confirm TLS — NOT VERIFIED (no Docker Engine, no real domain).

## Manual approval gate

A real pipeline's last automated stage is "staging deployment package ready" (image built, config
validated) — an explicit human action (documented in `staging-deployment-report.md`'s format: operator
identity, timestamp, source commit, target revision) triggers the actual `docker compose ... up -d`
against the real host. No credential or mechanism in this repository can perform that step without a
human present.

## No automatic production deployment

There is no production environment defined in this repository at all (Milestone 1: only staging is
implemented as a remotely-reachable environment this phase) — so "no automatic deploy to production"
is true by the simple fact that no production deployment target exists yet for anything to
automatically deploy to.
