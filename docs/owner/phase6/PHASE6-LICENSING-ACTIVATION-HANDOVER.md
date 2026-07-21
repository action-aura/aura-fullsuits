# Phase 6 -- Licensing & Activation Service -- Handover

## 1. What was built
A real, working external Licensing & Activation Service for Aura Owner: `POST /api/licensing/v1/activations` (device-signed, license-key-verified, device-limit-safe under real concurrency), `POST /api/licensing/v1/check-ins` (authenticated, no license key required), `POST /api/licensing/v1/deactivations` (idempotent), `GET /api/licensing/v1/signing-keys` (public key discovery), `GET /api/licensing/v1/service-info`. Server-signed Ed25519 assertions, a deterministic entitlement-resolution engine, an offline-grace policy authority (define-and-return only), PostgreSQL-backed replay protection/distributed rate limiting/idempotency (ADR-6.3, no Redis dependency added), full lifecycle integration (suspend/reactivate/revoke/replace/device-replace), an internal admin UI, extended RBAC, extended audit, and a standalone product-side activation simulator that is this phase's authoritative integration proof.

## 2. Scope discipline maintained
Zero changes to `products/retail/*`, `products/clinic/*`, `android/*`, or the original `AuraEnterprise/` repo. No license enforcement added to either product. No public internet exposure. No Phase 7 (subscription-expiry enforcement, payment gateway, e-invoicing, Aura Core integration) begun.

## 3. Test results
**179/179 automated tests passing** (85 Phase 5 + 94 Phase 6) against a real PostgreSQL database -- no mocks anywhere in the critical path. Real concurrency proven with actual threads. Three real bugs found and fixed during this phase's own development (missing ORM relationships, CSRF blocking the external API, a deactivation-idempotency signature-verification ordering bug) -- see `phase6-test-report.md`.

## 4. End-to-end proof
`activation-e2e-evidence.md` -- a full live run via the real simulator CLI against a real running Owner instance: device init, activation, independent assertion verification against published keys, check-in with no license key, replay rejection, signature-tampering rejection, deactivation. All 26 steps of the governing spec's Part Z performed with real commands and real output.

## 5. Files created
- `owner/app/licensing_service/` -- 13 modules (canonical, reason_codes, signing, device_identity, replay, ratelimit, idempotency, activation, checkin, deactivation, assertions, entitlements, offline_policy, health).
- `owner/app/api_external/` -- the versioned external blueprint.
- `owner/app/licensing_admin/` -- internal admin UI (6 templates).
- `owner/app/models/licensing_service.py` -- 12 new tables.
- `owner/migrations/versions/60f363ee66e8_*.py` -- the Phase 6 migration.
- `owner/tools/activation_simulator/` -- the standalone CLI.
- `owner/tests/test_phase6_*.py` -- 12 files, 94 tests.
- `docs/owner/phase6/` -- 26 documents (this file plus 25 others).

## 6. Files modified (additive only, per scope discipline)
`owner/app/config.py` (new env vars + production-mode validation), `owner/app/__init__.py` (blueprint wiring), `owner/app/cli.py` (signing-key + offline-policy commands), `owner/app/staff/seed_data.py` (13 new permissions), `owner/app/licensing/routes.py` (reactivate permission fix), `owner/app/models/licensing.py` / `installations.py` (added missing ORM relationships + one unique constraint, no columns removed), `owner/app/installations/services.py` (one new event type), `owner/tests/conftest.py` / `test_data_boundary.py` (Phase 6 test helpers; one Phase 5 test re-targeted at the real invariant), `.gitignore` (signing-key/device-key exclusions), `requirements/owner-server.txt` (unchanged this phase -- all Phase 6 crypto needs were already covered by Phase 5's `cryptography` dependency).

## 7. Known limitations (full detail: `phase6-residual-risk-register.md`)
Unused `pyjwt` dependency not yet removed; `cryptography`/`flask`/`werkzeug` version upgrades flagged, not performed (out of Phase 6's scope); no license-HMAC-derived rate-limit bucket dimension yet; no scheduled cleanup job for expired nonce/idempotency/rate-limit rows; no CI (carried from Phase 5).

## 8. What Phase 7/8 would need to decide
Whether and how to actually wire license enforcement into Retail/Clinic (not begun, not implied, by this phase); subscription-expiry enforcement; payment-gateway integration; national e-invoicing; Aura Core integration. All explicitly out of Phase 6's scope per the governing spec.

## 9. Stop condition
Per the governing spec: **stop completely after Phase 6.** No Phase 7 work begins without new, explicit authorization.
