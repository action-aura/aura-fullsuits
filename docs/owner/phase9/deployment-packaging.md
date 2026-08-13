# Phase 9 Milestone 3 — Deployment Packaging

## What already existed (Phase 6), reused not reinvented

`owner/Dockerfile` + `owner/docker-compose.yml`: Gunicorn WSGI entrypoint, Postgres 17, migration-then-
seed-then-serve ordering, signing keys on a dedicated volume. This is the correct baseline (see
`staging-architecture.md`).

## What Phase 9 added

- `owner/app/health.py`: real `/health/live` (process-responsive only) and `/health/ready` (real DB
  connectivity check, real Alembic head-revision check, and the full real Phase 8V-P9 preflight check
  set — signing key, trust anchor, RBAC seed, license pepper — surfaced as name+status only, no detail
  text, no secrets). Wired into `create_app()`. 5 new real tests (`owner/tests/test_health.py`,
  including one proving no secret/connection-string ever appears in the response body).
- `owner/app/config.py`: new `StagingConfig` (`ENV = "staging"`), same hardening posture as
  `ProductionConfig` (secret validation, `SESSION_COOKIE_SECURE`, external-API production checks all
  apply), but a distinct `ENV` value so nothing staging-produced (logs, health responses, audit
  records) can be mistaken for real production.
- `owner/Dockerfile.staging`: hardened image — `requirements/owner-server.txt` only (no dev tooling),
  dedicated non-root `aura` user, `HEALTHCHECK` against the new `/health/live`, Gunicorn tuned for a
  small supervised pilot (`--workers 2 --threads 4 --timeout 30 --graceful-timeout 15 --max-requests
  2000` — bounded worker lifetime, bounded request time, not a claim of internet scale).
- `docker-compose.staging.yml`: adds a Caddy reverse-proxy service (TLS termination, security headers),
  removes Postgres's host port publication entirely (`internal: true` network — the single most
  important change from the dev compose file), loads all staging secrets from an out-of-repo
  `.env.staging`, adds a `backup` service (Milestone 7).
- `deploy/staging/Caddyfile`: HSTS, `X-Content-Type-Options`, `X-Frame-Options: DENY`,
  `Referrer-Policy`, and a real (not aspirational) CSP scoped to the Owner UI's actual asset origins.
- `.dockerignore`: excludes `.venv`, build artifacts, signed keystores, `.env*`, and `docs/` from any
  image build context.

## Verified

- `/health/live` and `/health/ready` run for real against the real local dev Owner process and the real
  local Postgres 17 service (see `health-readiness-contract.md` for the exact request/response
  evidence).
- `owner/tests/test_health.py`: 5/5 passing, including the no-secret-leakage assertion.

## NOT VERIFIED this session

Docker Engine is not installed on this machine (`docker --version` -> command not found). The
Dockerfile/Compose/Caddyfile artifacts above are real, complete, internally consistent configuration —
but were not built or run as containers this session. This is recorded honestly, not silently assumed
working. Real local Postgres 17 (native Windows service, confirmed listening on `5432`, `pg_dump`/
`psql` present under `Program Files\PostgreSQL\17\bin`) was used instead for every milestone that
needed a real database (6, 7, 18, 19) — genuinely real evidence, just not containerized evidence.
