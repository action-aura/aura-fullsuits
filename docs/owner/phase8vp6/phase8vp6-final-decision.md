# Phase 8V-P6 — Final Decision

## Overall verdict: **CONDITIONAL PASS continues — final unconditional tag WITHHELD**

`aura-owner-commercial-ops-phase8-conditional-complete` remains the accurate marker. Not creating
`aura-commercial-licensing-operations-phase8-complete` this session.

## What this session genuinely achieved (the phase's actual mandate)

Both P1s named at the start of this session are now **fixed, tested, and physically proven**:

1. **`Subscription.status` / `license_status == SUSPENDED` never consumed locally**: fixed in
   `commercial_runtime/licensing_contracts/policy_evaluator.py::evaluate()` (additive, no contract
   version bump, reused already-signed, already-parsed fields). Physically proven on a rebuilt device:
   a real subscription EXPIRED transition (license status untouched) produced real, immediate,
   on-device `RESTRICTED`, with wire evidence proving the technical `WARN_ONLY` policy present in that
   exact assertion could not have caused it.
2. **`EmergencyExtension` business records not wired into technical grace**: fixed via
   `owner/app/licensing_service/offline_policy.py::serialize_policy_for_subscription()` (per-request
   override, never mutates the shared, possibly-multi-license `OfflinePolicy` row). Physically proven:
   the same device, same still-EXPIRED subscription, moved `Restricted -> Active` the instant a real
   extension was created, a real patient record was successfully saved during that window, and the
   device automatically reverted to `Restricted` the instant the extension's real signed window
   elapsed -- no manual action, no clock manipulation.

17 new automated tests added (11 `policy_evaluator`, 3 `assertion_verifier`, 3 Owner-level HTTP
integration tests); full regression re-run clean (960/960 across Owner/commercial_runtime/
licensing_contracts/Retail/Clinic, explicitly including product backends).

## Per-dimension verdict (governing spec Part AB's 25 dimensions)

| # | Dimension | Verdict |
|---|---|---|
| 1 | Commercial-state resolver | PASS (already existed, correctly reused, not duplicated) |
| 2 | Subscription status enforcement | **PASS** (fixed and physically proven this session) |
| 3 | Commercial/offline policy separation | PASS (new logic runs independently of `hard_expiry_behavior`, proven via a deliberately non-restrictive `WARN_ONLY` policy during the physical test) |
| 4 | Emergency-extension functional wiring | **PASS** (fixed and physically proven this session) |
| 5 | Emergency-extension expiry | **PASS** (physically proven, real elapsed time, no clock manipulation) |
| 6 | Timezone correctness | PASS (audited; zero naive-datetime introductions; real extension timestamps confirmed correctly aligned) |
| 7 | Assertion contract | PASS (no version bump needed; additive parsing only; documented) |
| 8 | Owner check-in | PASS (now consults the full commercial picture; real HTTP integration tests) |
| 9 | Scenario 1 (retained) | PASS (retained, no source change affecting it) |
| 10 | Scenario 2 | **NOT VERIFIED** (Retail not rebuilt/re-tested this session) |
| 11 | Scenario 3 | **PASS** (real, physical, unconfounded -- see `scenario3-commercial-enforcement-final.md`) |
| 12 | Scenario 4 (retained) | PASS (retained, no source change affecting it) |
| 13 | Scenario 5 | **PASS** (real, physical, unconfounded, including expiry -- see `scenario5-emergency-extension-functional-final.md`) |
| 14 | Scenario 6 | **NOT VERIFIED** (not attempted this session) |
| 15 | Scenario 7 | **NOT VERIFIED** (not attempted this session) |
| 16 | Stale assertion | **NOT VERIFIED** (not attempted this session) |
| 17 | Backup/restore/export | **NOT VERIFIED** (entry point located; not exercised) |
| 18 | Retail financial/returns | **NOT VERIFIED** |
| 19 | Clinic invoice/payment | **NOT VERIFIED** |
| 20 | Local deactivation recovery | P2, disclosed, unfixed (reconfirmed, unchanged from Phase 8V-P5) |
| 21 | Traffic privacy | PARTIAL (clean for what was captured; limited scope) |
| 22 | Logcat privacy | PARTIAL (real spot-check clean; not full per-scenario protocol) |
| 23 | Data preservation | PARTIAL (real positive evidence; no full baseline comparison) |
| 24 | Automated regression | **PASS** (960/960, product backends included) |
| 25 | Phase 8 overall | **CONDITIONAL PASS continues; tag withheld** |

## Why the tag is still withheld, stated plainly

Scenarios 2 (Retail), 6, 7, stale-assertion rejection, backup/restore/export, and Clinic invoice/payment
integrity remain unverified this session -- explicitly named blockers under this session's own
governing spec, same as every prior session's honest pattern. What changed this session is qualitative,
not just quantitative: the two real architectural gaps this whole multi-session effort kept
running into (why RESTRICTED was never reachable through normal commercial means, and why emergency
extensions never actually did anything) are now root-caused, fixed, tested, and physically demonstrated
with unconfounded evidence -- the strongest single-session result in this effort's history, even though
the tag remains withheld pending the remaining scenario coverage.

## Recommendation for the next session

1. Rebuild Retail Android (same proven command pattern) and repeat the Scenario 3-style proof on it
   directly (fast, ~10 minutes given the pattern is now established), then complete Scenario 2's full
   late-renewal/88.00/returns sequence.
2. Start the real Windows Retail install to unlock Scenarios 6 and 7.
3. Backup/restore/export and Clinic invoice/payment -- both UI-only, comparatively fast, entry points
   already located this session.
4. Extend the wire-capture middleware with a hold/release capability for the stale-assertion test.
