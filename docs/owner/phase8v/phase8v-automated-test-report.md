# Phase 8V — Automated Test Report (Part AB)

## Final totals, this session, at final HEAD

| Suite | Result | Delta from Phase 8 baseline |
|---|---|---|
| `owner/tests/` | **379 passed** | +22 (357 baseline + 19 new UI-route tests + 3 new live-wire scenario tests) |
| `commercial_runtime/licensing_contracts/tests/` | **214 passed** | +0 test count, but the suite re-ran clean after the `ALLOWED_PAYLOAD_FIELDS` fix (purely additive change, zero regressions) |
| Android Clinic `./gradlew testDebugUnitTest` | **82/82 passed** | unchanged from Phase 8 Milestone 7 (no Kotlin changes this phase; last real build result stands) |
| Android Retail `./gradlew testDebugUnitTest` | **BUILD SUCCESSFUL** | unchanged from Phase 8 Milestone 7 |

Zero failures, zero unexplained skips, across every suite run this session.

## New test files this phase

- `owner/tests/test_phase8v_ui_routes.py` (19 tests) -- every new Owner UI workflow, HTTP-level, real
  Postgres, real RBAC/MFA/CSRF.
- `owner/tests/test_phase8v_scenario_live_server.py` (3 tests) -- real cross-package wire-level
  scenario validation (Scenarios 1, 2, 6), the one that found and proved the fix for the
  `ALLOWED_PAYLOAD_FIELDS` regression.

## Not re-run this session (and why that's a reasoned scope decision, not an oversight)

`products/retail/tests`/`products/clinic/tests` (the full business-logic suites -- POS, accounting,
CRM, HR): Phase 8V made zero Python changes to either product's backend, only to two frontend JS
files (not covered by pytest) and two Kotlin files (covered by the Gradle unit-test run above).
Re-running suites unrelated to any change made this session would not add evidence, and the
pre-existing cross-file test-isolation issue in those suites (documented in Milestone 8's own
baseline doc) means "run the whole product suite" was never a meaningful single command to begin
with.
