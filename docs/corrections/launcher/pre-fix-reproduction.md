# Phase 3.7 — Pre-Fix Reproduction: Windows Launcher Watchdog Failure

Status: **PROVEN**. Real packaged Windows builds, real running processes,
real instrumented logs. No claim below is estimated.

## Method

Temporary diagnostic instrumentation was added to `_wait_for_server()` in
both `products/retail/desktop/launcher_retail.py` and
`products/clinic/desktop/launcher_clinic.py` (logging only — readiness URL,
proxy environment variables, `getaddrinfo` resolution, and per-attempt
exception type/HTTP status/elapsed time; no secrets or business data). Both
products were rebuilt with PyInstaller and launched as real `.exe`
processes against a throwaway `AURA_APP_DATA` directory, and left running
long enough for the watchdog to fire on its own.

## Retail — `dist/AuraRetail/AuraRetail.exe`

- Selected port: `5000` (first attempt in `_find_free_port()`'s range succeeded).
- Server bind address: `127.0.0.1:5000` (confirmed from the `waitress`
  startup log line `Starting server on http://127.0.0.1:5000`).
- Server thread state: started successfully; `waitress.serve()` was
  actively accepting connections (see external curl result below).
- Readiness URL used internally: **`http://127.0.0.1:5000`** — bare root
  path, no `/api/...` suffix.
- Proxy environment observed at readiness-check time:
  `HTTP_PROXY=None HTTPS_PROXY=None NO_PROXY=None` — no proxy configured.
- `getaddrinfo('127.0.0.1')` resolved to a single `AF_INET` entry,
  `('127.0.0.1', 0)` — no IPv4/IPv6 ambiguity.
- Internal readiness attempts: **107 attempts over 45.03 seconds — every
  single one raised `urllib.error.HTTPError code=404 reason='NOT FOUND'`.**
  Zero attempts succeeded.
- External verification (separate `curl` process, run concurrently with
  the internal polling loop): `GET /api/onboarding/status` returned `200`
  with a valid JSON body throughout the same window the internal check was
  failing.
- Exact reason `sys.exit(1)` was invoked: `_wait_for_server()` returned
  `False` after its 45 s deadline elapsed with zero successful attempts,
  causing `main()` to call `_fatal('The Aura Retail server did not start
  in time.')`.
- **Secondary observation**: after `_fatal()` logged the error, the
  process **did not actually terminate** in this run — `ps -W` still
  showed `AuraRetail.exe` running, and it continued answering HTTP
  requests correctly afterward, until manually killed with `taskkill`. The
  most likely explanation (not exhaustively proven) is that `_fatal()`'s
  `ctypes.windll.user32.MessageBoxW(...)` call — a blocking modal dialog —
  never returns in this launch context (no interactive desktop session to
  dismiss it), so the `sys.exit(1)` line immediately after it is never
  reached. This is a second, independent fragility in the same failure
  path, addressed in Step 5 of the fix (see
  `root-cause-analysis.md` and `launcher-readiness-design.md`).

## Clinic — `dist/AuraClinic/AuraClinic.exe`

Identical pattern, independently reproduced:

- Selected port: `5000`.
- Server bind address: `127.0.0.1:5000`.
- Readiness URL used internally: `http://127.0.0.1:5000`.
- Proxy environment: `HTTP_PROXY=None HTTPS_PROXY=None NO_PROXY=None`.
- `getaddrinfo('127.0.0.1')`: single `AF_INET` entry, no ambiguity.
- Internal readiness attempts: **97 attempts over 45.27 seconds — every
  single one raised `HTTPError code=404 reason='NOT FOUND'`.**
- External verification: `GET /api/onboarding/status` returned `200`
  throughout, via a separate `curl` process.
- `_fatal('The Aura Clinic server did not start in time.')` was invoked
  after the same 45 s deadline.
- Same secondary observation: the process remained alive and reachable
  after `_fatal()` logged its error, until manually killed.

## Conclusion of this step

The failure is **100% deterministic**, not intermittent, not proxy
-related, not IPv6-related, and not a threading race: every single
readiness attempt in both 45-second windows (204 attempts total across
both products) failed with the identical `HTTPError 404`, while the actual
API was demonstrably reachable and correct the entire time via an
independent client. This rules out the environmental/networking
hypotheses as the primary cause and points directly at the readiness
check's own target URL. See `root-cause-analysis.md` for the confirmed
root cause and the evidence that closes it out.

## Cleanup

The temporary diagnostic logging described above was removed from both
launcher files once the root cause was confirmed (see the fix commit) —
it is not present in the corrected, shipped launcher code. The throwaway
`AURA_APP_DATA` directories and log files used for this reproduction were
deleted after this document was written. No synthetic data was committed.

**Security note (found by automated review of the reproduction commit)**:
the temporary diagnostic instrumentation logged the raw *values* of
`HTTP_PROXY`/`HTTPS_PROXY`/`NO_PROXY` to `startup.log`, not just whether
they were set. In this environment all three were `None`, so no credential
was actually captured -- but a real proxy URL can embed a username/password
(`http://user:pass@proxy:8080`), and logging that verbatim to a file would
be an information-disclosure bug on a customer machine with such a proxy
configured. The permanent, corrected launcher code (see
`launcher-readiness-design.md`) does not log proxy environment variables
at all, in either value or presence form -- this pattern is called out
here so it is not reintroduced by a future diagnostic pass.
