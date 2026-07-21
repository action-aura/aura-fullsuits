# Phase 6 -- Scope and Baseline

## Baseline verified before any change
- Tag `aura-owner-foundation-phase5-complete` resolves to commit `f91d993`, which equals `HEAD` on `master` -- zero drift.
- `git status --porcelain` clean.
- Full Phase 5 suite: **85/85 passing** against a real PostgreSQL 17 database (fresh `alembic downgrade base` + `upgrade head` on `aura_owner_test` immediately before the run).
- Inspected (not assumed) before building: `owner/app/models/licensing.py` (`License` -- `key_prefix`/`key_suffix_masked`/`key_secret_hmac`, no plaintext column), `owner/app/models/installations.py` (`Installation` -- already has an unused `device_public_key` placeholder text column from Phase 5's forward-looking design; Phase 6 adds a proper dedicated table instead of relying on that single column, see `device-identity-design.md`), `owner/app/config.py` (existing env var names: `OWNER_SECRET_KEY`, `OWNER_DATABASE_URL`, `OWNER_LICENSE_PEPPER`, `OWNER_EXTERNAL_API_ENABLED`, `REQUIRED_PRODUCTION_SECRETS`), `owner/app/security/rbac.py` (`require_permission`, `require_recent_auth`, `require_login` decorators), `owner/app/audit/services.py` (`record()`/`redact()`/hash-chain pattern), `owner/app/api/routes.py` + `owner/app/__init__.py`'s conditional blueprint registration (the existing, already-correct "disabled by default, blueprint not even imported" pattern Phase 6 extends rather than replaces).

## Scope discipline (binding)
Phase 6 builds the **service authority and simulator only**. No changes to `products/retail/*`, `products/clinic/*`, `android/*`, or the original `AuraEnterprise/` repo. No license enforcement added to either product. No public internet exposure -- localhost/isolated test execution only, exactly as Phase 5 established for the external API surface.

## Key scope decision made during discovery: PostgreSQL over Redis
The spec allows either, "prefer Redis unless a PostgreSQL-backed design is demonstrably more reliable for this project." This sandbox has no Docker and no native Windows Redis support (Redis Labs ships no official Windows build since v5; the only options are WSL2 or a third-party port, neither of which exists in this environment, matching the exact same constraint Phase 5 already worked around for Postgres itself). PostgreSQL is already Owner's single system of record, already proven under concurrent-transaction load in Phase 5's device-limit-safe design precedent (`system/backup.py`'s lock-ordering fix), and is equally "shared across processes" as Redis would be, since every Owner worker process already connects to the same Postgres instance -- satisfying the spec's actual requirement (a shared store reachable by all workers), not a specific product choice. Full reasoning: `cryptographic-design-decision.md`.

## What "done" means for Phase 6
Every Part A-AC deliverable is real, running code or a real, evidence-backed document. The product-side activation simulator (Part U) is the authoritative integration proof, run against a real local PostgreSQL-backed Owner instance with the external API enabled, using only synthetic data.
