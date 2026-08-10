# Phase 9 Milestone 6 — PostgreSQL Hardening

## Real work performed (this session, native local Postgres 17, not simulated)

A genuinely separate `aura_owner_staging` database was created on this machine's real Postgres 17
service (confirmed listening: `netstat` showed PID 6264 on `5432`), distinct from `aura_owner_dev` and
`aura_owner_test` — real environment/data separation, not a relabeled dev database.

```sql
CREATE DATABASE aura_owner_staging OWNER aura_owner;
ALTER DATABASE aura_owner_staging SET statement_timeout = '30s';
ALTER DATABASE aura_owner_staging SET idle_in_transaction_session_timeout = '60s';
ALTER DATABASE aura_owner_staging SET timezone = 'UTC';
```

Verified via `SHOW` against a real connection to `aura_owner_staging` (not just the `ALTER DATABASE`
command succeeding): `statement_timeout = 30s`, `idle_in_transaction_session_timeout = 1min`,
`timezone = UTC`, `autovacuum = on` (PostgreSQL 17 default, confirmed not disabled).

Real Alembic migration chain applied clean, all 6 revisions in order (`62e4adb0a7b9` ->
`0f8d55b753ed`), then real RBAC seed (69 permissions, 5 roles), catalog seed, offline-policy seed, a
real Ed25519 signing key generated and activated. `/health/ready` against this real staging DB:
`database_connectivity: OK`, `migration_at_head: OK`, `active_signing_key_exists: OK`,
`license_pepper_configured: OK` (a real generated pepper, not the dev placeholder).

## Known gap: dedicated least-privilege application role NOT created — documented honestly

Creating a distinct `aura_owner_staging_app` role (separate from `aura_owner`, no superuser/
CREATEDB/CREATEROLE) requires Postgres superuser or `CREATEROLE` privilege. The `aura_owner` role
available in this session has neither (`rolsuper = f`, `rolcreaterole = f`) and `pg_hba.conf` requires
`scram-sha-256` for every connection including `postgres` — no credential for the real superuser is
available this session. **This is a real, honestly-recorded gap**, not silently worked around: the
staging *database* is real and separate; the staging *role* currently reuses the dev `aura_owner`
credential, which is NOT the least-privilege posture Milestone 6 requires.

Exact SQL for a real deployment (where superuser access exists):

```sql
CREATE ROLE aura_owner_staging_app LOGIN PASSWORD '<from .env.staging>'
  CONNECTION LIMIT 20 NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;
ALTER ROLE aura_owner_staging_app SET statement_timeout = '30s';
ALTER ROLE aura_owner_staging_app SET idle_in_transaction_session_timeout = '60s';
GRANT ALL PRIVILEGES ON DATABASE aura_owner_staging TO aura_owner_staging_app;
ALTER DATABASE aura_owner_staging OWNER TO aura_owner_staging_app;
```

`docker-compose.staging.yml` already references `${STAGING_DB_USER}`/`${STAGING_DB_PASSWORD}` (not a
hardcoded name), so applying this SQL in a real deployment requires no compose-file change — only
running it once against the real Postgres instance before first deploy.

## No superuser application account

Confirmed: `aura_owner` (used this session) is `rolsuper = f`. The eventual dedicated staging role
above is explicitly `NOSUPERUSER`. Neither the app nor the migration runner needs Postgres superuser —
DDL privileges via database ownership are sufficient (already proven: the full migration chain applied
successfully as the non-superuser `aura_owner`).

## Secure network binding

This machine's Postgres has `listen_addresses = '*'` (binds all interfaces) — a real, if minor,
dev-machine observation, not a staging-relevant finding: in the real Compose-based staging deployment,
network exposure is controlled at the Docker layer (`docker-compose.staging.yml`'s `db` service has no
`ports:` mapping and sits on an `internal: true` network — see `network-and-trust-boundaries.md`), not
by `postgresql.conf`'s `listen_addresses`. A real remote host's Postgres, if ever run outside Docker,
should set `listen_addresses = 'localhost'` explicitly; not verified against a real host this session.

## TLS for database traffic leaving the host

Not applicable to this session's topology (Owner and Postgres are Docker-network-adjacent, traffic
never leaves the host) — required if a future real deployment moves to a managed/remote Postgres
service (see ADR-9.3's migration-friendly framing).

## Migration procedure

See `migration-runbook.md`. Slow-query visibility, storage monitoring, index-health review: see
`database-operational-checklist.md` and `observability-architecture.md` (Milestone 8).
