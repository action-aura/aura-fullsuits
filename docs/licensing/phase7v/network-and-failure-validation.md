# Phase 7V — Network and Failure Validation (Part O)

Consolidates Phase 7's existing failure-mode coverage with new Phase 7V live evidence gathered on
the genuinely rebuilt, trust-anchor-bundled Windows frozen executables.

## New live evidence this session (Windows, frozen exe, real processes)

- **Owner stopped mid-session / connection refused**: real Owner Flask process killed
  (`server_close()`, not a mock) while Clinic was `ACTIVE_ONLINE`. Check-in against the dead
  service → HTTP 200 (never surfaced as an error to the caller), `last_attempt_reached_owner:
  false`, state degraded to `ACTIVE_OFFLINE`. Local installation identity (`installation_id`)
  unchanged and readable. **A single failed attempt did not restrict anything.**
- **App restart during outage**: process killed and relaunched while Owner was down; status still
  reported the same `installation_id` and the correct (offline-consistent) state — no crash, no
  corrupted state file.
- **Deactivate attempted while Owner unreachable**: failed safely with `reason_code:
  NETWORK_UNAVAILABLE` and a redacted, non-leaking error detail — local state unchanged
  (`GRACE_PERIOD`/whatever it was before remained exactly as it was). Confirms deactivation is
  deliberately **not** offline-safe by design (an authoritative Owner-side action), distinct from
  check-in/activation's offline tolerance.
- **Extended offline (WARNING, GRACE_PERIOD)**: driven live via the real persisted state (backdating
  only `last_successful_checkin_at`, never the trusted-time anchor itself) — see
  `windows-rc1-to-rc2-installer-validation.md` for the full sequence and the finding that naively
  rewriting the trusted-time anchor field does *not* move the evaluated state, a positive
  clock-tamper-resistance property observed directly, not just claimed from source reading.

## Pre-existing coverage re-confirmed (unit level, all passing in the Part D 585-test combined run)

`test_client.py` (14 tests) and related suites already cover, and were re-run this session:
- Retry/backoff schedule (exponential, capped, real `requests.exceptions` mapping).
- `Retry-After` header honored on 429/503.
- Retries exhausted → clean `NetworkError`, no infinite loop.
- Request timeout → `NetworkError`, not a hang.
- TLS/SSL errors are never silently retried into an insecure fallback.
- Malformed JSON response → `MalformedResponseError`, old state retained (never overwritten by
  garbage).
- Tampered/forged assertion → rejected, old valid state retained (see
  `android-authority-boundary-physical-report.md` for the full assertion-forgery matrix, equally
  applicable to Windows since both platforms share `AssertionVerifier`).
- Long offline gap → `RESTRICTED` reachable (state-machine level; live reproduction this session
  reached `WARNING`/`GRACE_PERIOD` but not `RESTRICTED`, since the test license's plan uses
  `WARN_ONLY` hard-expiry behavior — see the installer-validation doc for the full explanation).

## Idempotency / repeated-nonce / request-accepted-but-response-lost

Covered by Owner-side tests re-run in `owner-regression-closure.md` (replay tests, rate-limit
tests, idempotency tests all passing at 186/186) — these are properties of Owner's request
handling, exercised identically regardless of which platform's client calls in.

## Database/state integrity under failure

Every outage/restart cycle performed this session was followed by a real `PRAGMA integrity_check`
on the affected SQLite databases (`clinic.db`, `registry.db`, `licensing.db`) — always `ok`, never
corrupted, across kills, restarts, and backdated-then-restarted state.

## Customer data transmission during failures

None of the outage/retry/backoff traffic captured during this session's live tests included any
patient, sales, or business data — the licensing protocol carries only license keys (once, at
activation), device fingerprints, and assertion envelopes, matching the Phase 7 design guarantee
re-confirmed in `release-artifact-security-inspection.md`.

## Not re-verified this session (already covered by Phase 7's own extensive Part AD work)

Android-specific network-failure behavior (Kotlin `OwnerClient.kt`'s retry/backoff, offline
handling) was not re-driven live this session (no physical device — see Part J); it was previously
verified at the unit level in Phase 7 and is structurally the same retry/backoff/error-mapping
pattern as the Windows client, now additionally cross-checked by this session's live Windows
outage proof.

## Verdict

Network/failure resilience: **PASS** (Windows, live and unit); **PASS at unit level only** (Android
— no physical device this session).
