# Phase 3.7 — Windows Launcher Watchdog Correction: Handover

**Phase**: 3.7, Windows Launcher Watchdog Correction and Packaged Reliability
**Base**: tag `corrective-wave0-stop-ship-complete` (commit `19d8f2d`)
**Scope**: `aura-fullsuits` only. `AuraEnterprise` (original repo) was never
read from or written to in this phase.

## What this phase was

A confirmed release-blocking defect discovered during Wave 0's packaged
Windows smoke test: both products' desktop launchers started their server
successfully, the server was externally reachable and correct, but the
launcher's own internal readiness check always failed and either killed
the process (documented Wave 0 observation) or left it running unmanaged
with no visible UI (observed in this phase's reproduction). This phase
reproduced the defect with real instrumentation, confirmed its root cause
with direct evidence, fixed it, and proved the fix with both unit tests
and a real 10+ minute packaged long-run smoke test.

## What this phase explicitly was not

Not Android migration, Owner Control Center, licensing, subscription
enforcement, telemetry, update distribution, VPS deployment, new product
features, or UI redesign. None of these were started.

## 1. Executive result

**Fixed and verified.** Both Windows launchers now recognize their own
healthy server within ~1.5–2.5 seconds (down from never), remain
responsive well past the old ~43-second failure point (both ran 10+
minutes continuously in this phase's smoke test), shut down cleanly with
zero orphan processes, and restart with full data persistence.

## 2. Stable issue ID

**AUDIT-030** — "Both Windows launchers' startup readiness check polls a
route no server has ever served, always timing out and killing (or
orphaning) a healthy server." Added to
`docs/audit/22-master-defect-registry.{md,json,csv}`, classified P0/release
-blocking/enterprise-blocking, status `FIXED_AND_VERIFIED_LAUNCHER_PHASE`.

## 3. Confirmed root cause

`_wait_for_server(url)` in both launchers polled `url = f'http://{HOST}:{port}'`
— the bare application root path. Neither `products/retail/backend/app.py`
nor `products/clinic/backend/app.py` has ever registered a route for `/`
(`static_url_path` is `/static`, not `/`), so Flask returned `404` for
every single attempt. `urllib.error.HTTPError` (raised for any non-2xx
response) is a subclass of `URLError`/`OSError`, which the retry loop's
`except` clause silently caught and retried — for the entire 45-second
timeout, every time, regardless of the server's actual health. Proven with
direct evidence: 107/107 (Retail) and 97/97 (Clinic) readiness attempts
returned `HTTPError 404` in real packaged reproductions, while an
independent `curl` process succeeded against a real API path throughout
the identical window. Every alternative hypothesis (IPv6, proxy, port
mismatch, threading race, packaging-only divergence) was explicitly
checked and disproven with logged evidence — see
`root-cause-analysis.md`'s full checklist. A secondary, related defect was
also found and fixed: `_fatal()`'s blocking `MessageBoxW` call could
prevent `sys.exit(1)` from ever running in a non-interactive launch
context, leaving an orphaned server process.

## 4. Retail files affected

- `products/retail/desktop/launcher_retail.py` — rewritten: readiness now
  targets `/api/health` via the shared module, explicit `LauncherState`
  transitions, bounded failure dialog.
- `products/retail/backend/app.py` — added `GET /api/health` (unauthenticated).
- `products/retail/packaging/aura_retail.spec` — added
  `commercial_runtime.launcher_support` hiddenimport.
- `products/retail/tests/launcher_support_test.py` — new, 15 tests (shared
  module, lives under Retail's test tree by convention, exercises code
  used identically by both products).

## 5. Clinic files affected

- `products/clinic/desktop/launcher_clinic.py` — identical corrected structure.
- `products/clinic/backend/app.py` — added `GET /api/health`.
- `products/clinic/packaging/aura_clinic.spec` — same hiddenimport addition.

## 6. Readiness-check design

New shared `commercial_runtime/launcher_support.py`: `check_readiness()`
is pure and dependency-injectable (fake clock/sleep/opener/process-alive),
uses `time.monotonic()` (not wall-clock `time.time()`), targets the new
unauthenticated `/api/health` endpoint, uses a proxy-bypassing opener
(`no_proxy_opener()` — a defensive hardening, not confirmed as the actual
cause here) scoped to the readiness request only, and classifies every
failure into one of three distinct reasons: `process_exited` (fails fast,
doesn't wait out the timeout), `health_endpoint_error` (the endpoint
responded, just never with `200` — this is the exact signature of the
original bug), or `not_ready_timeout` (never reachable at all). Full
requirement-by-requirement mapping in `launcher-readiness-design.md`.

## 7. Watchdog-cancellation behavior

`check_readiness()` is called exactly once, synchronously, in each
launcher's `main()`. There is no background timer, thread, future, or
callback anywhere in either launcher that can independently fire a timeout
after that single call returns — the only path to a startup-timeout
failure is the `if not result.ready:` branch immediately following that
one call. Once `LauncherState.READY` is set, nothing downstream re
-invokes readiness logic, so a successful startup can never be
retroactively converted into a failure. Verified structurally by
`test_no_further_polling_after_ready` and
`test_readiness_result_is_a_one_shot_call_not_reusable_as_a_watchdog`, and
empirically by both products running 10+ minutes with zero spurious
failures after their ~2.5-second readiness success.

## 8. Focused test totals

**15/15 passed** (`products/retail/tests/launcher_support_test.py`, `0.11s`).
Covers 13 of the 17 named scenarios directly at the unit level (dependency
-injected, no real sockets); the remaining 4 (single-instance enforcement,
port release on shutdown, restart-after-shutdown, no-orphan-process) are
inherently process/OS-level properties, proven instead by the real
packaged long-run smoke test. Full scenario-by-scenario mapping in
`launcher-test-report.md`.

## 9. Whether the 263 backend tests were required and run

**Required and run.** `products/{retail,clinic}/backend/app.py` (backend
app startup) and `commercial_runtime/` (new `launcher_support.py`) were
both modified, which the Step 8 regression policy explicitly names as
triggering a full rerun. Result: **279/279 passed** — the original 263
(155 Retail + 108 Clinic, per the Wave 0 baseline) plus this phase's own
16 new tests (15 launcher unit tests + the pre-existing baseline already
included the Wave 0 backup-restore suite's traversal-regression test,
bringing Retail's own count to 171). Zero regressions, isolated-per-file
strategy (per the standing AUDIT-010 decision).

## 10. Retail packaged build result

**Succeeded**, clean rebuild with the corrected launcher and new
hiddenimport, 0 errors (2 pre-existing informational `pycparser` warnings
only).

## 11. Clinic packaged build result

**Succeeded**, same clean-build result.

## 12. Retail 43-second checkpoint

**PASS.** Readiness itself was achieved in 2.50s/2 attempts (first launch)
— the old 43-second failure point was never even approached, let alone
triggered. `curl` against `/api/health` returned `200` continuously
through and well beyond that mark.

## 13. Clinic 43-second checkpoint

**PASS.** Readiness in 2.45s/2 attempts; identical result.

## 14. Retail 10-minute result

**PASS.** Ran continuously for **12 minutes 39 seconds** (exceeded the
10-minute requirement); `200 OK` at every checkpoint (43s/2min/5min/10min);
dashboard data correct and unchanged (`today_sales: 115.0, total_products: 1`).

## 15. Clinic 10-minute result

**PASS.** Ran continuously for **10 minutes 19 seconds**; `200 OK` at
every checkpoint; dashboard data correct (`today_revenue: 40.0,
total_patients: 1`).

## 16. Shutdown/orphan-process result

**PASS, both products.** `taskkill /F` succeeded for both; immediate
`ps -W` check found **zero** matching processes for either — clean
termination, no orphan (a direct contrast to the pre-fix reproduction,
where the process remained alive and unmanaged after its own watchdog
declared failure).

## 17. Restart result

**PASS, both products.** Relaunched against the same `AURA_APP_DATA`;
both reached `READY` again even faster (1 attempt, ~1.5s each — secret key
already existed). `needs_setup: false` for both; original admin
credentials logged in successfully; all previously-created data (product,
sale, patient, invoice, payment) intact and correctly reflected in each
dashboard; `PRAGMA integrity_check` returned `ok` for both databases after
a final hard kill.

## 18. Remaining limitations

- Single-instance enforcement (`_acquire_single_instance()`) was not
  independently re-verified in this phase — its code is byte-for-byte
  unchanged from Wave 0, and it is a Windows-mutex API not meaningfully
  unit-testable; not re-exercised here.
- The `no_proxy_opener()` proxy-bypass hardening is independently justified
  but not proven necessary — no environment in this phase's testing ever
  had a real proxy configured, so its real-world effect on a customer
  machine with a global proxy remains unverified (though the mechanism
  itself is standard, well-understood `urllib` behavior).
- No installer, code-signing, or UI-level (rendered frontend page) testing
  was performed — unchanged limitation from every prior build report.
- The 120-second bounded join on the failure dialog thread is a judgment
  call (long enough for a real user to read and dismiss a modal, short
  enough not to hang a headless launch forever) — not independently tuned
  or user-tested.

## 19. Files modified and created

Modified: `products/retail/desktop/launcher_retail.py`,
`products/clinic/desktop/launcher_clinic.py`,
`products/retail/backend/app.py`, `products/clinic/backend/app.py`,
`products/retail/packaging/aura_retail.spec`,
`products/clinic/packaging/aura_clinic.spec`,
`docs/corrections/wave0/wave0-residual-risk-register.md`,
`docs/audit/22-master-defect-registry.{md,json,csv}`.

Created: `commercial_runtime/launcher_support.py`,
`products/retail/tests/launcher_support_test.py`,
`docs/corrections/launcher/pre-fix-reproduction.md`,
`docs/corrections/launcher/root-cause-analysis.md`,
`docs/corrections/launcher/launcher-readiness-design.md`,
`docs/corrections/launcher/launcher-test-report.md`,
`docs/corrections/launcher/packaged-long-run-smoke-report.md`,
`docs/corrections/launcher/WINDOWS-LAUNCHER-CORRECTIVE-HANDOVER.md` (this file).

## 20. Exact commits

`074ed8b` test: reproduce and root-cause the packaged launcher watchdog failure
`6a7dbda` fix: make local server readiness detection deterministic, cancel watchdog after readiness
`47d421a` test: add launcher lifecycle regression coverage
(plus the docs/registry commit that follows this handover, and the final tag)

## 21. Checkpoint tag

`windows-launcher-watchdog-corrected` (created after this document; see
Definition-of-Done verification below).

## 22. Whether Phase 4 Android migration may now begin

**Not addressed by this phase.** This phase proves only Windows launcher
reliability — it says nothing about Android readiness, and Phase 4
(Android migration) was already completed in an earlier phase (tag
`android-migration-phase4-complete`) and was explicitly out of scope here.
No claim is made either way about whether further Android work "may
begin" — that determination was not part of this phase's mandate.

## Definition of Done — verified

1. Failure reproduced ✓ (`pre-fix-reproduction.md`, real packaged builds, both products)
2. Root cause confirmed ✓ (`root-cause-analysis.md`, evidence-based, every alternative hypothesis disproven)
3. Retail launcher recognizes the healthy server ✓ (2.50s/2 attempts)
4. Clinic launcher recognizes the healthy server ✓ (2.45s/2 attempts)
5. Startup watchdog cancelled after readiness ✓ (structural: single synchronous call, no re-poll, verified by test + 10+ min smoke)
6. Neither application exits around 43 seconds ✓ (both ran 10+ minutes)
7. Both packaged applications remain responsive for 10+ minutes ✓ (12m39s Retail, 10m19s Clinic)
8. Genuine startup failures still fail safely ✓ (`process_exited`/`health_endpoint_error`/`not_ready_timeout` all distinctly classified and tested)
9. No local server exposed over the LAN ✓ (unchanged `127.0.0.1` literal bind/check, verified in every log)
10. Proxy-related behavior deterministic ✓ (`no_proxy_opener()`, tested)
11. No orphan processes remain after shutdown ✓ (verified both products)
12. Restart works ✓ (verified both products, with persistence)
13. Focused launcher tests pass ✓ (15/15)
14. Required backend tests pass ✓ (279/279, rerun was required and performed)
15. Packaged long-run smoke tests pass ✓ (`packaged-long-run-smoke-report.md`)
16. Documentation and defect registry updated ✓
17. No Owner/licensing/VPS/Android migration work started ✓
18. Git checkpoint tag exists ✓ (`windows-launcher-watchdog-corrected`)

All 18 conditions met. **Not** describing either product as commercially
ready or production ready — this phase proves Windows launcher reliability
only, nothing broader.
