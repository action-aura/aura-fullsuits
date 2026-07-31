# Phase 8V-P7 — Retained-Scenario Smoke Checks — Final

## Scenario 3: DONE, real, PASS

Full physical re-execution (not just a smoke check) on the rebuilt rc.4 Clinic artifact: real
check-in against the still-`EXPIRED` subscription (`installation_id c7150980-...`) reproduced real
`Restricted` state, same real unconfounded pattern (`WARN_ONLY` technical policy) as Phase 8V-P6's own
proof. Confirms the fix survives the rebuild cycle. See `raw-wire-final.md`.

## Scenario 2's own restriction proof reused this pattern for Retail

Not a "retained scenario" in the strict sense (Scenario 2 was never fully proven before this session),
but the same smoke-check *method* was applied to Retail for the first time this session -- see
`scenario2-retail-late-renewal-final.md`.

## Scenario 1, Scenario 4: not re-smoked this session

No source change in Phase 8V-P6 or this session touches early-renewal or pilot-conversion logic, so the
risk of silent regression is low, but no new physical confirmation was performed this session either
(the physical device disconnected before this lower-priority item was reached, after the higher-value
Scenario 2/3 work was completed). Per `retained-evidence-validity.md`'s own risk classification, this is
acceptable to carry forward without repetition absent a regression signal, but is recorded here plainly
as "not actively re-checked this session" rather than silently implied as re-verified.

## Scenario 5: not independently re-smoked this session

Phase 8V-P6's own real, physical, unconfounded proof (isolated effect + automatic expiry) stands
unchanged -- no source affecting emergency-extension wiring changed in this session. Re-running it would
require a fresh short-duration extension cycle on the physical device, which the disconnection
prevented. Carried forward as valid retained evidence per `retained-evidence-validity.md`.

## Overall

One full re-execution (Scenario 3), one first-time-for-Retail application of the same method
(Scenario 2's restriction half), two items carried forward without a fresh smoke check due to the
device disconnection (Scenario 1/4/5) -- disclosed honestly rather than assumed.
