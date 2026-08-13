# Phase 9 Milestone 18 — Capacity and Resilience Results

All real, run against a real waitress-served (`--threads=8`) `StagingConfig` Owner process bound to
`127.0.0.1:5570`, against the real `aura_owner_staging` database.

## Liveness under concurrency — PASS, trivial cost

10 concurrent `GET /health/live`: all `200`, 2-3ms each. No DB dependency, no contention. Safe to poll
frequently.

## Readiness under concurrency — real finding: DB connection-pool contention

30 concurrent `GET /health/ready`: all correctly `503` (expected — `trust_anchor_matches_active_key`
genuinely fails for this brand-new never-built-against staging key; not a capacity artifact). Latency:
**p95 4406ms**, vs. a **260-290ms single-request baseline** — roughly 15-17x slower under this
concurrency level.

**Root cause (real, identified, not guessed)**: `owner/app/config.py`'s `SQLALCHEMY_ENGINE_OPTIONS`
sets only `pool_pre_ping: True` — no explicit `pool_size`/`max_overflow`, so SQLAlchemy's defaults
apply (pool_size=5, max_overflow=10, 15 total connections). `/health/ready` runs the *full* real
preflight check bundle (signing key, trust anchor, permission seed, role sync, pepper checks — each a
real DB query), so 30 concurrent readiness requests genuinely queue for a 15-connection pool.

**Assessment**: not a P0/P1 for this phase's realistic pilot target (`capacity-test-plan.md`'s own "1
scraper, 15-30s interval" target is nowhere near 30 concurrent probes) — but a real, documented
capacity boundary. **Recommendation for the real deployment**: set an explicit `pool_size`/
`max_overflow` in `StagingConfig` sized to the real expected monitoring concurrency once a real
Prometheus setup exists (Milestone 8), and/or consider caching the (rarely-changing) trust-anchor/
permission-seed checks for a short TTL rather than re-querying on every single readiness probe. Not
changed this session — would be premature tuning against a load pattern that doesn't exist yet
(no real monitoring stack deployed, per Milestone 8's own NOT VERIFIED status).

## Login rate limiting — PASS, exact real threshold

Real sequential login attempts against `phase9-drill-admin@example.com` with wrong passwords:
attempts 1-4 -> `401 "Invalid email or password."`; attempt 5 onward -> `401 "Too many attempts --
try again later."` — exactly matches `LOGIN_MAX_ATTEMPTS=5`, confirmed for real, not assumed from
reading the config value alone.

## Real methodology note: `Secure` session cookie and plain HTTP

`StagingConfig.SESSION_COOKIE_SECURE=True` (correct, required hardening) means the session cookie
never round-trips over a plain-HTTP connection in a spec-compliant client (Python `requests` correctly
refused to resend it; `curl`'s cookie jar is more lenient and does resend it, which is why the
sequential lockout test above used `curl`, not `requests`). This is not a bug — it is the real,
concrete reason a genuinely working staging deployment needs real TLS (Milestone 12) to be
functionally usable at all for anything cookie-authenticated, not merely "more secure to have it."
Recorded here as real operational insight this capacity test surfaced.

## Scheduler under concurrent request load — PASS

Real Milestone 9 scheduler bundle (`expiry-scan`/`reconcile`/`device-limit-scan`/`verify_chain`) run
while 10 concurrent `GET /health/live` requests were in flight: scheduler exited `0` (`status: OK`),
all 10 concurrent requests returned `200`. No interference observed.

## Graceful restart — PASS

Process killed, restarted: **2 seconds to `/health/live` responding again**, real measured value, not
estimated.

## PostgreSQL restart, reverse-proxy restart, disk-space simulation

NOT VERIFIED this session — see `capacity-test-plan.md`'s explicit safety rationale (shared dev
Postgres instance, no reverse proxy running without Docker).

## Overall assessment

Real capacity behavior at this pilot's realistic scale (Milestone 18's own targets) is good:
sub-5ms liveness, correct rate-limiting, fast restart recovery, no scheduler/request interference. The
one real finding (readiness-under-heavy-concurrency latency) is honestly outside the realistic target
load and does not block Phase 9's decision, but is documented as a real, specific, actionable
follow-up rather than glossed over.
