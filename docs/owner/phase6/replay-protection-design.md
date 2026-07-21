# Phase 6 -- Replay Protection Design (Part L)

## Mechanism (PostgreSQL, ADR-6.3 -- not Redis)
`owner_security_nonce_records` with a `UNIQUE(nonce, scope)` constraint. `consume_nonce()` does a plain `INSERT`; a duplicate `(nonce, scope)` pair violates the constraint, caught as `IntegrityError` and translated to `ReplayError("NONCE_REUSED")`. This is the atomic check -- there is no separate SELECT-then-INSERT window for a race to slip through, because the database's own unique-constraint enforcement *is* the check.

## Scoping
Nonces are scoped per operation type (`"activation"`, `"check_in"`, `"deactivation"`) -- the same nonce string reused across two different scopes does not collide, since the uniqueness key is the `(nonce, scope)` pair, not the nonce alone (verified: `test_phase6_security_controls.py::test_same_nonce_different_scope_is_allowed`).

## Timestamp freshness
`validate_timestamp()` rejects any request whose `timestamp` is more than `OWNER_ACTIVATION_TIMESTAMP_SKEW_SECONDS` (default 300s) away from the server's current time, in *either* direction -- too old **or** too far in the future both produce `TIMESTAMP_OUTSIDE_ALLOWED_WINDOW`. A naive (timezone-less) timestamp is rejected outright as `INVALID_TIMESTAMP` before the skew check even runs.

## Configuration (Part L's explicit list)
- **Allowed timestamp skew**: `OWNER_ACTIVATION_TIMESTAMP_SKEW_SECONDS`, default 300s.
- **Nonce format**: opaque string.
- **Nonce length**: 16-64 characters (`NONCE_MIN_LENGTH`/`NONCE_MAX_LENGTH`).
- **Nonce TTL**: `OWNER_NONCE_TTL_SECONDS`, default 600s (`expires_at` column; `purge_expired_nonces()` available for periodic cleanup, not yet wired to a scheduled job -- a documented, acceptable gap for this phase's scale).
- **Duplicate nonce behavior**: hard rejection, `NONCE_REUSED`, no partial processing.
- **Clock-skew reason codes**: `INVALID_TIMESTAMP` (malformed/naive) vs. `TIMESTAMP_OUTSIDE_ALLOWED_WINDOW` (well-formed but outside the allowed skew) -- kept distinct since they indicate different client-side problems (a client whose clock is merely a few minutes off gets a different, more actionable signal than one sending garbage).
- **Testing clock abstraction**: `validate_timestamp(..., now=...)` accepts an explicit `now` parameter specifically so tests can simulate skew deterministically without sleeping or mocking global time (`test_phase6_security_controls.py`).

## Fail-closed behavior
`OWNER_REPLAY_PROTECTION_REQUIRED=true` (the default, and hard-enforced in production-like mode by `config.py::validate_external_api_production`) means `is_service_ready()` checks real Postgres connectivity (`check_replay_protection_available()`, a live `SELECT 1 FROM owner_security_nonce_records`) before any request is processed -- if the replay store is unreachable, every request is rejected `503 SERVICE_TEMPORARILY_UNAVAILABLE` rather than silently skipping the nonce check. There is no development-only bypass flag that could accidentally ship enabled -- the "development fallback" the spec anticipates simply doesn't exist in this implementation, because Postgres (unlike an optional Redis) is *already* a hard dependency for the whole app to run at all.

## Multi-worker / multi-process correctness
Because the nonce store is PostgreSQL rather than per-process memory, two Owner worker processes (e.g. behind gunicorn's multiple workers) checking the same nonce concurrently both hit the same database and the same unique constraint -- exactly one wins the INSERT, the other gets `IntegrityError`. Proven under real concurrency by `test_phase6_concurrency.py` (which exercises the adjacent device-limit row-lock, the same underlying "Postgres as the single source of truth across processes" property).
