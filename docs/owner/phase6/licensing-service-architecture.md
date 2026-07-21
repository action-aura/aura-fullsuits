# Phase 6 -- Licensing Service Architecture

## New packages
- `owner/app/licensing_service/` -- the external-facing service layer: `canonical.py` (deterministic serialization), `signing.py` (Ed25519 server signing-key management), `device_identity.py` (device public-key registration/verification), `activation.py` (initial-activation protocol), `checkin.py` (authenticated check-in), `assertions.py` (signed assertion issuance/verification), `entitlements.py` (resolution engine), `offline_policy.py`, `replay.py` (nonce store), `ratelimit.py` (distributed, Postgres-backed), `idempotency.py`, `reason_codes.py`.
- `owner/app/api_external/` -- the versioned external Flask blueprint (`/api/licensing/v1/*`), completely separate from `owner/app/api/` (Phase 5's inactive future-contract stub, left as-is) and from every internal staff blueprint. Own error handlers, own before/after-request hooks, own logging discipline.
- `owner/tools/activation_simulator/` -- standalone CLI, imports nothing from `products/*`.

## Why a new `api_external` package instead of extending `app/api/`
Phase 5's `app/api/` was explicitly scoped as "inactive specifications + one read-only stub" (see `owner-api-contract-preparation.md`). Phase 6 needs a materially larger, security-critical surface (signature verification, replay protection, rate limiting, transactional device-limit enforcement) with its own hardening posture. Reusing the Phase 5 package would blur "this route group is a stub" with "this route group is the real external attack surface" -- keeping them separate packages makes the blast radius and review boundary explicit. Both remain gated by the same `OWNER_EXTERNAL_API_ENABLED` flag and the same "blueprint not even imported when disabled" pattern.

## Request flow (activation)
```
HTTP POST /api/licensing/v1/activations
  -> api_external/activation_routes.py (shape validation, size limit, content-type check)
  -> licensing_service/canonical.py (canonicalize for signature verification)
  -> licensing_service/replay.py (nonce + timestamp check, Postgres-backed)
  -> licensing_service/device_identity.py (verify Ed25519 signature over canonical payload)
  -> licensing_service/idempotency.py (check for a prior identical request)
  -> licensing_service/activation.py (license lookup by HMAC, all validation gates, row-locked
     device-limit check, installation register-or-reuse, activation-event record)
  -> licensing_service/entitlements.py (deterministic resolution)
  -> licensing_service/assertions.py (build + Ed25519-sign the assertion)
  -> single committed transaction
  -> canonical, allowlist-serialized JSON response
```
Every step before the transaction is read-only/stateless verification; the transaction itself (device-limit lock through activation-event + idempotency-record write) is the single atomic unit, matching Part E's 23-step sequence.

## Reuse of Phase 5 primitives (not reimplemented)
`app/security/license_keys.py::hash_license_secret`/`verify_license_key` (HMAC lookup, unchanged), `app/audit/services.py::record`/`redact` (extended with new action codes and forbidden-marker terms, not replaced), `app/security/rbac.py` decorators (used by the internal admin views in Part R), `app/extensions.py::db_session`/`get_engine` (same connection-pool pattern that Phase 5's backup fix already hardened).

## Diagram
```
Internal staff browser --> internal blueprints (unchanged, Phase 5)
Activation simulator   --> /api/licensing/v1/*  --> licensing_service/*  --> PostgreSQL (single DB, all tables)
                                                                          --> owner_security_nonce_records / rate-limit / idempotency tables (Part L/M/N, Postgres not Redis)
```
