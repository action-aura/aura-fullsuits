# Phase 6 -- Test Report (Part Y)

## Combined suite: 179/179 passing
`OWNER_TEST_DATABASE_URL=... python -m pytest owner/tests/ -q` against a real PostgreSQL 17 database, fresh `alembic downgrade base && upgrade head` immediately before the run: **179 passed, 0 failed, 0 skipped** in ~3m20s (85 Phase 5 + 94 Phase 6).

| File | Tests | Covers |
|---|---|---|
| `test_phase6_crypto.py` | 18 | canonical determinism, signing-key generate/activate/rotate/health/path-traversal, device signature verify/tamper/wrong-key/malformed/unsupported-algorithm, assertion sign/verify/tamper/unknown-key/expired/not-yet-valid/forbidden-field/revoked-key |
| `test_phase6_security_controls.py` | 13 | nonce reuse/scoping/timestamp-window, rate-limit enforcement/independence/no-plaintext-secret, idempotency replay/conflict/no-match |
| `test_phase6_activation_protocol.py` | 15 | valid activation, invalid license, suspended/revoked license, wrong product/platform/contract-version, device limit, idempotent retry, replay rejection, invalid signature, no-plaintext-anywhere, malformed/wrong-content-type/no-signing-key |
| `test_phase6_checkin_protocol.py` | 10 | valid check-in with no license key, device-key mismatch, stolen-installation-ID, unknown installation, replay, suspended license, revoked device key, last-check-in update, deactivate-then-blocked, deactivation idempotency |
| `test_phase6_concurrency.py` | 2 | simultaneous final-slot activation (real threads), rollback releases capacity |
| `test_phase6_entitlements.py` | 7 | deny-by-default, plan/license-override precedence, unavailable-addon exclusion, negative/wrong-type rejection, determinism |
| `test_phase6_offline_policy.py` | 4 | safe non-unlimited defaults, no-destructive-enum-option, default fallback, explicit assignment |
| `test_phase6_lifecycle.py` | 5 | suspend blocks activation, reactivation requires recent-auth, reactivation restores check-in, revocation blocks check-in, device replacement revokes old key |
| `test_phase6_rbac.py` | 7 | Support/Sales/Finance/Viewer/Super-Admin boundaries on the new admin surface, unauthenticated redirect |
| `test_phase6_audit.py` | 4 | activation audited, signing-key ops audited, no secret leakage, hash chain intact after Phase 6 activity |
| `test_phase6_data_boundary.py` | 5 | no forbidden term in Phase 6 schema, assertion guard, no cross-package import, live response scan, service-info leak scan |
| `test_phase6_migration.py` | 4 | populated-DB upgrade, schema drift, unique constraints (HMAC, nonce) |

## Real bugs found and fixed by this suite (not hypothetical -- each reproduced red, then green)
1. **Deactivation idempotency broke by design**: a retried deactivation failed signature verification because the first call had already revoked the device key being verified against. Fixed via `get_most_recent_device_key()`. See `idempotency-design.md`.
2. **Missing ORM relationships** (`Installation.platform`, `Installation.license`, `License.allowed_release_channel`) caused `AttributeError` the first time a real assertion was built -- caught during manual live verification (curl), not by an initially-incomplete unit test, then confirmed fixed by the full automated suite passing afterward.
3. **CSRF blocking the external API**: Flask-WTF's global CSRF protection initially rejected every external POST (device-signature-authenticated APIs have no session to carry a CSRF token in) -- fixed with an explicit `csrf.exempt()` on the licensing blueprint, found during the same live manual verification pass.

## Manual end-to-end verification
See `activation-e2e-evidence.md` -- a full live run via the actual simulator CLI against a real running Owner instance, real PostgreSQL, real Ed25519 cryptography, zero mocks.

## What was NOT exercised
Redis-specific failure modes (not applicable -- ADR-6.3). True multi-host distributed rate limiting (only multi-process-on-one-database, which is what "distributed" means for this deployment model). Load/performance testing beyond the 2-thread concurrency proof.
