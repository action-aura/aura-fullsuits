# Phase 8V-P3 — Session Execution Plan

Written before knowing whether a device would be available, per this phase's own Part A instruction.
Actual outcome: no device connected (`device-readiness.md`), so only the "if no device" branch below
was executed.

## If a device connects

1. Record device identity, run the 5-minute ADB stability loop.
2. Rebuild both Android products with `-PownerLicensingBaseUrl=http://127.0.0.1:5551/api/licensing/v1`
   after `adb reverse tcp:5551 tcp:5551` (see `artifact-verification.md` -- discovered this session
   that the existing rc.3 artifacts have an empty, NOT_CONFIGURED licensing URL baked in).
3. Start Owner in the real dev configuration, confirm `flask commercial preflight` is `ok: true`
   (already reconfirmed this session regardless -- `owner-validation-environment.md`).
4. Install/upgrade both products, create synthetic local data (Part F's minimums).
5. Set up traffic capture and Logcat clearing per `traffic-capture-plan.md` / `logcat-review-plan.md`.
6. Run all seven scenarios in the order the governing brief lists them (Parts H-N), each with its own
   evidence document.
7. Run persistence, backend-enforcement, traffic-privacy, Logcat-privacy, and data-preservation
   checks (Parts O-S).
8. Final regression, final decision, and -- only if every mandatory gate genuinely passes -- the
   final tag.

## If no device connects (what actually happened)

1. Complete every device-independent check: baseline, preflight, artifact verification.
2. Write `physical-validation-matrix.md` recording every scenario as NOT VERIFIED, with the specific
   reason (no device), not silently omitted or assumed.
3. Do not create any of the scenario-evidence files (Parts H-N), the persistence/enforcement/traffic/
   Logcat/data-preservation evidence files (Parts O-S), or the signed-installation-upgrade file (Part
   E) -- all require actual device execution, and creating them empty or templated would misrepresent
   untested work as evidence (same principle Phase 8V-P2 already established).
4. Reconfirm the automated regression suites at current HEAD for real (`final-regression.md`).
5. Write the final decision documenting the single remaining gate, update the Phase 8V-P2 decision
   docs additively, and stop -- no Phase 9 work.
