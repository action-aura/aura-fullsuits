# Phase 9R — M7: Rate Limiting Audit (Real Findings, No Code Redesign Needed)

## Audited, not rebuilt — both existing mechanisms are already shared-store, not process-local

### Licensing API (`app/licensing_service/ratelimit.py`)

Already PostgreSQL-backed (`RateLimitCounter` table), by explicit design
decision (ADR-6.3, Phase 6) specifically "since all workers share one
database — satisfying 'distributed' without a Redis dependency." Policies
already cover activation (10/60s), invalid-license attempts (5/300s,
tighter — serial guessing), invalid-signature attempts (5/300s), check-in
(30/60s), signing-key discovery (60/60s), and service-info (60/60s). No
change needed — already satisfies M7's "must work across multiple workers"
requirement by construction.

### Login / MFA verification / recovery-code attempts (`app/security/ratelimit.py`)

**Real finding:** already backed by the persistent `owner_login_attempts`
PostgreSQL table (`is_locked_out()`/`record_attempt()`), queried identically
by every worker — but the module's own docstring, and Phase 5's own threat
model (`owner-threat-model.md` #14), both described this as a
"single-process" limitation "would need Redis at real scale." **That
description is stale**, not current. Fixed: the docstring now states the
real behavior; the Phase 5 threat-model entry is annotated as superseded
(historical record kept, not rewritten) rather than left actively
misleading a future reader into thinking this needs a Redis migration
before going remote.

**Proven, not just asserted:** `owner/tests/test_phase9r_rate_limit_multi_worker.py`
opens a second, fully independent SQLAlchemy engine/session against the
same database (standing in for a second Gunicorn worker process — not
merely a second call on the same connection) and confirms a lockout
recorded by one "worker" is immediately visible to, and enforced by,
completely independent database connections. 2/2 passing.

MFA verification and recovery-code attempts reuse this exact same
`is_locked_out`/`record_attempt` pair (`app/auth/routes.py::mfa_verify_submit`),
so they inherit the same real cross-worker safety with no separate
mechanism needed.

## Other M7-named surfaces

| Surface | Status |
|---|---|
| Password reset | **Not applicable** — no self-service password-reset feature exists in this codebase (staff accounts are admin-invited/managed); nothing to rate-limit |
| Invitation acceptance | No explicit rate limit on `accept-invitation/<token>`, but the token is `secrets.token_urlsafe(32)` (256 bits), one-time-use enforced at the DB row level, short expiry (Phase 5 threat #13) — brute-forcing a 256-bit token is infeasible regardless of request rate; not a gap worth a new mechanism |
| Product-download authorization | Doesn't exist yet — M11 (private distribution) hasn't been built. Will need its own rate limit when it is; tracked there, not here |
| Support diagnostic requests | Covered by the existing `service_info` licensing policy (60/60s) for the one diagnostic-adjacent endpoint that exists |

## Disposition

**PASS for the repository-controlled portion.** Both rate-limiting
mechanisms already satisfy the "shared backend, not process-local"
requirement by construction, proven under real multi-connection
concurrency, not merely re-asserted. Real value added this milestone:
corrected two pieces of stale documentation that would have sent a future
reader chasing an unnecessary Redis migration, and identified the one
genuinely missing surface (download authorization) as a known, tracked gap
belonging to M11, not silently absent.
