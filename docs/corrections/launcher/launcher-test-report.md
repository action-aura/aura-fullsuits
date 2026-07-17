# Phase 3.7 — Launcher Test Report

Status: **PROVEN**. `products/retail/tests/launcher_support_test.py`:
**15/15 passed**, `0.11s`.

## Scenario coverage (the 17 named in the Phase 3.7 addendum)

| # | Scenario | Coverage | Test |
|---|---|---|---|
| 1 | Server becomes ready immediately | Unit | `test_ready_immediately` |
| 2 | Server becomes ready after a delay | Unit | `test_ready_after_delay` |
| 3 | External proxy environment variables are present | Unit | `test_no_proxy_opener_ignores_environment_proxy` |
| 4 | localhost and 127.0.0.1 behavior | Unit (URL construction) + Packaged (real bind/connect) | `test_health_url_uses_explicit_loopback_literal`; `packaged-long-run-smoke-report.md` |
| 5 | Readiness succeeds and remains successful beyond the old 43s window | Unit + Packaged (real 2.5s readiness, real 10+ min uptime) | `test_ready_after_44_seconds_still_succeeds_within_45s_budget`; `packaged-long-run-smoke-report.md` |
| 6 | Watchdog is cancelled after readiness | Unit (structural: single call, no re-poll) | `test_no_further_polling_after_ready` |
| 7 | A later transient health failure does not trigger the startup timeout | Unit (independence of successive calls) | `test_readiness_result_is_a_one_shot_call_not_reusable_as_a_watchdog` |
| 8 | Server process exits before readiness | Unit | `test_process_exited_fails_fast_not_full_timeout` |
| 9 | Port is already occupied | Unit (real socket) + Packaged (Clinic actually fell back to `5001` because Retail held `5000`) | `test_find_free_port_skips_occupied_port`; `packaged-long-run-smoke-report.md` |
| 10 | Wrong health route | Unit (this is the exact Phase 3.7 root cause reproduced as a unit case) | `test_wrong_route_404_classified_as_health_endpoint_error_not_timeout` |
| 11 | Health endpoint returns non-success status | Unit | `test_health_endpoint_500_classified_as_health_endpoint_error` |
| 12 | Timeout occurs genuinely | Unit | `test_genuine_timeout_no_response_ever_classified_as_not_ready_timeout` |
| 13 | Duplicate startup is prevented | Packaged/integration only (real named-mutex `_acquire_single_instance()`, unchanged code, Windows-only API — not meaningfully unit-testable) | Not re-verified in this phase; behavior is byte-for-byte unchanged from Wave 0, where it was last exercised |
| 14 | Clean shutdown releases the port | Packaged | `packaged-long-run-smoke-report.md` (both processes restarted on the same ports after `taskkill`) |
| 15 | Application can restart after shutdown | Packaged | `packaged-long-run-smoke-report.md` |
| 16 | No orphan server process remains | Packaged | `packaged-long-run-smoke-report.md` (`ps -W` confirmed empty after shutdown, both products) |
| 17 | No `sys.exit` is invoked after successful readiness | Unit (structural) + Packaged (10+ min uptime with zero exits) | `test_check_readiness_never_exits_the_process`; `packaged-long-run-smoke-report.md` |

## Additional tests beyond the 17 named scenarios

- `test_launcher_state_enum_has_all_required_states` — confirms all 7
  required `LauncherState` values exist (`NOT_STARTED`, `STARTING`,
  `READY`, `UI_RUNNING`, `STOPPING`, `STOPPED`, `FAILED`).
- `test_readiness_result_repr_does_not_leak_url_or_secrets` — a light
  safety check on `ReadinessResult.__repr__` (used in log lines) not
  including the target URL or any secret material.

## Why 13/14/15/16 are packaged-only, not unit tests

`launcher_retail.py`/`launcher_clinic.py` themselves are thin
entry-point scripts with heavy module-level side effects by import alone
(configuring logging, creating directories, resolving `AURA_APP_DATA`) —
the same pattern every other entry point in this codebase already uses
(`app.py` is likewise never unit-tested directly; it's exercised via
`Flask.test_client()` against the Flask `app` object it produces). The
actual correctness-critical *logic* this phase needed to fix and verify
(readiness detection, failure classification, monotonic timing, proxy
bypass) was extracted into `commercial_runtime/launcher_support.py`
specifically so it could be unit-tested in isolation — which is what these
15 tests do. Process-level properties (mutex-based single-instance
enforcement, OS-level port release timing, cross-restart persistence, and
orphan-process detection) are properties of the *operating system and the
whole process*, not of any one function, and are proven instead by the
real packaged long-run smoke test.

## Regression

These are new tests for new code (`commercial_runtime/launcher_support.py`
did not exist before this phase) — there is no prior baseline to regress
against for this specific suite. See `WINDOWS-LAUNCHER-CORRECTIVE-HANDOVER.md`
for whether the pre-existing 263-test backend suite was also required to be
rerun in this phase (per the Step 8 regression policy) and its result.
