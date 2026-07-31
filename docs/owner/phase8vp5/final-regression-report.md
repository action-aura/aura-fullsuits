# Phase 8V-P5 — Final Automated Regression Report

## Result: ALL RUN, ALL PASS — product backends explicitly included this time

Run via `.venv\Scripts\python.exe` (the project's real, dependency-complete virtualenv at
`aura-fullsuits/.venv` -- the system-default `python` on PATH lacked `cryptography` and fails collection
entirely; using the wrong interpreter is not the same as tests being broken, but it was ruled out
explicitly here rather than assumed).

| Suite | Runner | Files | Tests | Result |
|---|---|---|---|---|
| Owner | `owner/` `pytest -q` (single process; no known cross-file pollution here) | -- | 395 | **395 passed**, 0 failed (7m13s) |
| commercial_runtime + licensing_contracts | `products/run_all_tests.py commercial_runtime licensing_contracts` (canonical one-process-per-file isolation runner, per its own AUDIT-010 docstring) | 23 | 219 | **219 passed**, 0 failed |
| Retail product backend | `products/run_all_tests.py retail` | 12 | 194 | **194 passed**, 0 failed |
| Clinic product backend | `products/run_all_tests.py clinic` | 11 | 135 | **135 passed**, 0 failed |
| **Total** | | **46 files** | **943** | **943 passed, 0 failed** |

Owner's 395 (vs. the 394 carried-forward baseline) reflects exactly the one new regression test added
this session (`test_create_emergency_extension_default_now_is_real_utc_not_shifted`); no other count
changed, consistent with the emergency-extension timezone fix being an isolated, contained change (a
`grep` for bare `datetime.utcnow()` across `owner/app` found no other occurrence).

## Areas actually exercised (spot-checked from the suite names, not merely assumed from the total)

Retail: launcher/support, backup/restore, capability guard, financial authority, import/export,
localization, onboarding, phase7 migration, **pricing (tax/discount calculations)**, **returns**,
security, Wave 1C financial gate. Clinic: backup/restore, capability guard, independence,
localization, onboarding/auth, **payment**, phase7 migration, privacy, RBAC, workflow, Wave 1C
financial gate. commercial_runtime/licensing_contracts: activation, Android-bridge identity, assertion
verifier, canonical schema, capability guard, check-in scheduler, client, deactivation, device
identity, events, Flask guard, trust-anchor generation/loading/store, internal sync routes, policy
evaluator, routes, state machine, state repository, status presenter, trusted time, migration safety.

## What this regression does NOT substitute for

Per the governing spec's own explicit instruction, a full automated pass does not by itself justify a
Phase 8 PASS -- it is one of many required dimensions. It does not exercise the physical device, the
real Owner HTTP surface end-to-end (that is the wire-capture evidence's job), or any of the scenarios
this session left NOT VERIFIED (see the per-scenario docs). See `phase8-final-decision.md` for the full,
honest per-dimension verdict.

## Root-level `tests/` directory

`tests/audit`, `tests/integration`, `tests/privacy`, `tests/security`, `tests/smoke`, `tests/tenancy`
are present in `pyproject.toml`'s `testpaths` but contain no test files (empty placeholder directories);
`pytest tests` collects 0 items. This is not a gap introduced or hidden this session -- confirmed by
directly listing the directory tree before concluding "no tests ran" meant something was wrong.
