# Phase 3.7 — Launcher Readiness Design

Status: **PROVEN** (design implemented; verification results in
`launcher-test-report.md` and `packaged-long-run-smoke-report.md`).

## What changed

1. **New unauthenticated health endpoint**: `GET /api/health` added to
   both `products/retail/backend/app.py` and `products/clinic/backend/app.py`,
   returning `{"status": "ok"}` with HTTP 200. Deliberately not gated by
   `mt_login_required` (the launcher polls it before any session could
   exist) and deliberately minimal (no business data, no auth state, no
   filesystem paths — nothing an attacker or a support screenshot could
   learn from it beyond "the process is alive").
2. **New shared module**: `commercial_runtime/launcher_support.py` —
   `LauncherState` (the lifecycle enum), `check_readiness()` (the actual
   polling logic, pure and dependency-injectable), `health_url()`,
   `no_proxy_opener()`, `show_fatal_dialog()`. Both launchers import and
   use this instead of each having its own copy of the logic that was, in
   fact, an identical copy of the same bug (see `root-cause-analysis.md`'s
   observation that both launcher files' docstrings already said "identical
   structure" — the duplication itself was part of how this class of bug
   could exist twice, unnoticed, for as long as it did).
3. **Rewritten `launcher_retail.py`/`launcher_clinic.py`**: explicit
   `LauncherState` transitions logged at every step, readiness now targets
   `/api/health` instead of `/`, and the failure-dialog path is
   time-bounded.

## Requirement-by-requirement

1. **Explicit loopback address**: `HOST = '127.0.0.1'` (a literal, never
   `'localhost'`) is used identically for both the server bind
   (`waitress.serve(host=HOST, ...)`) and the readiness URL
   (`health_url(HOST, port)`) — unchanged from before (this was never the
   defect), and explicitly commented in both launchers as loopback-only.
2. **Bypass external HTTP proxies for local readiness checks**:
   `no_proxy_opener()` builds a `urllib.request.build_opener(urllib.request.ProxyHandler({}))`
   — an empty proxy mapping overrides any environment/registry-detected
   proxy for that opener specifically. Scoped to the readiness check's own
   opener only; does not touch `os.environ` or affect any other networking
   in the process. Not confirmed as the original defect's cause (proxy env
   vars were unset in every reproduction — see `root-cause-analysis.md`),
   but independently justified: a real customer machine could have a
   global proxy configured, and a loopback health check should never
   depend on it.
3. **Dedicated unauthenticated local health/readiness endpoint revealing no
   sensitive information**: `GET /api/health` → `{"status": "ok"}`, no auth
   required, no PII/business data/paths in the response.
4. **Treat only the intended successful response as ready**: `check_readiness()`
   only returns `ready=True` when the response status is exactly `200`;
   any other status (including a redirect or a 4xx/5xx) is recorded as
   `health_endpoint_error` and does not count as success.
5. **Bounded retries and a monotonic deadline**: `check_readiness()` uses
   `time.monotonic()` (via the injectable `now` parameter, defaulting to
   `time.monotonic`) for both the deadline and elapsed-time accounting —
   immune to wall-clock adjustments (NTP sync, DST, manual clock changes)
   that could have corrupted the old `time.time()`-based deadline. Retries
   are bounded by `timeout_seconds` (default `45.0`, unchanged budget) and
   `poll_interval` (`0.4s`, unchanged).
6. **Log a safe reason when startup fails**: every failure now carries a
   `reason_code` (`PORT_UNAVAILABLE`, `SERVER_PROCESS_EXITED`,
   `HEALTH_ENDPOINT_ERROR`, `STARTUP_TIMEOUT`) logged and shown in the
   failure dialog, plus `result.detail` (a safe, human-readable string —
   e.g. `"Health endpoint returned HTTP 404."` — never a raw exception
   object, stack trace, or file path).
7. **Stop retrying permanently once readiness succeeds**: `check_readiness()`
   returns immediately on the first `200` response — no further attempts
   occur.
8. **Never later convert a successful startup into a timeout failure**:
   `check_readiness()` is called exactly once, synchronously, in `main()`.
   There is no background timer, thread, future, or callback anywhere in
   either launcher that can independently fire a timeout after this call
   returns — `main()`'s only path to `_fatal()` for a timeout reason is the
   `if not result.ready:` branch immediately after the single
   `check_readiness()` call. Once `_set_state(LauncherState.READY)` runs,
   nothing downstream re-invokes readiness logic.
9. **Distinguish port unavailable / process exited / server not ready /
   health endpoint error / client-network error**:
   - `PORT_UNAVAILABLE` — `_find_free_port()` exhausted its range before
     `check_readiness()` is ever called.
   - `SERVER_PROCESS_EXITED` (`reason='process_exited'`) — the injected
     `is_process_alive` (`server_thread.is_alive`) returned `False` before
     any successful check; checked on every loop iteration, so this fails
     fast rather than waiting out the full timeout.
   - `HEALTH_ENDPOINT_ERROR` (`reason='health_endpoint_error'`) — the
     endpoint responded at least once, but never with `200` (the exact
     failure mode this whole phase was triggered by, when the target was
     still `/`).
   - `STARTUP_TIMEOUT` (`reason='not_ready_timeout'`) — the deadline was
     reached with no HTTP response of any kind (genuine "still starting" or
     "never came up" case) — client/network-level failures (connection
     refused, etc.) accumulate into this bucket via `saw_http_response`
     staying `False`.
10. **Do not terminate a confirmed healthy server because one later probe
    fails**: there is no later probe — nothing in either launcher polls
    `/api/health` again after `READY` is reached. The post-ready loop
    (`while server_thread.is_alive(): time.sleep(1)`) only checks thread
    liveness, never makes an HTTP request, and cannot trigger `_fatal()`.
11. **Do not hide genuine startup failures**: `_fatal()` still logs to
    `startup.log` and (when frozen) still shows a dialog to the user — see
    Step 5's design below.
12. **Do not bind the application server to LAN-accessible interfaces**:
    unchanged and verified — `waitress.serve(host=HOST, ...)` with
    `HOST='127.0.0.1'` literal; confirmed by the `Starting server on
    http://127.0.0.1:<port>` log line in every reproduction and smoke test.
13. **Do not disable TLS verification globally or modify unrelated
    networking**: the proxy bypass is scoped to one opener instance created
    fresh inside `check_readiness()`; nothing touches `ssl` context
    defaults, `os.environ`, or any other HTTP client in either product.
14. **Preserve current single-instance behavior**: `_acquire_single_instance()`
    is byte-for-byte unchanged in both launchers.
15. **Preserve clean shutdown and port release**: unchanged shutdown path
    (`server_thread` is a daemon thread; process exit releases the socket).
    `check_readiness()`'s `urlopen`/opener calls use `with opener.open(...) as resp:`,
    explicitly closing each polling connection rather than leaking sockets
    across up to ~112 polling attempts (45s ÷ 0.4s) per launch.

## Startup state machine (Step 4)

```
NOT_STARTED -> STARTING -> READY -> UI_RUNNING -> STOPPING -> STOPPED
                    \-> FAILED -> STOPPED
```

Implemented as `commercial_runtime.launcher_support.LauncherState` (a plain
`Enum`) plus a single module-level `_state` variable per launcher, mutated
only through `_set_state()`, which logs every transition
(`[state] starting -> ready`, etc.) to `startup.log`. This is intentionally
**not** a generic state-machine framework — per the phase's own instruction
not to overengineer, it is the smallest addition that makes the lifecycle
explicit, observable in logs, and easy to reason about: there is exactly
one function (`main()`) that drives transitions, exactly one place readiness
is checked, and exactly one place a fatal failure is declared.

**Watchdog cancellation**: because `check_readiness()` is a single
synchronous call (not a persistent background timer), there is nothing to
"cancel" in the traditional sense — the watchdog *is* the call, and it
naturally cannot fire again once it has returned. This is a deliberately
simpler and more verifiable design than a cancellable-timer approach: a
timer that must be remembered to be cancelled is exactly the kind of
mechanism that produces bugs like the one this phase fixed (a check that
kept running/mattering long after it should have stopped). The test suite
(`launcher-test-report.md`, scenario 6) verifies this property directly:
after `check_readiness()` returns `ready=True`, calling the readiness
-dependent failure path is not possible without a second explicit call,
which the launcher code demonstrably never makes.

## Failure behavior (Step 5)

`_fatal(message, reason_code)`:
1. Transitions to `FAILED`, logs `[reason_code] message` to `startup.log`
   (safe text only — never a raw exception object or stack trace).
2. If frozen (a real packaged build), shows a failure dialog via
   `show_fatal_dialog()` — see below.
3. Transitions to `STOPPED`.
4. Calls `sys.exit(1)`.

**Time-bounded failure dialog** (the second, secondary defect found during
reproduction — see `root-cause-analysis.md`'s "Secondary finding"):
`show_fatal_dialog()` runs the blocking `MessageBoxW` call on a daemon
thread and joins it with a `120s` timeout. A real interactive user still
sees a normal modal dialog and can dismiss it at their own pace (the join
timeout is generous specifically so a real person reading the message
isn't cut off). A non-interactive launch context (no desktop session
available to display a modal on — the exact context this phase's automated
reproduction ran in) can no longer block `sys.exit(1)` indefinitely: after
120s with no dialog interaction possible, the launcher proceeds to
`STOPPED` and exits, releasing the port instead of leaving an orphaned
server process running invisibly, which is what was observed in both
Phase 3.7 reproductions before this fix.

The dialog message includes the safe `reason_code` (e.g.
`STARTUP_TIMEOUT`) as a diagnostic reference and points to
`logs\startup.log` — no stack trace, no filesystem paths beyond the
already-known log location, no environment variable values.
