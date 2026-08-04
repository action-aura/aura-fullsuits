# Phase 9R — Configuration Contract (M2)

## Environments

Four explicit environments, each its own `Config` subclass in
`owner/app/config.py`, selected via `OWNER_ENV`:

| Environment | `ENV` value | Secret validation | Cookie `Secure` | Notes |
|---|---|---|---|---|
| Development | `development` | Exempt (`validate()` returns immediately) | `False` | Local dev only, ships safe insecure defaults for every secret so a fresh checkout runs with zero setup |
| Test | n/a — `TESTING = True` | Exempt (same early return) | n/a | Used by the pytest suite; own database (`OWNER_TEST_DATABASE_URL`), own signing-key directory (`OWNER_TEST_SIGNING_KEY_DIRECTORY`), own attachment directory — never touches the dev database |
| Staging | `staging` | **Full production validation** | `True` | Distinct `ENV` value so logs/health/audit records are never mistakable for real production (an existing Phase 9 principle, unchanged) |
| Production | `production` | **Full production validation** | `True` | |

Staging is deliberately not a lighter-weight production — every fail-closed
check below applies identically to both.

## Fail-closed startup validation (`BaseConfig.validate()`)

Called once at application boot. In staging/production, raises `ConfigError`
(refuses to start) rather than logging a warning and continuing, for every
condition in this table:

| Condition | Check |
|---|---|
| Required secret absent | `OWNER_SECRET_KEY`, `OWNER_DATABASE_URL`, `OWNER_LICENSE_PEPPER` must all be set |
| Default/placeholder secret used | `SECRET_KEY`/`LICENSE_PEPPER` rejected if they match any known dev/test default marker (`insecure`, `dev-only`, `test-secret`, `test-license-pepper`, `changeme`, `placeholder`) |
| Weak secret | `SECRET_KEY`/`LICENSE_PEPPER` must be ≥32 characters |
| Malformed database URL | Must start with `postgresql://` or `postgresql+psycopg://` |
| Wrong-environment database | Rejected if the URL matches a known dev/test database name (`aura_owner_dev`, `aura_owner_test`) — **not** `localhost`, since a self-managed PostgreSQL co-located with the app (ADR-3) is a legitimate production topology |
| Insecure cookie configuration | `SESSION_COOKIE_SECURE` must be `True` |
| Trusted proxy configuration absent | `OWNER_TRUSTED_PROXY_COUNT` must be ≥1 (this deployment sits behind Caddy, M6) |
| Host allowlist absent | `OWNER_ALLOWED_HOSTS` must be non-empty |
| Backup target undefined | `OWNER_BACKUP_TARGET_URL` must be set and must be a remote URI (contains `://`), never a bare local path |
| Scheduler ownership ambiguous | `OWNER_SCHEDULER_ROLE` must be exactly `owner` or `worker`, set explicitly |
| Unknown configuration key (typo detection) | When `OWNER_STRICT_CONFIG=true`, any `OWNER_*` environment variable not in the known-variable set fails startup |
| Debug enabled | `DEBUG` must be `False` when the external API is enabled (existing Phase 6 check, `validate_external_api_production()`) |
| Licensing signing key directory missing | `OWNER_SIGNING_KEY_DIRECTORY` must exist when the external API is enabled (existing Phase 6 check) |
| Replay protection / distributed rate limiting disabled | Both must be `True` when the external API is enabled (existing Phase 6 checks) |

The last four rows are the pre-existing Phase 6
`validate_external_api_production()` checks, run after the new M2 checks
pass — Phase 9R extends the contract, it doesn't replace it.

## What is deliberately *not* an in-process runtime check

**Staging/production secret collision** (the same `OWNER_SECRET_KEY` reused
across two environments) cannot be detected by one process reading its own
environment — there is nothing in a single process's env to compare against.
This is enforced procedurally instead: the deployment pipeline (M16) is
required to generate each environment's secrets independently and never
copy a `.env` file between environments. Documented as an operational
invariant, not silently assumed safe.

## Safe example environment file

`owner/.env.example` (already existed, pre-Phase-9R) ships every variable
with either a safe non-secret default (timeouts, feature flags) or an empty
placeholder for anything sensitive — never a real value. Extended for M2's
new variables; see `environment-separation.md` for the diff.

## Full failure-mode test coverage

`owner/tests/test_phase9r_environment_separation.py` — one test per row in
the table above (both the "fails without it" and, where relevant, the
"doesn't wrongly fail a legitimate config" case, e.g. the co-located
`localhost` production database test), plus explicit tests proving
development and `TESTING=True` remain fully exempt by design.
