# Phase 5 -- Aura Owner Implementation Plan

## Build order (matches the git commit sequence in Part AC)
1. Discovery + scope docs (this set) -- done first, no code.
2. Scaffold `owner/` app factory, config, Docker Compose/Dockerfile, `.env.example`.
3. PostgreSQL schema: all ~40 tables via SQLAlchemy models + a single initial Alembic migration, run against a real local Postgres.
4. Security primitives (Argon2 hashing, TOTP MFA, session/cookie config) + staff auth (bootstrap CLI, login/logout, RBAC) + staff invitations.
5. Commercial catalog (products/platforms/versions/plans/prices/add-ons/entitlements) + release-manifest import command.
6. Customers/contacts + subscriptions/renewals/payments.
7. License domain + secure key issuance service.
8. Installations/devices + activation events + Part R contract JSON Schemas (inactive).
9. Audit log (hash chain) + internal dashboard + UI templates across all areas.
10. Data-boundary allowlist serializers + forbidden-field guard tests.
11. Security hardening pass (CSRF/headers/rate limiting) applied across all routes.
12. Owner database backup/restore (`pg_dump`/`pg_restore`-based).
13. Full automated test suite; fix anything red before tagging.
14. Remaining documentation set (Part AB).
15. Tag `aura-owner-foundation-phase5-complete`.

## Environment reality check (recorded honestly, not glossed over)
This sandboxed session had neither Docker nor a local PostgreSQL server pre-installed. Docker was not available to install (no Docker Desktop/engine reachable via winget in this non-interactive session); PostgreSQL 17 was installed locally via `winget install PostgreSQL.PostgreSQL.17` (same mechanism already used in this project for Python/Node/JDK/Android SDK per `docs/owner/phase5/owner-local-development-guide.md`), so migrations and the test suite run against a real Postgres instance, not a substitute. `docker-compose.yml`/`Dockerfile` are still written as the primary, portable, documented path for anyone with Docker available -- they are infrastructure-as-code deliverables per Part C regardless of what this particular sandbox could run.

## What "done" means for this plan
Every Part A-AC deliverable exists as real, running code or a real, evidence-backed document -- not a stub. Where a sub-requirement could not be fully exercised in this sandbox (e.g. a second reviewer physically reading a one-time key-reveal screen), that gap is named explicitly in `owner-residual-risk-register.md`, not silently claimed as done.
