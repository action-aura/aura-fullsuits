# Phase 9.5B-R2 — Cross-Product Regression Plan

## Suites to run from the final Phase 9.5B-R2 HEAD

| Suite | Historical baseline | Location (to be confirmed by real discovery) |
|---|---|---|
| Owner | 605/605 | `owner/tests/` (`pytest`) |
| commercial_runtime | 235/235 | to be located under repo root — canonical runner |
| Retail | 194/194 | to be located — canonical runner, order-independence required |
| Clinic | 135/135 | to be located — canonical runner |
| Phase 9 infrastructure/security | preflight + migration + schema-drift + scheduler + backup + logging + dependency/secret scan | existing scripts under `owner/app/commercial_ops/preflight.py` and prior Phase 9 tooling |

Historical combined total before new R2 tests: 605 + 235 + 194 + 135 = 1,169.
The real post-wave total must be reported as actually executed, not assumed —
new Owner tests from M7/M8 will raise the Owner figure; the other three
suites are expected to reproduce their historical totals unchanged since no
file outside `owner/` is touched by this wave.

## Method

1. Locate each suite's real runner command by inspecting the repo (not
   assumed from memory).
2. Run each to completion, capture exact pass/fail/skip counts.
3. Record exact commands and exact output in `final-complete-regression-report.md`.
4. Any suite that cannot be located or run is reported as NOT VERIFIED with
   the exact reason — never silently omitted or assumed passing.
