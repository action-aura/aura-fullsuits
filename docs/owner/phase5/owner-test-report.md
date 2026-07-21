# Phase 5 -- Owner Test Report (Part Z)

## Automated suite: 78/78 passing
Run command: `OWNER_TEST_DATABASE_URL=postgresql+psycopg://aura_owner:aura_owner_dev@localhost:5432/aura_owner_test python -m pytest owner/tests/ -q` against a real PostgreSQL 17 database (migrated fresh via the session-scoped `_migrated_schema` fixture), full clean run: **78 passed in 87.27s, 0 failed, 0 skipped**.

| File | Tests | Covers |
|---|---|---|
| `test_auth.py` | 13 | bootstrap-no-defaults, wrong password, unknown-email generic error, throttling/lockout, disabled account, MFA-required login flow, MFA verify (wrong/correct code), recovery-code one-time use, idle-session timeout, logout revocation, password-change session invalidation |
| `test_rbac.py` | 8 | Viewer read-only, direct-API-call rejection by role, Support blocked from pricing, Sales blocked from staff admin, Finance blocked from licenses, unauthenticated redirect, Super Admin bypass |
| `test_customers.py` | 4 | create/view, duplicate detection (flagged not blocked), archive (soft, not hard delete), contact/note management |
| `test_catalog.py` | 5 | seed idempotency, product-platform mapping, price history never overwritten, invalid addon status rejected, unbuilt add-ons never marked AVAILABLE |
| `test_subscriptions.py` | 5 | valid/invalid transitions, status history with reason, terminal-state rejection, payment status validation |
| `test_licensing.py` | 9 | key format/entropy, non-determinism (50 unique keys), plaintext never persisted, one-time reveal + idempotent replay, secret never in audit, HMAC verify (wrong pepper fails), invalid transition rejected, permission + recent-auth gating |
| `test_installations.py` | 3 | no raw hardware ID columns, valid/invalid transitions, activation event auto-recorded |
| `test_audit.py` | 5 | audit created for sensitive write, secret redaction, hash-chain linkage, tamper detection, no update/delete route exists |
| `test_data_boundary.py` | 6 | no forbidden column/table names, serializer construction guard, fixed-allowlist output, no cross-package imports, external API off by default |
| `test_security.py` | 9 | CSRF required, security headers present, cookie flags, XSS escaping (live payload), SQL-injection-style input handled safely, privilege-escalation recent-auth gate, invitation one-time-use, expired invitation rejected, no stack traces leaked |
| `test_database.py` | 5 | migration up/down from empty on a disposable scratch DB, zero schema drift vs. models, unique constraint enforced, foreign key enforced, audit-log table indexed/queryable |
| `test_backup_restore.py` | 6 | backup succeeds + checksum recorded, backup failure recorded honestly, restore rejects tampered checksum, pre-restore safety backup provably created, **restore actually recovers deleted data**, backup route permission-gated |

## A real deadlock found and fixed via this suite
`test_backup_restore.py`'s restore tests initially hung (300s timeout) due to a genuine lock-ordering bug between the app's own open DB connection and `pg_restore --clean`'s DROP statements -- see `owner-database-backup-and-recovery.md` for the fix and re-verification. This is exactly the value of writing and running real tests against a real database rather than mocking the restore path.

## Manual end-to-end HTTP verification (live server, real Postgres, no mocks)
Beyond the automated suite, a full live walkthrough was performed against a running `flask run` instance via `curl` with a real cookie jar: Super Admin bootstrap -> login -> forced MFA enrollment (real TOTP code computed from the displayed secret) -> dashboard/customers/catalog/staff/audit pages all 200 -> create customer -> create plan -> create subscription -> create license -> issue license key (real one-time reveal, `AURA-CLN-1-KG6G-ZCRF-U37N-WF63-5RPM`) -> **replay same issuance request confirmed 0 occurrences of the key in the response** -> recent-auth window genuinely expired mid-session and correctly redirected to reauth -> reauth with a fresh TOTP code succeeded -> register installation -> audit hash-chain verify returned OK. The dev database was then reset (`alembic downgrade base` + `upgrade head` + re-seed) to leave it in the canonical seed-only state per Part AA.

## What was NOT exercised
No load/performance testing. No multi-browser/concurrent-session stress testing beyond the two-client session-revocation test. No CI pipeline configured to run this suite automatically (manual `pytest` invocation only, this phase). See `owner-residual-risk-register.md`.
