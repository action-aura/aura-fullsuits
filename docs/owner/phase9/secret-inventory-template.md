# Phase 9 — Secret Inventory Template

Names and ownership only. No value ever recorded here or anywhere in the repository.

| Secret name (env var) | Purpose | Owning role | Storage location | Rotation trigger |
|---|---|---|---|---|
| `STAGING_DB_USER` | Postgres application role username | Infra operator | `.env.staging` (out of repo) | Credential compromise suspected |
| `STAGING_DB_PASSWORD` | Postgres application role password | Infra operator | `.env.staging` | Every 90 days or on compromise |
| `STAGING_DB_NAME` | Database name (not secret, listed for completeness) | Infra operator | `.env.staging` | n/a |
| `OWNER_SECRET_KEY` | Flask session-signing key | Infra operator | `.env.staging` | On compromise; invalidates all active sessions |
| `OWNER_LICENSE_PEPPER` | License-key HMAC pepper | Commercial ops owner + infra operator (dual awareness) | `.env.staging` | Only at a deliberate pilot reset — see `key-rotation-runbook.md` (destructive to existing licenses) |
| `OWNER_SIGNING_KEY_DIRECTORY` contents (Ed25519 private key) | Assertion signing | Infra operator | Docker named volume `owner_signing_keys_staging`, never in Postgres, never network-attached | Per `key-rotation-runbook.md` |
| Backup encryption key | Encrypts `pg_dump` output at rest | Infra operator | Separate from the DB itself; real deployment: a KMS-managed key or a passphrase in a distinct restricted file | On compromise or scheduled per `backup-policy.md` |
| SSH private key(s) | Operator access to the staging host | Individual operator (never shared) | Operator's own machine only, never on the staging host | Per operator offboarding |
| `STAGING_DOMAIN` | Caddy's TLS/reverse-proxy hostname | Infra operator | `.env.staging` (not secret, but environment-specific — listed for completeness) | On domain change |

Android/Windows code-signing credentials are out of scope for this table — see
`release-workstation-runbook.md` (they live on the release workstation, never on the staging host, and
were not touched this phase since no product code changed).
