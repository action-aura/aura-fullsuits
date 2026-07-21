# Phase 5 -- Aura Owner Architecture Decision Record

## ADR-1: Independent codebase, no shared runtime import
**Decision**: `owner/` imports nothing from `products/*/backend` or `commercial_runtime/*`. Shared logic is duplicated (in small, owner-scoped form) rather than imported.
**Why**: Part B's explicit rule ("Aura Owner must not import product-specific Retail or Clinic runtime modules") and Principle 4 (Product Data Separation). Even generic-looking shared code (e.g. `commercial_runtime/security/passwords.py`) is product-coupled by convention (PBKDF2, chosen specifically for Android/Chaquopy stdlib-only constraints) and not the right primitive for a server-only app.
**Consequence**: Owner has its own password-hashing module (Argon2id, not PBKDF2 -- see ADR-2), its own session/audit code. Minor duplication of *concepts* (hash-and-verify pattern), zero duplication of *product* code.

## ADR-2: Argon2id for staff password hashing, not PBKDF2
**Decision**: Use `argon2-cffi` (already curated in `requirements/owner-server.txt`) for staff password hashing.
**Why**: Retail/Clinic use PBKDF2-HMAC-SHA256 specifically because Android's Chaquopy runtime is pure-Python-stdlib-only -- that constraint does not apply to Owner (server-only, Docker/gunicorn). Argon2id is the stronger, currently-recommended choice (OWASP) when no stdlib-only constraint exists, and Part E requires "a vetted adaptive password-hashing implementation."

## ADR-3: PostgreSQL as sole system of record; no SQLite fallback anywhere, including tests
**Decision**: Every environment (dev, test, prod) targets real PostgreSQL. Test database is a second real Postgres database (`aura_owner_test`), not SQLite.
**Why**: Principle 8 is explicit ("Do not use SQLite as the production database"). Using SQLite only in tests would silently permit Postgres-only features (native `UUID`, `JSONB`, partial/unique constraints, `ON CONFLICT`) to go untested against the real engine, which is exactly the kind of gap Wave 1C's entire audit culture (re-verify against evidence, not assumption) argues against.
**Consequence**: A local PostgreSQL 17 instance is required to run migrations or tests. Documented in `owner-local-development-guide.md`; this dependency, and the fact that this sandboxed session had neither Docker nor Postgres pre-installed, is disclosed honestly rather than worked around with a weaker substitute.

## ADR-4: SQLAlchemy 2.x + Alembic, Flask application factory
**Decision**: Match the exact stack already curated in `requirements/owner-server.txt` (Flask, SQLAlchemy 2.0.32, Alembic 1.13.2, psycopg3, argon2-cffi, PyJWT) plus `pyotp` (added this phase; TOTP MFA has no existing curated dependency).
**Why**: Part A instructs reusing repository evidence over inventing new choices; `owner-server.txt` already exists, pre-committed, naming this exact stack -- treated as a binding prior decision, not merely a suggestion.

## ADR-5: Server-rendered Jinja UI, no SPA framework
**Decision**: Jinja2 templates + minimal vanilla JS (progressive enhancement: confirm dialogs, client-side filter/sort hints only). No React/Vue/build step.
**Why**: Part V / Part B explicitly warn against introducing a large frontend framework "solely for appearance," and Retail/Clinic's own web UIs follow the same server-rendered pattern (repository convention).

## ADR-6: Permission-code RBAC, not role-name checks
**Decision**: Every route decorated with `@require_permission("catalog.manage_plans")`-style checks against a `owner_role_permissions` join table, never `if role == "SALES"`.
**Why**: Part G's explicit instruction; also makes the five seed roles reconfigurable later without code changes.

## ADR-7: Public UUIDs on every externally-referenceable entity
**Decision**: All tables use a Postgres-native `UUID` primary key (`gen_random_uuid()`, `pgcrypto`/`uuid-ossp` not required -- Python-side `uuid4()` default, sent to Postgres as native `UUID` type) as their public identifier. No auto-increment integer ID is ever exposed through a URL, API response, or license/installation identifier.
**Why**: Part D ("Do not store human-readable sequential database IDs as external security identifiers") and Part X (enumeration prevention).

## ADR-8: Audit hash chain via per-row `previous_hash`/`current_hash`, computed in the audit-write service, not a DB trigger
**Decision**: `owner_audit_log.current_hash = sha256(previous_hash + canonical_json(row_fields))`, computed in Python inside the single `audit.record()` service function that is the only writer to the table.
**Why**: Keeps the tamper-evidence logic testable and portable (no dependency on Postgres-specific trigger/procedural-language features), while still being genuinely tamper-evident: any row edited outside that function breaks the chain, detectably, on the next `verify_chain()` pass.

## ADR-9: License secret storage -- HMAC-SHA256 with a server-side pepper, never reversible encryption
**Decision**: Store `hmac_sha256(pepper, secret_portion)` for the license key's secret component; never store the plaintext, never use reversible encryption.
**Why**: Part O is explicit ("no reversible encryption requirement... store only a secure hash/HMAC"). HMAC (not a plain hash) because a keyed pepper stops offline brute-forcing of the keyspace even if the database leaks without the pepper.
