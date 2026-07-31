# Phase 9 Milestone 5 — Secrets and Key Management

## Mechanism chosen: `.env.staging`, outside the repository, loaded by Docker Compose's `env_file`

Evaluated against the spec's own list (restricted env file / systemd credentials / Docker secrets /
cloud secret manager): a single, restricted, out-of-repo `.env.staging` file was chosen over Docker
Swarm secrets or a cloud secret manager because this is a single-host Compose deployment (Milestone 1
ADR-9.3) — Swarm secrets require Swarm mode (not used here), and a cloud secret manager would be the
first cloud dependency introduced anywhere in this stack for no corresponding benefit at pilot scale.
`docker-compose.staging.yml` already only references secrets via `env_file: - .env.staging` and
`${VAR}` interpolation — no value is ever inlined in a tracked file.

## What must exist in `.env.staging` (names only — see `secret-inventory-template.md`)

`STAGING_DB_USER`, `STAGING_DB_PASSWORD`, `STAGING_DB_NAME`, `OWNER_SECRET_KEY`,
`OWNER_LICENSE_PEPPER`, `STAGING_DOMAIN`, plus whatever the backup/monitoring services need
(Milestones 7-8).

## Enforcement already real, reused not reinvented

`BaseConfig.validate()` (Phase 6/8) already refuses to start in any non-development,
non-testing environment when `OWNER_SECRET_KEY`, `OWNER_DATABASE_URL`, or `OWNER_LICENSE_PEPPER` is
missing — `StagingConfig` (Milestone 3) inherits this unchanged. `validate_external_api_production()`
additionally refuses to start with the external licensing API enabled if the pepper is empty or the
known-insecure placeholder, if the signing-key directory doesn't exist, if replay protection or
distributed rate limiting is disabled, if `DEBUG` is true, or if `SESSION_COOKIE_SECURE` is false — all
already apply to staging with no change needed.

## No secret in Git — verified, not assumed

`.dockerignore` excludes `.env*`, `var/signing-keys`, `var/backups`. `.gitignore` (pre-existing) already
excludes `.env`; confirmed staging follows the identical `.env.staging` naming so the same ignore
pattern (`.env*`) covers it. Milestone 10's real secret scan (`gitleaks`/equivalent) is the actual proof
this holds, not just the ignore-file's presence.

## File permissions and ownership (real deployment)

`.env.staging`: owner `deploy` user only, mode `600`. Signing-key volume: owned by the container's
`aura` user (UID mapped from the host), `700` on the directory. Backup volume: `deploy` user, `700`.

## Startup failure when a required secret is missing

Already real and tested (Phase 8, `owner/tests/*config*` and `test_commercial_ops_preflight.py`):
`ConfigError` raised at `create_app()` time, process never starts, no partial/degraded startup.

## Redacted diagnostic output

Already real: `flask commercial preflight` and `/health/ready` (Milestone 3) both report only
name/status, never a secret value — confirmed by test.

## Staging key separation (Non-Negotiable, enforced by construction)

`docker-compose.staging.yml`'s `owner_signing_keys_staging` volume is a distinct named volume from the
dev compose's `owner_signing_keys` — a fresh `flask licensing generate-signing-key` run against
staging produces a key that has never existed in dev, and `OWNER_LICENSE_PEPPER` in `.env.staging` must
be a freshly generated value, never copied from `.env` (dev) or any future `.env.production`. No
automation in this repo can accidentally copy one environment's pepper/key into another's — they are
never read from a shared location.

## Rotation, revocation, backup, recovery

See `key-rotation-runbook.md`.

## Dual control for future production signing keys (recommendation, not implemented)

Recorded as a requirement for the real future-production environment (not staging, not built this
phase): the production signing private key should require two authorized operators to export/rotate
(e.g. split via `age`/Shamir secret sharing, or a hardware security module), never a single operator's
unilateral action. Staging does not need this — its blast radius on compromise is "staging pilot
clients trust a wrong key", not "every real customer's licensing trust is compromised".
