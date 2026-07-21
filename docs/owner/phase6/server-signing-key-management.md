# Phase 6 -- Server Signing-Key Management (Part I)

## Storage model
`owner_signing_keys` (Postgres) stores only public, non-secret metadata: `key_id`, `algorithm`, `public_key` (base64), `status`, and lifecycle timestamps. The **private** key half is a PEM file at `{OWNER_SIGNING_KEY_DIRECTORY}/{key_id}.pem`, `0o600` permissions, created with `O_EXCL` (refuses to silently overwrite an existing file), path-traversal-checked (`signing.py::_safe_key_path` rejects any `key_id` that isn't a plain `[A-Za-z0-9._-]` token and verifies the resolved path stays inside the configured directory). Private key material is never written to any database table, never logged, never shown in the Owner UI (`licensing_admin/signing_keys.html` displays `key_id`/`algorithm`/`status`/timestamps only).

## Lifecycle
`DRAFT` (generated, not yet trusted for issuance) → `ACTIVE` (the one key currently used to sign new assertions -- `activate_signing_key()` atomically retires whichever key was previously active) → `RETIRED` (no longer used for new issuance, but its signature remains **valid** for verification, so assertions it already signed keep working through their own `expires_at`) → `REVOKED` (compromise response only -- a revoked key's signature is **never** trusted again, even for a previously-issued, still-unexpired assertion; verified by `test_phase6_crypto.py::test_revoked_signing_key_never_trusted`).

## CLI (never prints private-key material)
```
flask licensing generate-signing-key            # DRAFT
flask licensing activate-signing-key <key_id>    # -> ACTIVE, retires the previous ACTIVE
flask licensing rotate-signing-key [--reason]    # generate + activate in one step
flask licensing export-public-keys               # same JSON shape as GET /signing-keys
flask licensing verify-signing-key-health         # real sign/verify round-trip against the active key
```

## Startup safety
`is_service_ready()` (`health.py`) checks `get_active_signing_key() is not None` before processing any external request -- if no `ACTIVE` key exists, every activation/check-in/deactivation request is rejected `503 SIGNING_KEY_UNAVAILABLE` rather than silently proceeding unsigned (verified live: `test_phase6_activation_protocol.py::test_service_unavailable_without_active_signing_key`). In non-development `OWNER_EXTERNAL_API_ENABLED=true` mode, `config.py::validate_external_api_production()` additionally refuses to even *start* the process if `OWNER_SIGNING_KEY_DIRECTORY` doesn't exist as a real directory.

## Backups
Owner's PostgreSQL backup (Phase 5, `system/backup.py`) covers `owner_signing_keys`' public metadata automatically as part of the normal database dump -- it does **not** include the private PEM files, which live outside the database entirely. A separate, explicitly-secured key-backup process (e.g. an encrypted offline copy of `OWNER_SIGNING_KEY_DIRECTORY`) is a deployment-operator responsibility documented here but not automated by this phase, per Part I's "excluded from Owner database backups unless an explicit, separately secured key-backup process is documented."

## `.gitignore` defenses
`owner/var/signing-keys/`, `owner/var/signing-keys-test/`, and `*.pem` are all excluded (added this phase). No signing key of any kind -- development, test, or otherwise -- has ever been committed.
