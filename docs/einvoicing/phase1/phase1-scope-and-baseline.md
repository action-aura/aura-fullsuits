# JoFotara e-invoicing — Phase 1 scope and baseline

## Governing commit

`09c6435ec7151cb36d92c54b02558a5b6b8296b5` (2026-08-02 13:27:26 +0300) —
`fix: switch scheduled-task install from CIM ScheduledTasks module to
schtasks.exe`. Working tree clean at this commit before Phase 1 work started
(only untracked addition: this `docs/einvoicing/` directory itself).

## Scope

Add opt-in (default OFF) Jordan JoFotara (ISTD) e-invoicing to both Aura
Retail and Aura Clinic, built as a shared module in `commercial_runtime/`.
Full rationale and architecture: `jofotara-integration-architecture.md`. Full
ordered step list: the implementation plan produced for this wave (this
directory).

Explicitly two-phase:
- **Phase 1** (this directory): full pipeline, tested end-to-end against a
  `MockProvider`. Zero live ISTD network calls anywhere in Phase 1 code.
- **Phase 2** (`docs/einvoicing/phase2/`): real ISTD field mapping, gated on
  the business obtaining official ISTD portal credentials and integration
  docs. Not executed as part of this wave.

## Baseline test run

Full existing suite, run via `python products/run_all_tests.py`, before any
Phase 1 code was written:

```
46 file(s) run, 46 passed, 0 failed
```

| Area | Files | Tests passed |
|---|---:|---:|
| `products/retail/tests/` | 12 | 194 |
| `products/clinic/tests/` | 11 | 135 |
| `commercial_runtime/` (`tests/` + `licensing_contracts/tests/`) | 23 | 235 |
| **Total** | **46** | **564** |

Full per-file breakdown preserved in this wave's task-runner log at the time
of this commit (see `phase1-test-report.md`, written at the end of this
phase, for the equivalent post-implementation run to diff against this one).

## Non-negotiable invariant for this phase

Every one of the 564 tests above must still pass, unchanged, at the end of
Phase 1. Additionally, two new regression suites
(`retail_einvoicing_regression_test.py`,
`clinic_einvoicing_regression_test.py` — see `outbox-state-machine.md` and
the implementation plan's Part 11) are written *before* any feature code, to
lock down flag-OFF behavior as byte-for-byte identical to this baseline.
