# Phase 9 Milestone 18 — Capacity Test Plan

## Realistic small-pilot targets (not internet-scale)

- Staff users: up to 10.
- Pilot customers: 1 at a time (this phase's own scope decision).
- Active installations: up to 3 (1 primary + up to 2 additional, matching real `device_limit`
  mechanics).
- Check-in cadence: daily per installation (matches `ASSERTION_TTL_SECONDS`/offline-policy defaults,
  Phase 8, unchanged).
- Peak activation attempts: single digits per day during onboarding, effectively zero afterward.
- Target p95 latency: liveness < 50ms, readiness < 1s under realistic (non-adversarial) monitoring
  scrape concurrency (1 scraper, 15-30s interval — not 30 concurrent probes).
- Acceptable error rate: 0% for real traffic; `503` on `/health/ready` is a correct, expected signal
  state, not an "error" to count against this target when a real dependency genuinely isn't ready.

## Tests planned (real, run against a real local waitress-served staging Owner instance — see
`capacity-and-resilience-results.md` for actual results)

1. Concurrent readiness checks (burst, 30 concurrent).
2. Concurrent liveness checks (burst, 10 concurrent).
3. Real login-lockout threshold sequence (`LOGIN_MAX_ATTEMPTS=5`).
4. Reconciliation-scan-under-concurrent-request-load (real Milestone 9 scheduler run while 10
   concurrent liveness requests are in flight).
5. Graceful process restart, timed recovery.
6. Backup during normal usage — implicitly covered by test 4 (the scheduler bundle includes no backup
   step by design — see `scheduled-operations.md` — the backup job is separate; not additionally
   burst-tested this session beyond its own real success already proven in Milestone 7).

## Explicitly NOT tested this session (real, honest gaps)

- PostgreSQL restart/recovery — this machine's Postgres 17 service is shared with the rest of this
  session's (and the developer's normal) work; restarting it would have been disruptive to unrelated
  state, not a safe isolated test this session. NOT VERIFIED.
- Reverse-proxy restart — no Caddy/reverse proxy running this session (no Docker Engine).
- Connection-pool exhaustion as a deliberately engineered scenario — not deliberately forced, but
  organically observed as a real side effect of test 1 (see results).
- Disk-space warning behavior — no safe way to simulate this against a shared dev machine's real disk.

## Safety

No destructive uncontrolled stress against shared infrastructure — every test ran against this
session's own dedicated `aura_owner_staging` database and a locally-bound (127.0.0.1-only) Owner
process, never touching anything outside this session's own real estate.
