# Phase 8V-P — Final Phase 8 Decision (Part W)

## Per-dimension verdicts

| # | Dimension | Verdict |
|---|---|---|
| 1 | Owner commercial-operations UI | **PASS** (Phase 8V, reconfirmed unaffected) |
| 2 | Renewal lifecycle | **PASS** (real, wire-level, both products' installed exes) |
| 3 | Payment governance | **PASS** (Phase 8V/M1, unchanged) |
| 4 | Pilot lifecycle | **PASS** (Owner-side real; post-conversion wire re-check incomplete, see risk register) |
| 5 | Emergency extensions | **PASS** (real, Owner HTTP/MFA) |
| 6 | Manual activation review | **PASS** (Phase 8V, unchanged; not re-exercised live this session, no scenario required it) |
| 7 | Device-slot operations | **PASS** (real, wire-level, found+fixed a real defect) |
| 8 | Clinic Windows commercial lifecycle | **PASS** (real, this session) |
| 9 | Clinic Android commercial lifecycle | **NOT VERIFIED** (no device) |
| 10 | Retail Windows commercial lifecycle | **PASS** (real, this session) |
| 11 | Retail Android commercial lifecycle | **NOT VERIFIED** (no device) |
| 12 | Product-to-Owner traffic privacy | **PASS** (real captured traffic, this session, clean) |
| 13 | Clinic overall | **CONDITIONAL PASS** (Windows real and complete; Android not verified) |
| 14 | Retail overall | **CONDITIONAL PASS** (Windows real and complete; Android not verified) |
| 15 | Phase 8 overall | **CONDITIONAL PASS** — unchanged qualifier, narrower gap |

## Why this is not issued as unconditional PASS

Per this phase's own explicit rule: "Do not issue PASS because the wire-level harness passed alone,"
and Part Y's own tag-blocking conditions — "no physical Android device is available,"
"Clinic Android evidence is incomplete," "Retail Android evidence is incomplete" — are all
unambiguously true this session, disclosed before any work began (see
`physical-device-readiness.md`), not discovered as a late excuse. The final
`aura-commercial-licensing-operations-phase8-complete` tag is **not created**.

## What genuinely changed since Phase 8V's own CONDITIONAL PASS

Phase 8V's gap was: "physical Android/Windows validation of all 7 scenarios... NOT VERIFIED." That
gap is now **narrower and more precise**:

- **Windows**: no longer a gap at all for 6 of 7 scenarios — real installed products, real Owner
  server, real cryptography, real found-and-fixed defects. Scenario 7 (plan downgrade) is
  conditional on a disclosed feature gap, not a validation gap.
- **Android**: still fully open, for the same reason (no device), now with everything else already
  proven at the Windows tier so a future device session's job is narrowly scoped: run the same 7
  scenarios (already-proven Owner-side and Windows-side mechanics) on a physical phone, build the
  Android artifacts, capture logcat.

## Two real defects this session found that a purely-simulated validation phase (Phase 8V) could
not have

1. `DEVICE_ALREADY_REGISTERED` (a genuine unhandled-500 crash bug) — found only because a real
   installed product's *persistent* device identity was reused against a second real license, a
   behavior no synthetic per-test-run device key ever exercised.
2. The stale trust-anchor/signing-key and stale permission-seed environment gaps — found only
   because this session used the actual persistent `aura_owner_dev` database and actual bundled
   `trust_anchor.json`, not a fresh-every-test-run isolated database and in-memory trust store.

Both are exactly the class of finding a physical-validation phase exists to surface, and both are
now fixed and regression-tested.

## Recommended next session's exact scope

Connect a physical Android device, build rc.3 APK/AAB for both products, install, run the 7
scenarios physically (all already proven at the service/Windows tier — the device session's job is
narrowly to confirm the identical behavior on real hardware), capture logcat, then write the closing
decision that finally drops "conditional." Nothing else.
