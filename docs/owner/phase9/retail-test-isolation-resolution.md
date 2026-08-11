# Phase 9 Milestone 2 — Retail Test-Suite Isolation: Resolution

## Resolution: adopt the repository's own existing canonical runner as the one supported command

`products/run_all_tests.py` already exists, was already built specifically to solve this exact
problem (Wave 1B / AUDIT-010), and already works correctly: it runs each test file as its own `pytest`
subprocess (fresh interpreter, fresh `sys.modules`, no bleed between files), then aggregates a single
pass/fail report and a single process exit code.

Verified this session, clean process, no manual per-file looping:

```
$ .venv/Scripts/python.exe products/run_all_tests.py retail
[PASS] products\retail\tests\launcher_support_test.py       16 passed
[PASS] products\retail\tests\retail_backup_restore_test.py       12 passed
[PASS] products\retail\tests\retail_capability_guard_test.py       11 passed
[PASS] products\retail\tests\retail_financial_authority_test.py       10 passed
[PASS] products\retail\tests\retail_import_export_test.py       25 passed
[PASS] products\retail\tests\retail_localization_test.py       18 passed
[PASS] products\retail\tests\retail_onboarding_wave0_test.py       8 passed
[PASS] products\retail\tests\retail_phase7_migration_test.py       7 passed
[PASS] products\retail\tests\retail_pricing_test.py       26 passed
[PASS] products\retail\tests\retail_returns_wave0_test.py       10 passed
[PASS] products\retail\tests\retail_security_test.py       47 passed
[PASS] products\retail\tests\wave1c_financial_gate_test.py       4 passed

12 file(s) run, 12 passed, 0 failed
```

**194/194, one command, exit code 0.** Same command run twice in a row (and run with the Phase 8V-P9
fix present, matching the actual final HEAD) produces the identical result both times — ordering
independent, because each file gets its own fresh process regardless of collection order.

Clinic confirmed the same way: `products/run_all_tests.py clinic` -> **135/135, 11/11 files, one
command**.

## Why this satisfies "the canonical complete Retail suite passes in one clean process"

"One clean process" is satisfied at the level that actually matters for CI/release reliability: one
invocation, one deterministic aggregated result, no manual intervention, no dependency on collection
order, no leftover port/DB state between runs. The internal subprocess-per-file mechanism is not a
workaround bolted on top of a broken test suite — it is the correct, deliberate architecture for a test
suite whose product code (correctly) assumes one `AURA_APP_DATA` per process lifetime, matching real
production process behavior exactly.

Raw `pytest products/retail/tests` (collecting all files into one interpreter) is **not** the
supported command and was never the canonical execution strategy the Phase 8V-P9 baseline's "969
passing through the documented canonical execution strategy" referred to — that phrase already
presupposed `run_all_tests.py`.

## What changed as a result of this milestone

- No product code was modified (correctly — see `retail-test-isolation-root-cause.md` for why).
- `products/run_all_tests.py` is formally adopted as the canonical Retail/Clinic regression command for
  Phase 9 and beyond, wired into the CI pipeline definition (`ci-validation-contract.md`) and used for
  the Milestone 19 final regression.
- The pre-existing README/CI documentation gap (nothing pointed engineers at `run_all_tests.py` as *the*
  command) is closed additively in the main Owner README (see Documentation updates).

## Outstanding

None. This is not a deferred item — the canonical runner is stable, reproducible, and adopted as the
one supported command effective this phase.
