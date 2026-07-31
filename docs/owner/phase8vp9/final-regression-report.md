# Phase 8V-P9 Part P — Final Complete Regression (Fresh, Final HEAD)

Per this phase's explicit instruction, the previous session's 960/329 totals are NOT reused. Every
number below was generated fresh, after this session's final source change, from the exact final
working tree.

## Results

| Suite | Result | Notes |
|---|---|---|
| Owner (`owner/tests`) | **405/405** | 398 baseline + 2 new (Part E device-slot precision) + 5 new (Part F pepper preflight) |
| `commercial_runtime`/`licensing_contracts` (full package, incl. `tests/`) | **235/235** | 233 baseline + 2 new (Part K stale-assertion) |
| Retail backend (`products/retail/tests`, per-file clean) | **194/194** | unchanged -- no Retail-specific source changed |
| Clinic backend (`products/clinic/tests`, per-file clean) | **135/135** | unchanged -- no Clinic-specific source changed |
| Retail Android unit tests (`testDebugUnitTest`, part of the rc.5 build) | **BUILD SUCCESSFUL, 0 failures** | ran as part of the real rc.5 rebuild |
| Clinic Android unit tests (`testDebugUnitTest`, part of the rc.5 build) | **BUILD SUCCESSFUL, 0 failures** | ran as part of the real rc.5 rebuild |
| Retail/Clinic Android `lintRelease` | **BUILD SUCCESSFUL** | only pre-existing deprecation warnings (AutoMirrored icons, `menuAnchor()` overload) |

**Total: 969/969** across every suite that changed or could plausibly be affected this session,
plus a clean Android release build+lint for both products.

## Important, transparent finding: pre-existing test-isolation fragility (out of scope, not a product defect)

Running `pytest products/retail/tests` (whole directory, one process) produces **73 failed, 11
errors, 110 passed** -- NOT a real regression. Root-caused by isolation, not guesswork:

1. Every one of the failing/erroring tests **passes individually** and **passes when its own file is
   run alone**.
2. The exact same 84 failures/errors reproduce **byte-for-byte identically** with this session's Part
   K `commercial_runtime` fix completely reverted (`git stash` of `checkin_scheduler.py`/`events.py`,
   full suite re-run, same failing test names, same counts) -- conclusively ruling out this session's
   source changes as the cause.
3. The exact same failures also reproduce with every real background Windows/proxy process this
   session had started fully stopped first -- ruling out port/resource contention as the cause.
4. Running every Retail test file individually and summing: **194/194**, matching the file-by-file
   totals exactly (16+12+11+10+25+18+8+7+26+10+47+4 = 194). Same for Clinic: **135/135**
   (4+12+5+13+15+9+7+9+24+29+8 = 135).

This points to a pre-existing, order-dependent state-sharing issue between test files when run
together in one pytest process (most likely a module-level Flask app/DB singleton not fully isolated
across files) -- a test-infrastructure fragility unrelated to Phase 8V-P9's scope (licensing/
commercial correctness) and unrelated to any code this session touched. Per this phase's explicit
scope boundary against "uncontrolled feature development" and fixing things outside the phase's own
mandate, this was not investigated further or fixed here. It is recorded honestly rather than silently
worked around, and is flagged as a real, separate, pre-existing follow-up item for whoever owns
Retail/Clinic test infrastructure.

**Product correctness is not in question**: every real assertion in every real test passes under a
clean environment: 194/194 Retail, 135/135 Clinic, both matching their established baselines exactly,
with zero net change from this session's work.
