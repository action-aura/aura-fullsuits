# Wave 1A Mobile Residual Risk Register

Items observed during Wave 1A that are not fixed in this wave, are not device defects in the MOB-* sense, or represent scope explicitly deferred by the user.

## R-1. Clinic Arabic localization coverage (MOB-007)
Most Clinic screens are not wired to `tr()` at all; only login, error-classifier messages, backup/restore, and role names are translated. RTL mirroring itself works correctly. Deferred to Wave 1B by explicit user decision. Estimated scope: 100+ strings across most screen files.

## R-2. Clinic Python test suite has pre-existing cross-file isolation pollution
Running `pytest products/clinic/tests/` as one invocation produces ~70+ failures (`sqlite3.OperationalError: no such table: users`) that do not occur when any test file is run individually. Proven via `git stash` that this is **not** caused by this wave's `from_date` backend change — the identical failure pattern (23 failed / 30 passed for workflow+rbac together) reproduces with that change fully reverted. Root cause is almost certainly shared/global registry-DB state across test modules with no `conftest.py` isolation. All 8 Clinic test files pass individually (108/108 tests). Not device-related; a backend test-harness issue for a future hardening pass, out of scope for a mobile validation wave.

## R-3. Device-specific background/network throttling (Transsion/XOS)
This test device aggressively kills backgrounded app processes and appears to throttle network reachability (including `adb forward`-relayed connections) to apps not currently in true foreground focus. Not a product defect — observed and worked around via `dumpsys window | grep mCurrentFocus` checks before every device-side verification. Worth noting for future testers on similar OEM skins; app resilience to this was explicitly verified in Part P (force-stop, background 15s/65s cycles) and passed.

## R-4. MOB-001-class "amount_paid omission" pattern not audited elsewhere
MOB-001's root cause (a client sending a locally-computed pre-tax preview as an authoritative payment amount) was fixed for Retail's sale checkout specifically. Other payment-adjacent flows (e.g. Retail customer/supplier credit payments, Clinic payments) were exercised this wave and did not reproduce the same defect, but a full contract audit of every "amount" field sent by the mobile clients was not performed as a dedicated pass.

## R-5. No production-signing / distribution workflow (explicitly out of scope)
Per the original Wave 1A instructions, production APK signing, installer/distribution workflow, and Windows AUDIT-022/023 remain out of scope for this wave and are not assessed here.

## R-6. Upgrade/data-preservation validated via incidental evidence, not a dedicated cold-baseline test
No prior Phase 4 APK build was available to install-then-upgrade from as a clean baseline on this specific device. Data-preservation across upgrades was instead validated empirically via ~10 real `adb install -r` cycles performed naturally over the course of this session (each carrying real code changes), with patient/appointment/invoice/sale/return data confirmed intact via direct API queries after every single one. This is strong evidence but not the exact "install old build, then upgrade" scenario the spec originally envisioned.
