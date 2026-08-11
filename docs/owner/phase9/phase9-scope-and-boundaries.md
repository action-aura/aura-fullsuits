# Phase 9 — Scope and Boundaries

## Objective

Build a secure, reproducible, production-like staging deployment stack for Aura Owner and prepare
(but do not execute) a controlled paid pilot — without claiming public-production readiness.

## Explicitly forbidden (unchanged from the governing instruction)

Phase 10; Aura Owner mobile (Android/iOS); Clinic/Retail iOS; app-store publication; public
self-service signup/checkout; payment-gateway integration; card/bank data storage; automatic billing;
automatic product updates; WhatsApp/SMS integration; e-invoicing; Aura Core integration; unrelated
product-feature work; real patient/customer data in staging; unrestricted internet exposure; disabled
TLS verification; trust-on-first-use; plaintext secrets; public database exposure; committed
production credentials; automatic deploy to a real production environment; describing staging as
production.

## Local-only decision (recorded, user-confirmed)

No cloud/VPS account, domain, or remote host is available in this session. The user was asked directly
and chose the spec's own documented fallback path: complete and validate the full deployment stack
locally; mark every milestone that requires a real remotely-reachable, HTTPS-verified host as **NOT
VERIFIED**; do not fabricate access; do not create the Phase 9 completion tag this session.

This changes *verification status*, not *scope* — every local milestone (architecture, packaging,
hardening, Postgres operations, backup/restore, observability, scheduling, security review, CI
definition, pilot/incident/privacy runbooks, capacity testing, regression) is executed for real, not
theoretically documented.

## What "local staging" means concretely in this session

- A real Docker Compose stack (reverse proxy + Owner + Postgres) runnable on this machine, using a
  self-signed/local CA certificate rather than a publicly-trusted one (a real trust boundary can still
  be demonstrated; a *public* one cannot).
- A real local Postgres instance, hardened per Milestone 6, with a real backup taken and a real restore
  performed into a second, isolated local database (not the live one) — this is fully achievable
  without any cloud dependency and is treated as real evidence, not a substitute.
- A real local monitoring stack (e.g. a lightweight scrape/alerting setup) observing the real local
  Owner process.
- Real security/dependency/secret scans against the real repository and the real local artifacts.
- The staging-connected Android/Windows rc.6 artifacts, private distribution, and pilot onboarding
  milestones require a real HTTPS staging URL that does not exist this session — these remain **NOT
  VERIFIED**, not fabricated, not skipped silently.

## Retail test-suite isolation (Milestone 2)

Treated as a real Phase 9 engineering-quality gate, independent of infrastructure availability — fully
achievable and required regardless of local-vs-remote staging.
