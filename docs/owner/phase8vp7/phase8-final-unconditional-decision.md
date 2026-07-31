# Phase 8V-P7 — Final Phase 8 Decision

## Overall verdict: **CONDITIONAL PASS continues — final unconditional tag WITHHELD**

`aura-owner-commercial-ops-phase8-conditional-complete` remains the accurate marker. Not creating
`aura-commercial-licensing-operations-phase8-complete` this session.

## What this session genuinely achieved

1. **Corrected a real gap in Phase 8V-P6's own conclusion**: Windows artifacts (not just Android) embed
   `commercial_runtime` as a physical build-time copy and were confirmed, by direct file-timestamp
   evidence, to be running the stale, pre-fix evaluator. Both Windows executables rebuilt.
2. **All four artifact families aligned** to a new immutable version (`1.0.0-rc.4`/versionCode 5) per
   the project's own documented versioning policy, across all 8 canonical sources, with zero rc.3
   history overwritten.
3. **Android identity/signing continuity fully re-verified** on real, physical in-place upgrades (both
   products): certificate SHA-256 exact match to historical identity, installation ID/first-install-time
   preserved.
4. **Scenario 3 physically re-proven** on the rebuilt Clinic artifact -- the fix survives the rebuild
   cycle, confirmed, not assumed.
5. **Scenario 2's restriction half physically proven for Retail for the first time**, using the same
   unconfounded method (deliberately non-restrictive technical policy + real subscription EXPIRED
   transition) already proven for Clinic.
6. **Scenario 6 reached a real, full PASS** -- discovered mid-session that genuine multi-instance
   Windows product identities (same machine, distinct `AURA_APP_DATA` directories, each with its own
   real device keypair via the real activation contract) satisfy the spec's own definition of a
   legitimate client identity, correcting an earlier, overly pessimistic constraint disclosure. Real
   device-limit block, real Owner-side replacement, real idempotency proof.
7. **Scenario 7's Owner-side mechanics reached a real, full PASS**, including a genuine new finding
   about how `scan_over_limit_licenses()`'s date-precision evaluation interacts with short-duration
   temporary exceptions (a real, disclosed, non-blocking design characteristic, not a bug).
8. **A real mid-session bug was found and fixed by the operator, not silently worked around**: the
   first attempt to issue a real license key for the Scenario 6/7 test license used the wrong Flask
   config key name for the HMAC pepper, producing a key that failed real Owner signature verification --
   caught by inspecting the real captured wire evidence, corrected, and the correct key worked
   immediately.

## Per-dimension verdict

See `remaining-physical-gate-matrix.md` for the full 18-row table. Summary: artifact alignment,
versioning, signing continuity, Scenario 3 smoke, Scenario 6, Scenario 7 (Owner-side), and full
regression are **PASS**. Scenario 2, Retail 88.00/returns, stale-assertion, both products'
backup/restore/export, Clinic invoice/payment, and full data-preservation/Logcat comparison are
**PARTIAL or NOT VERIFIED**, entirely attributable to one real, disclosed cause: an extended physical
Android device disconnection partway through the session that multiple real recovery attempts did not
resolve within the remaining session time.

## Why the tag is still withheld, stated plainly

The governing spec's own explicit gate list requires Scenario 2 PASS (not partial), Clinic and Retail
backup/restore/export PASS, Clinic invoice/payment PASS, Retail 88.00/returns PASS, and physical
stale-assertion rejection -- none of which are met this session, for the single disclosed reason above,
not from any newly-discovered defect. Zero P0, zero P1 remain (unchanged from Phase 8V-P6); the one
carried P2 (local-deactivation UX) does not block per the spec's own instruction.

## Recommendation for the next session

Reconnect the device first, before any other work, and verify stability for several minutes before
proceeding (this session's own hard-entry-gate discipline, applied mid-session this time rather than
only at the start). Then, in priority order: (1) confirm Scenario 2's renewal restoration + run the
88.00/return cases (the Owner-side renewal is already real and applied -- only the device-facing half
remains); (2) backup/restore/export on both products (entry points already located); (3) Clinic
invoice/payment; (4) stale-assertion (needs the capture middleware extended with hold/release); (5) the
Android-facing confirmation halves of Scenario 7 (temporary-exception assertion metadata) using the
real license/exception mechanics this session already proved Owner-side.
