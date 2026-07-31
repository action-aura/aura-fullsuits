# Phase 8V-P6 — Physical Closure Plan (as executed)

1. Hard entry gate: real device confirmed, stable.
2. Baseline: git clean, HEAD `cf13d6e`, conditional tag unchanged.
3. Root-cause analysis (source-only, no device needed): found the exact unwired fields
   (`subscription_status`, `license_status == SUSPENDED`, `commercial_grace_end`) and the exact
   emergency-extension gap (stored `OfflinePolicy` row vs. real `EmergencyExtension` record never
   connected).
4. Implemented the fix, entirely additive, no contract version bump, no new decision engine.
5. Full automated regression after the fix, before touching the device (Owner 398/398,
   commercial_runtime+licensing_contracts 226/226, Retail 194/194, Clinic 135/135 -- all green, proving
   the fix doesn't regress anything already proven).
6. Build-impact decision: Android rebuild required (commercial_runtime is staged into the APK at build
   time). Rebuilt and reinstalled Clinic (in-place upgrade, same package ID/signing key/identity,
   confirmed via `dumpsys package` and `aapt dump badging`).
7. Restarted Owner with the real wire-capture middleware; real preflight `ok: true`; `adb reverse`
   recreated and verified.
8. Physical Scenario 3 (subscription-driven commercial restriction): assigned a genuinely
   non-restrictive `WARN_ONLY` technical policy first (to remove any possible confound), transitioned
   the real subscription to `EXPIRED` while leaving `License.status` untouched, triggered a real
   check-in, confirmed real physical `RESTRICTED`, confirmed via real captured wire evidence that the
   technical policy in that exact assertion could not have caused it, confirmed real backend denial of
   a new patient with no row created.
9. Physical Scenario 5 (emergency extension functional wiring): created a real, short, MFA-service-level
   extension for the same still-EXPIRED subscription, confirmed the device moved
   Restricted -> Active with wire evidence showing the subscription was still EXPIRED (isolating the
   extension as the sole cause), confirmed a real patient could now be saved, waited for the extension's
   real signed expiry, confirmed automatic reversion to Restricted with no manual action.
10. Wrote up both scenarios with full real evidence.
11. Remaining scope (Scenario 2 Retail physical rebuild+test, Scenario 6, Scenario 7, stale-assertion,
    backup/restore/export, Clinic invoice/payment) not reached within this session's time budget --
    honestly disclosed in their own docs rather than assumed complete, matching every prior session's
    pattern.
