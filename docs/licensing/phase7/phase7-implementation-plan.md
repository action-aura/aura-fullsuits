# Phase 7 -- Implementation Plan

## Baseline test results (Part A, run before any change)

```
OWNER_TEST_DATABASE_URL=...aura_owner_test .venv/Scripts/python.exe -m pytest owner/tests/ -q
  -> 179 passed in 239.62s

.venv/Scripts/python.exe products/run_all_tests.py
  -> 20 file(s) run, 20 passed, 0 failed
     (Retail: 176 tests across 10 files; Clinic: 116 across 9 files;
      commercial_runtime/tests/migration_safety_test.py: 5)
```

Android unit/lint/build baselines (spec item 5 of Part A's pre-modification checklist) are deferred to the start of Part F/AA, since they require invoking Gradle with the Android SDK/Chaquopy toolchain -- not yet exercised in this session. Recorded here as an explicit gap, not silently skipped.

## Two open policy decisions gating implementation (must be resolved before Part Q/R code, not during)

1. **Clinic**: whether `/visits` POST, `/visits/<id>` PATCH, `/visits/<id>/notes`, `/appointments/<id>` PATCH/checkin, and `/prescriptions` POST represent "new work" (blocked when restricted) or "completing already-open encounters" (arguably must remain possible) -- requires reading the actual handler bodies in `clinic_api.py`, not yet done. See `clinic-restriction-capability-matrix.md`.
2. **Retail**: whether `/returns` POST and `/customers/<id>/payments` POST are unrestricted-allowed (refund obligation preservation) or routed through a supervised override -- requires reading the actual handler bodies in `retail_api.py`. See `retail-restriction-capability-matrix.md`.

## Sequencing (Clinic before Retail throughout, per Part Q/AB's explicit instruction)

1. **Part B** -- version bump to rc.2 across all four products' canonical version sources; do not overwrite rc.1 artifacts (copy `dist/` outputs aside first).
2. **Part C** -- shared licensing domain: Python package in `commercial_runtime/licensing_contracts/` (currently empty, confirmed in Part A) built and unit-tested standalone, against shared JSON conformance fixtures, before any product wiring. Kotlin reference implementation built once (Clinic Android), Retail Android's Kotlin package constructed in parallel against the same fixtures afterward.
3. **Part D** -- trust bootstrap: the small Owner-side signed-manifest extension first (own commit, own tests, extends but does not modify Phase 6's existing `/signing-keys` behavior's shape -- additive field only), then the bundled `trust_anchor.json` generation script, then client-side trust-store code.
4. **Parts E/F** -- device identity, Windows then Android (Windows has no toolchain-availability risk; validating the pattern there first de-risks the Android `cryptography`/Tink decision named in the threat model before committing to it on the harder platform).
5. **Parts G/H/I/J/K/L/M/N/O** -- built together as one vertical slice per product, in order Clinic-Windows -> Clinic-Android -> Retail-Windows -> Retail-Android, each slice ending in a real activation+check-in+offline-simulation test against the actual running Phase 6 Owner service (not mocked), matching Part AB/AC's end-to-end sequence.
6. **Parts P/Q/T** -- Clinic capability guard, resolving open decision #1 first.
7. **Parts R/T** -- Retail capability guard, only after Clinic's passes fully, resolving open decision #2 first.
8. **Part S** -- entitlement gates, layered on top of the now-working assertion/state-machine plumbing; infrastructure only, no WhatsApp/SMS/reports/dashboard feature built behind it (explicit non-goal, matches Part S's own text).
9. **Parts U/V** -- authority-boundary code (the localhost sync endpoint, the DPAPI/Keystore modules) -- largely produced as a byproduct of E/F/J/K above; this step is the explicit boundary-test pass (direct-bypass attempts, Kotlin-boolean-spoofing attempt) rather than new construction.
10. **Part W** -- local event recorder, threaded through every state transition already built in steps 5-9 (added incrementally alongside them in practice, called out as its own step here for tracking/testing purposes).
11. **Part X** -- deactivation/replacement flow, per product.
12. **Part Y** -- rc.1 -> rc.2 migration safety, with real synthetic pre-rc.2 data, pre-migration backup, rollback test.
13. **Parts Z/AA** -- release rebuilds and the full validation checklists, Clinic first.
14. **Parts AB/AC** -- the full end-to-end scripted sequences, Clinic then Retail, Windows then Android, against the real Phase 6 Owner service.
15. **Part AD** -- outage/failure-injection testing (Owner stopped, DB down, signing key pulled, malformed responses, etc.) -- reuses the real Owner service, deliberately broken in controlled ways, not simulated.
16. **Part AE** -- security hardening verification pass (the full checklist), including a `pip-audit`-equivalent dependency scan for whatever new Windows/Android dependencies this phase adds (`cryptography` if not already covariant with Owner's version, the chosen Android Ed25519 library).
17. **Part AF+ (spec truncated here)** -- full combined test-suite run (Owner + Retail + Clinic + new licensing-contract tests + Android instrumented tests), physical-device validation on the Infinix X6528 where available (marked NOT VERIFIED and not fabricated if unavailable at the time), and the final tag + structured response. The exact tail of Part AF and everything after it in the original spec was not received (see `phase7-scope-and-baseline.md`) -- the shape of the closing deliverable is inferred from the identical Phase 5/6 pattern (46-item structured final response) and will be delivered in that form, with any inference called out explicitly rather than silently presented as spec text.

## Pacing note

This is a genuinely multi-session build spanning Windows cryptography/DPAPI, Android Kotlin/Keystore/Chaquopy, four separate product codebases, and real signed-artifact rebuilds -- it is not being collapsed into a single implementation pass. Each numbered step above is closed out with its own real test evidence (unit tests where the step produces testable logic, a real running-Owner-service exchange where the step produces a network interaction, a real Gradle/PyInstaller build where the step produces a release artifact) before the next step starts, matching the verification discipline already established in Phases 5 and 6. Progress against this list is tracked in the session's todo list; this document is updated as steps complete or as open decisions get resolved, not left static.
