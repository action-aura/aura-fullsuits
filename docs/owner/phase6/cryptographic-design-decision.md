# Phase 6 -- Cryptographic Design Decisions

## ADR-6.1: Ed25519 for both device identity and server signing
**Decision**: Ed25519 (via `cryptography.hazmat.primitives.asymmetric.ed25519`, already a Phase-5 dependency -- no new package needed) for device proof-of-possession signatures, server-signed assertions, and server-signed public-key-set responses.
**Why**: Small keys (32-byte public, 32-byte private), small signatures (64 bytes), deterministic (no nonce-reuse signature-forgery class that plagues ECDSA), fast verification suitable for every check-in, broad library support for a future Kotlin/Python product-side client. No documented reason in this project favors an alternative.

## ADR-6.2: HMAC-SHA256 license-key lookup unchanged from Phase 5
**Decision**: Reuse `app/security/license_keys.py::hash_license_secret`/`verify_license_key` exactly as built in Phase 5 -- no new algorithm introduced for license-key matching.
**Why**: Already reviewed, tested, and pepper-keyed; Part E's "HMAC/hash the submitted license key using the configured server pepper" describes the existing mechanism precisely. Introducing a second license-hashing scheme would be needless duplication.

## ADR-6.3: PostgreSQL, not Redis, for nonce/idempotency/rate-limit state
**Decision**: `owner_security_nonce_records`, `owner_external_idempotency_records`, and a Postgres-backed rate-limit counter table, all with strict TTL-style expiry columns and indexed lookups -- no Redis dependency added.
**Why** (the spec's own escape hatch, exercised): this sandbox has no Docker and no native Windows Redis; Postgres is already Owner's system of record and already proven safe under Phase 5's own concurrent-transaction hardening; a "distributed" requirement means "shared across every Owner worker process," which Postgres satisfies identically to Redis since both are already the one thing every worker connects to. Full detail: `phase6-scope-and-baseline.md`.
**Consequence**: Slightly higher latency per nonce-check than an in-memory Redis lookup (a few ms, immaterial for a licensing check-in cadence measured in minutes) in exchange for zero new infrastructure and zero new failure mode to reason about. `OWNER_REPLAY_PROTECTION_REQUIRED=true` still fails closed if Postgres itself is unavailable -- there's no weaker fallback.

## ADR-6.4: Canonical serialization -- sorted-key, separator-compact JSON (not JCS/CBOR)
**Decision**: `json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)` over NFC-normalized string values, matching and extending the pattern `app/audit/services.py::_canonical` already established in Phase 5.
**Why**: Simple, dependency-free, auditable by inspection, and consistent with the one canonicalization scheme already trusted in this codebase (the audit hash chain). A bespoke binary format (CBOR) or a formal JCS library would add a dependency for marginal benefit at this project's current scale and threat model.

## ADR-6.5: Assertions are signed JSON envelopes, not JWT/PASETO
**Decision**: A hand-rolled `{payload: {...}, signing_key_id, algorithm, signature}` envelope, canonical-serialized before signing, over adopting a JWT or PASETO library.
**Why**: JWT's algorithm-confusion history and PASETO's smaller ecosystem both argued against pulling in a new dependency for something this codebase can implement, test, and fully understand in ~100 lines using the same canonicalization already trusted elsewhere. The spec explicitly permits "a carefully designed signed JSON envelope" as an alternative to PASETO. No unauthenticated custom encryption is used anywhere -- confidentiality is not required for assertion metadata (Principle 6), only authenticity and integrity, which Ed25519 signing over canonical bytes provides directly.

## ADR-6.6: Key storage -- filesystem, outside the repo, never in application tables
**Decision**: Server signing private keys live as PEM files under `OWNER_SIGNING_KEY_DIRECTORY` (an external path, default `owner/var/signing-keys/` for local dev, gitignored), never in a database column. Only public keys, key IDs, algorithm, and lifecycle timestamps are stored in `owner_signing_keys`.
**Why**: Matches Part I's explicit instruction and Phase 5's own precedent (`OWNER_BACKUP_DIR` is filesystem-based, gitignored, outside the repo's committed tree).
