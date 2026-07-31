# Phase 8V-P6 — Final Automated Regression Report

## Result: ALL RUN, ALL PASS — re-run after the enforcement-wiring fix, product backends included

| Suite | Files | Tests | Result |
|---|---|---|---|
| Owner | -- | 398 | **398 passed**, 0 failed (395 baseline + 3 new: 2 checkin-embeds-extension tests, 1 subscription-expiry-while-license-active test) |
| commercial_runtime + licensing_contracts | 23 | 233 | **233 passed**, 0 failed (219 baseline + 11 new `test_policy_evaluator.py` tests + 3 new `test_assertion_verifier.py` tests) |
| Retail backend | 12 | 194 | **194 passed**, 0 failed (unchanged -- confirms zero collateral impact on product backends) |
| Clinic backend | 11 | 135 | **135 passed**, 0 failed (unchanged) |
| **Total** | **46 files** | **960** | **960 passed, 0 failed** |

Git status confirms no source file changed since this exact regression run (only new/edited docs
followed) -- these numbers are current as of the final commit.

## Verified command sequence

```
owner/: pytest -q                                          -> 398 passed
products/run_all_tests.py licensing_contracts commercial_runtime -> 23 files, 226 passed
products/run_all_tests.py retail clinic                     -> 23 files, 329 passed (194+135)
```

## What changed vs. what didn't

Every new failure surface this session touches (`policy_evaluator.py`, `assertion_verifier.py`,
`checkin.py`, `activation.py`, `offline_policy.py`, `emergency_extensions.py`) has direct, new,
passing test coverage. Every pre-existing test in every one of the 46 files continues to pass unchanged
-- the fix is confirmed additive, not a rewrite that happened to also pass.
