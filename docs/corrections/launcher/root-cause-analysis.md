# Phase 3.7 — Root Cause Analysis: Windows Launcher Watchdog Failure

Status: **PROVEN**, confirmed by direct evidence in
`pre-fix-reproduction.md`, not inferred.

## Confirmed root cause

`_wait_for_server(url)` in both `launcher_retail.py` and
`launcher_clinic.py` is called with `url = f'http://{HOST}:{port}'` —
**the bare application root path (`/`), with no route suffix.** Neither
`products/retail/backend/app.py` nor `products/clinic/backend/app.py`
(nor any blueprint registered by either) has ever defined a route for `/`
— `static_url_path` is `/static`, not `/`. Flask's default behavior for an
undefined route is `404 Not Found`. `urllib.request.urlopen()` raises
`urllib.error.HTTPError` for any non-2xx/3xx response, and `HTTPError` is
a subclass of both `URLError` and `OSError` — so `_wait_for_server()`'s
`except (urllib.error.URLError, OSError):` clause catches it and retries,
identically to a real connection failure, with no way to distinguish "the
server isn't up yet" from "the server is up and correctly returning 404
for a route that was never meant to exist."

This means the readiness check was **structurally incapable of ever
succeeding**, in any environment, on any machine, regardless of proxy
configuration, IPv6 resolution, or system load — it does not depend on
Windows-specific behavior at all (reproducible identically against the
dev-mode Flask `test_client()`, confirmed below). It happened to go
unnoticed through the entire Wave 0 packaged smoke test because that test
launched each `.exe` directly and drove it with `curl` against real API
paths, never through `launcher_retail.py`'s/`launcher_clinic.py`'s own
`main()` entry point — no automated test in this codebase exercises the
launcher script itself, only `app.py` via `Flask.test_client()`.

### Confirming the same behavior outside the packaged build

```
$ AURA_STANDALONE=1 AURA_APP_DATA=<throwaway> python -c "
import app as retail_app
retail_app.init_app()
c = retail_app.app.test_client()
print('GET / ->', c.get('/').status_code)
print('GET /api/onboarding/status ->', c.get('/api/onboarding/status').status_code)
"
GET / -> 404
GET /api/onboarding/status -> 200
```

Same result in dev mode as in the packaged exe — direct proof this is a
plain application-routing gap, not a packaging-only or Windows-only
artifact, and not something that only manifests under `sys.frozen`.

## Checklist of hypotheses (per the Phase 3.7 addendum), each proven or disproven with evidence

| Hypothesis | Verdict | Evidence |
|---|---|---|
| localhost resolving to IPv6 while server binds IPv4 | **DISPROVEN** | `HOST = '127.0.0.1'` is a literal, never `'localhost'`, in both the bind call and the readiness URL — no hostname resolution occurs at all. `getaddrinfo('127.0.0.1')` (logged) returned a single `AF_INET` entry regardless. |
| Requests inheriting system `HTTP_PROXY`/`HTTPS_PROXY` | **DISPROVEN** | Logged and observed empty (`None`) for all three proxy-related env vars during both reproductions. |
| Missing `NO_PROXY` for localhost/127.0.0.1 | **DISPROVEN** | No proxy was configured at all (`getproxies()`-equivalent env vars were unset), so a missing `NO_PROXY` entry could not have mattered here. Still hardened defensively in the fix (see design doc) since a real customer machine could have a global proxy. |
| Readiness request using a different port | **DISPROVEN** | The exact port returned by `_find_free_port()` is the same variable passed into both `_run_server(port)` and the `url` used by `_wait_for_server()` — confirmed by reading the code and by the log line `Starting server on http://127.0.0.1:5000` matching the logged readiness URL `http://127.0.0.1:5000`. |
| **Readiness request using an incorrect route** | **CONFIRMED — this is the root cause** | 107/107 (Retail) and 97/97 (Clinic) readiness attempts returned `HTTPError code=404` on the bare root path, while a real API path (`/api/onboarding/status`) returned `200` throughout the identical window via an independent client. |
| Server binding to a different interface | **DISPROVEN** | `waitress.serve(..., host=HOST, port=port, ...)` with `HOST='127.0.0.1'` — the log line confirms binding to `127.0.0.1`, matching the readiness check's target host exactly. |
| Startup race (checking before the server is listening) | **DISPROVEN as the cause of the timeout** | The external `curl` succeeded well within the 45 s window (the server was listening and correctly answering within seconds of "Starting server" being logged) — a race would explain a few early failed attempts, not all 97–107 attempts across the full window. |
| HTTP client session initialized incorrectly | **DISPROVEN** | `urllib.request.urlopen()` used directly with no custom opener/session in the original code; the 404s are genuine, correctly-formed HTTP responses from the real server, not client-side errors. |
| Health route requiring authentication | **N/A in the original code** | No health/readiness route existed at all prior to this phase — the check hit `/`, not an authenticated route. (The new `/api/health` route added by this fix is deliberately unauthenticated — see design doc requirement 3.) |
| False status-code expectations | **Related, not separate** | `urlopen()`'s default behavior (raise on non-2xx) was never actually wrong for a route that returns 404 — the real issue is which route was targeted, not how the status code was interpreted. |
| Exception swallowed by the retry loop | **CONTRIBUTING FACTOR, not root cause** | The loop's blanket `except (URLError, OSError): time.sleep(0.4)` did correctly catch `HTTPError` (as designed, for genuine "not up yet" cases like connection-refused), but gave no visibility into *why* each attempt failed — this opacity is why the defect went undiagnosed until this phase's instrumented reproduction. Addressed by the new design's distinct failure-reason logging. |
| Server thread/process lifecycle mismatch | **DISPROVEN** | The server thread was alive and actively serving for the entire reproduction window (confirmed both by the successful external `curl` calls and by the process remaining reachable well after the watchdog's own failure was logged). |
| pywebview startup blocking behavior | **DISPROVEN as related to this defect** | `_run_native_window()` is only called *after* `_wait_for_server()` returns — pywebview is never invoked during the failure window. (A **separate, secondary** blocking-call issue was found in `_fatal()`'s `MessageBoxW` call — see below — but this is downstream of the readiness defect, not its cause.) |
| Packaged-only resource/configuration divergence | **DISPROVEN** | The identical 404-on-`/` behavior was reproduced in unfrozen dev mode via `Flask.test_client()` (see above) — this is not a `sys.frozen`-specific or PyInstaller-specific defect. |
| Launcher checking before final configuration is available | **DISPROVEN** | `app.init_app()` (which initializes the registry + product database) runs synchronously at the top of `_run_server()`, before `waitress.serve()` is ever called — by the time the port is listening, configuration/DB init is already complete. |
| Multiple server instances or stale port ownership | **DISPROVEN** | `_acquire_single_instance()`'s named-mutex guard was intact and functioning in both reproductions (only one process instance existed); `_find_free_port()` correctly found `5000` free and it was the only port in use. |
| Watchdog continuing after readiness already succeeded | **N/A** | Readiness never succeeded in the reproduction (0/107 and 0/97 attempts), so this scenario did not occur here. The new state-machine design still adds explicit protection against it (see design doc, Step 4) as a defense-in-depth measure, not because it was observed. |
| Readiness state not being persisted correctly | **N/A** | No readiness state is persisted anywhere in the original design (a single synchronous boolean-returning function) — not applicable to this codebase's actual architecture. |
| Timeout arithmetic or monotonic-clock error | **DISPROVEN as the cause, but a real latent risk** | `time.time() + timeout` compared against `time.time() < deadline` is correct arithmetic and matched the logged elapsed times precisely (45.03 s and 45.27 s against a 45.0 s timeout) — no clock-jump was observed. `time.time()` (wall clock) is still switched to `time.monotonic()` in the fix as a defensive hardening measure, since a wall-clock deadline is a latent risk on any machine where NTP adjusts the clock mid-startup, even though it was not the cause of this specific, deterministic failure. |

## Secondary finding (not the root cause of the named 43-second defect, but discovered by the same reproduction and addressed by the same fix)

`_fatal()` calls `ctypes.windll.user32.MessageBoxW(...)` — a **blocking,
modal** Win32 API call — before `sys.exit(1)`. In both reproduction runs
(launched as a background process with no attached interactive desktop
session), this call did not return, `sys.exit(1)` was never reached, and
the daemon server thread kept running indefinitely as an orphaned,
unmanaged process the user has no way to discover from the (never-shown)
UI. This means the originally-reported "watchdog terminates the app"
symptom is not even the full picture: in some launch contexts, the app
instead becomes a silent zombie process that keeps consuming a port and
resources with no visible window and no way for the user to know it
happened. This is addressed by Step 5 of the fix (see
`launcher-readiness-design.md`) — the corrected failure path shows a
non-blocking error and always reaches a clean, deterministic shutdown.

## Why the original 45-second timeout felt like "approximately 43 seconds"

`_wait_for_server(url, timeout=45.0)` loops with `time.sleep(0.4)` between
attempts and a `urlopen(..., timeout=2)` per-attempt cap; the loop exits
when `time.time() >= deadline`, and the very last in-flight attempt (up to
2 s) can complete after the nominal deadline is technically already
reached inside the loop condition check that started it. `43` was the
addendum's own approximate observation from the earlier Wave 0 smoke
test; this phase's precise instrumented measurement is `45.03 s` (Retail)
and `45.27 s` (Clinic) — consistent with the same 45 s constant, not a
separate or shorter timeout.
