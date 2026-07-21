# Phase 6 -- Implementation Plan

## Build order (matches the git commit sequence, Part AC)
1. Discovery docs (this set) -- done first, no code.
2. Schema + migration: 11 new tables (signing keys, device public keys, activation requests/decisions, signed assertions, idempotency records, offline policies + assignments, entitlement snapshots, nonce records, key-rotation events, service-health events), verified against a populated Phase 5 database.
3. Server signing-key management (Ed25519 generation/activation/rotation/export/health, filesystem-backed, CLI).
4. Device identity (registration, proof-of-possession verification, revocation/replacement).
5. Canonical serialization + reason-code catalog (shared primitives everything else depends on).
6. Replay protection (Postgres nonce store) + distributed rate limiting (Postgres counters).
7. Persistent idempotency.
8. Initial activation protocol (the 23-step sequence, transactional, device-limit-safe).
9. Signed assertion issuance + verification.
10. Authenticated check-in.
11. Entitlement resolution engine.
12. Offline policy authority.
13. Suspension/reactivation/revocation/replacement service behavior (extends Phase 5's existing `licensing/services.py` transitions, doesn't replace them).
14. External API blueprint wiring (`/api/licensing/v1/*`), gated exactly like Phase 5's pattern.
15. Owner UI admin extensions (Part R) + RBAC permission extensions (Part S).
16. Audit extensions (Part T) -- new action codes, new forbidden markers.
17. Product-side activation simulator (Part U) -- built once the protocol is stable enough to drive against.
18. Security hardening pass + dependency scan (Part W/X).
19. Full test suite (Part Y) -- written alongside each part above, not deferred to the end; concurrency and crypto-specific suites finalized last.
20. Manual end-to-end verification with evidence report (Part Z).
21. Remaining documentation (Part AA) + env config (Part AB).
22. Tag `aura-owner-licensing-activation-phase6-complete` (the truncated spec message's tag name inferred from its own naming convention -- `aura-owner-<domain>-phase<N>-complete`, matching `aura-owner-foundation-phase5-complete` exactly; confirmed as the literal intended name before tagging, see `phase6-implementation-plan.md`'s own tag-verification step in Part AC execution).

## Explicit non-goals (repeating the spec's own boundary for this document's own clarity)
No Retail/Clinic source changes. No public exposure. No Phase 7 (subscription-expiry enforcement, payment gateway, e-invoicing, Aura Core integration) begun.
