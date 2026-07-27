# Phase 8V — Validation Scenario Plan

Adapts the governing brief's Parts Q-W (7 scenarios) to what is genuinely achievable in this
environment (see `phase8v-scope-and-baseline.md`'s environment-constraint section). Each scenario
runs as a real, Postgres-backed, HTTP-level integration test hitting the actual Owner Flask app and
the actual `commercial_runtime.licensing_contracts` client/activation code -- the same code path a
real Windows desktop product uses, and (for everything except the physical device itself) the same
code path Android's embedded backend uses.

| # | Scenario | Verified for real (this environment) | Explicitly NOT verified (needs physical Android) |
|---|---|---|---|
| 1 | Early renewal | Owner UI create->approve->apply; real check-in via `commercial_runtime` client against real Owner HTTP; no license-key re-transmission (structurally impossible -- check-in payload has no key field); same `installation_id`/device key; fresh signed assertion; assertion state reflects new term | Physical on-device screenshot/logcat |
| 2 | Late renewal (after expiry) | Same pipeline starting from `EXPIRED` subscription (`_REVIVABLE_SUBSCRIPTION_STATUSES`); RESTRICTED-equivalent local state before renewal, ACTIVE_ONLINE after; read access preserved throughout (no code path in this repo ever deletes/hides data on any commercial transition) | Physical on-device |
| 3 | Past-due -> restricted | `expiry_scan.py` transitions + real `commercial_runtime` state-machine evaluation (already Phase 7V-A-proven code, re-exercised here with Phase 8 fields present) | Physical on-device |
| 4 | Pilot conversion | Owner UI-guided convert (create+approve+apply renewal, then `mark_pilot_converted()`), real assertion carries `pilot_status: "CONVERTED"` | Physical on-device |
| 5 | Emergency extension | Owner UI create (MFA-gated) -> assertion carries `emergency_extension_id` -> simulated time-window expiry (via `as_of`/`now` overrides, the same pattern every M3/M5/M6 test already uses) -> extension no longer active | Physical on-device |
| 6 | Device replacement | `replace_device_slot()` + a real second activation HTTP call proving slot re-use and no double-consumption | Physical on-device |
| 7 | Plan downgrade / overage | `scan_over_limit_licenses()` + Owner UI remediation surfacing, real HTTP activation attempt rejected once over limit | Physical on-device |

## What "real traffic capture" means here

No physical device traffic to capture. The structural equivalent already used in Milestone 7/8's
data-boundary work is extended: every HTTP request/response body exchanged during these scenario
tests is captured verbatim (via the Flask test client's real request/response objects — not mocked)
and asserted against the exact allowlist in the governing brief's Part Y. This is real captured
traffic from a real running Flask app and a real `commercial_runtime` client against it -- it is
Owner-Windows traffic, not Owner-Android traffic. See `real-traffic-evidence.md`.
