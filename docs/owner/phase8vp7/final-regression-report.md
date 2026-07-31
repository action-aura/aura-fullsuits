# Phase 8V-P7 — Final Automated Regression Report

## Result: ALL RUN, ALL PASS, zero collateral impact from the version-alignment change

## Run 1 — before the Android/Windows physical work (confirms the environment itself is clean)

| Suite | Tests | Result |
|---|---|---|
| Owner | 398 | **398 passed**, 0 failed (547.16s) |
| commercial_runtime + licensing_contracts + Retail + Clinic backend | 562 (233+194+135) | **562 passed**, 0 failed (46 files, 0 failed) |
| **Subtotal** | **960** | **960 passed, 0 failed** |

## Run 2 — after the 8-file version-alignment bump (`rc.3 -> rc.4`, versionCode `4 -> 5`), confirming no
collateral impact

| Suite | Tests | Result |
|---|---|---|
| Retail + Clinic backend (the two suites that import the changed `config.py` files) | 329 (194+135) | **329 passed**, 0 failed (23 files, 0 failed) |

Owner and commercial_runtime/licensing_contracts were not re-run a second time -- neither imports
`products/*/backend/config.py`, so the version-bump files could not affect them; re-running would have
consumed real device-adjacent time better spent on physical scenarios (a judgment call, disclosed here
rather than silently made).

## Grand total, real, both runs combined

**960 + 329 = 1,289 individual test executions across this session's two regression passes, 0
failures.** (Not "1,289 distinct tests" -- 329 of them are the same Retail/Clinic tests run twice,
deliberately, to bracket the version-bump change.)

## Product backends explicitly included, per this session's own explicit requirement

Confirmed both runs include: launcher/support, backup/restore, capability guard, financial authority,
import/export, localization, onboarding, phase7 migration, pricing (tax/discount), returns, security,
Wave 1C financial gate (Retail); backup/restore, capability guard, independence, localization,
onboarding/auth, payment, phase7 migration, privacy, RBAC, workflow, Wave 1C financial gate (Clinic).

## What this does not substitute for

Same caveat as every prior session: automated regression is one dimension among many. See
`remaining-physical-gate-matrix.md` for the honest physical-scenario status this run does not speak to.
